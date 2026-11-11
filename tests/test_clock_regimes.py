"""The clock estimator on a known answer, its refusal of unidentified designs, and the published
within-regime fits (D-08b, D-22) reproduced from ``src.clock.regimes.WINDOWS``."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import polars as pl
import pytest

from src.clock.estimate import WEEKDAYS, ClockFit, _expiry_days, fit
from src.clock.regimes import WINDOWS, _fit_rows
from src.config import Settings

TRUE_WEIGHTS = {"Mon": 1.4, "Tue": 0.6, "Wed": 1.1, "Thu": 0.9, "Fri": 1.0}


def _synthetic(tmp_path, maturities_per_date: int, noise: float = 0.0) -> tuple[Settings, dict]:
    """Weekday sessions, Thursday weeklies, and quotes generated from TRUE_WEIGHTS times a random
    per-date scale: exactly the model ``fit`` assumes. Also returns the true weights rescaled to
    mean one over the sessions the quotes span, which is what ``fit`` reports (D-01)."""
    cfg = Settings(data_root=tmp_path / "data", results_root=tmp_path / "results")
    days = [dt.date(2025, 1, 6) + dt.timedelta(d) for d in range(200)]
    sessions = [d for d in days if d.weekday() < 5]
    thursdays = [d for d in sessions if d.weekday() == 3]
    for path in (cfg.raw_dir, cfg.reference_dir, cfg.tables_dir, cfg.panel_dir):
        path.mkdir(parents=True)
    (cfg.raw_dir / "calendar.json").write_text(
        json.dumps({"trading_days": [d.isoformat() for d in sessions]}), encoding="utf-8"
    )
    (cfg.reference_dir / "macro_events.json").write_text(
        json.dumps({"union_budget": [], "rbi_mpc": []}), encoding="utf-8"
    )
    panel = {"symbol": "SYN", "instr": "IDO", "trade_date": thursdays, "expiry": thursdays}
    pl.DataFrame(panel).write_parquet(cfg.panel_dir / "fo_2025.parquet")

    rng = np.random.default_rng(1)
    w = np.array([TRUE_WEIGHTS[WEEKDAYS[d.weekday()]] for d in sessions])
    idx = {d: i for i, d in enumerate(sessions)}
    rows, touched = [], set()
    for t in sessions:
        scale = rng.lognormal(0.0, 0.3)
        for T in [e for e in thursdays if idx[e] - idx[t] >= 2][:maturities_per_date]:
            touched.update(range(idx[t] + 1, idx[T] + 1))
            var = scale * w[idx[t] + 1 : idx[T] + 1].sum() * np.exp(noise * rng.standard_normal())
            rows.append({"trade_date": t, "symbol": "SYN", "expiry": T, "tenor": "weekly",
                         "dte_trd": idx[T] - idx[t], "total_var_corr": var})
    pl.DataFrame(rows).write_parquet(cfg.tables_dir / "atm_variance.parquet")
    mean_w = w[sorted(touched)].mean()
    return cfg, {d: v / mean_w for d, v in TRUE_WEIGHTS.items()}


def test_recovers_known_clock_and_keeps_its_draws(tmp_path):
    cfg, expected = _synthetic(tmp_path, maturities_per_date=4)
    f = fit(cfg, "SYN", window=None, dummies=(), n_boot=20, seed=0)
    for d in WEEKDAYS:
        assert f.weights[d] == pytest.approx(expected[d], rel=1e-6)
    assert f.rmse < 1e-8
    obs = f.observations
    assert obs.height == f.n_obs
    assert np.allclose(obs["observed"], obs["fitted"], atol=1e-8)
    # Noise-free data: every resample recovers the truth, and the draws are what the CI is made of.
    assert len(f.boot_draws) == 20
    for d in WEEKDAYS:
        vals = np.array([draw[d] for draw in f.boot_draws])
        assert np.allclose(vals, expected[d], rtol=1e-5)
        assert f.boot[d] == pytest.approx((np.percentile(vals, 2.5), np.percentile(vals, 97.5)))


def test_noisy_clock_interval_covers_truth(tmp_path):
    cfg, expected = _synthetic(tmp_path, maturities_per_date=4, noise=0.05)
    f = fit(cfg, "SYN", window=None, dummies=(), n_boot=100, seed=0)
    for d in WEEKDAYS:
        lo, hi = f.boot[d]
        assert lo < expected[d] < hi
        assert hi - lo > 1e-3  # a real interval, not the zero-width artefact


def test_refuses_one_maturity_per_date(tmp_path):
    # BANKEX's shape: one quote per date demeans to zero, nothing is identified.
    cfg, _ = _synthetic(tmp_path, maturities_per_date=1)
    with pytest.raises(ValueError, match="refusing to fit"):
        fit(cfg, "SYN", window=None, dummies=(), n_boot=10)


def test_expiry_days_ignore_redated_contracts(tmp_path):
    panel = tmp_path / "panel"
    panel.mkdir()
    redated, real = dt.date(2025, 9, 25), dt.date(2025, 9, 30)
    pl.DataFrame(
        {
            "symbol": ["X", "X", "X"],
            "instr": ["IDO"] * 3,
            "trade_date": [dt.date(2025, 7, 31), dt.date(2025, 8, 1), real],
            "expiry": [redated, real, real],
        }
    ).write_parquet(panel / "fo_2025.parquet")
    assert _expiry_days(str(panel), "X") == frozenset({real})


def test_flag_names_the_parameter_and_spares_sound_weekdays():
    # NIFTY Thu h1's shape: weekday weights fine, weekend multiplier run to zero, cond exploded.
    weights = {"Mon": 1.2, "Tue": 1.0, "Wed": 0.9, "Thu": 0.7, "Fri": 1.2, "weekend": 1e-10}
    f = ClockFit(params={}, weights=weights, combined={}, dropped=[], n_obs=100, n_groups=40,
                 rmse=0.1, condition_number=3e10, vif={})
    rows = _fit_rows(WINDOWS[1], "weekday", f)
    assert {r["identified"] for r in rows} == {False}
    assert {r["weekdays_in_range"] for r in rows} == {True}
    assert "weekend 1e-10 outside" in rows[0]["flag"] and "cond 3e+10" in rows[0]["flag"]
    f.weights = {**weights, "Fri": 20.0}
    assert {r["weekdays_in_range"] for r in _fit_rows(WINDOWS[1], "weekday", f)} == {False}


#: (symbol, label, half, own weekday, point, lo, hi, decimals of the published interval).
PUBLISHED = [
    ("NIFTY", "Thu regime", "full", "Thu", 0.699, 0.483, 0.889, 3),
    ("NIFTY", "Thu regime", "full", "Fri", 1.299, 1.058, 1.477, 3),
    ("NIFTY", "Tue regime", "full", "Tue", 0.607, 0.298, 0.844, 3),
    ("NIFTY", "Tue regime", "full", "Mon", 1.459, 1.163, 1.780, 3),
    ("NIFTY", "Thu regime", "h1", "Thu", 0.675, 0.45, 0.87, 2),
    ("NIFTY", "Thu regime", "h2", "Thu", 0.484, 0.14, 0.91, 2),
    ("NIFTY", "Tue regime", "h1", "Tue", 0.549, 0.12, 0.80, 2),
    ("NIFTY", "Tue regime", "h2", "Tue", 0.498, 0.06, 1.16, 2),
    ("SENSEX", "Fri regime", "full", "Fri", 1.409, 1.077, 1.693, 3),
    ("SENSEX", "Fri regime", "full", "Thu", 0.872, 0.560, 1.235, 3),
    ("SENSEX", "Tue regime", "full", "Tue", 1.692, 1.170, 2.071, 3),
    ("SENSEX", "Tue regime", "full", "Thu", 0.932, 0.595, 1.205, 3),
    ("SENSEX", "Thu regime", "full", "Thu", 0.946, 0.758, 1.171, 3),
    ("SENSEX", "Thu regime", "full", "Tue", 0.926, 0.707, 1.094, 3),
    ("SENSEX", "Fri regime", "h1", "Fri", 1.137, 0.75, 2.27, 2),
    ("SENSEX", "Fri regime", "h2", "Fri", 1.413, 1.05, 1.77, 2),
    ("SENSEX", "Tue regime", "h1", "Tue", 1.897, None, None, 2),
    ("SENSEX", "Tue regime", "h2", "Tue", 0.157, None, None, 2),
    ("SENSEX", "Thu regime", "h1", "Thu", 0.842, 0.58, 1.04, 2),
    ("SENSEX", "Thu regime", "h2", "Thu", 1.001, 0.80, 1.33, 2),
]
#: Published condition numbers (D-08b, D-22).
PUBLISHED_COND = {
    ("NIFTY", "Thu regime", "full"): 9.4, ("NIFTY", "Tue regime", "full"): 4.0,
    ("SENSEX", "Fri regime", "full"): 9.6, ("SENSEX", "Tue regime", "full"): 26.8,
    ("SENSEX", "Thu regime", "full"): 5.3, ("SENSEX", "Tue regime", "h1"): 42.7,
    ("SENSEX", "Tue regime", "h2"): 31.7,
}


@pytest.fixture(scope="module")
def published_fits():
    keys = {(s, lab, h) for s, lab, h, *_ in PUBLISHED}
    return {
        (w.symbol, w.label, w.half): fit(
            symbol=w.symbol, window=(w.start, w.end), n_boot=w.n_boot, seed=0, dummies=("weekend",)
        )
        for w in WINDOWS
        if (w.symbol, w.label, w.half) in keys
    }


@pytest.mark.slow
@pytest.mark.parametrize("symbol,label,half,day,point,lo,hi,dp", PUBLISHED)
def test_reproduces_published_weight(published_fits, symbol, label, half, day, point, lo, hi, dp):
    f = published_fits[(symbol, label, half)]
    assert round(f.weights[day], 3) == point
    if lo is not None:
        assert (round(f.boot[day][0], dp), round(f.boot[day][1], dp)) == (lo, hi)


@pytest.mark.slow
@pytest.mark.parametrize("key,cond", PUBLISHED_COND.items())
def test_reproduces_published_condition(published_fits, key, cond):
    assert round(published_fits[key].condition_number, 1) == cond

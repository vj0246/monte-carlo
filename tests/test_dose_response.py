"""Known-answer checks for the dose-response arithmetic (src.analysis.dose_response).

Synthetic two-unit data with a planted effect: the treated unit's Wednesday carries 0.6 of the
variance of every other session before the event and 1.0 after; the control is flat throughout.
Under mean-one normalisation the population DiD is exactly ln((4 + 0.6) / 5 / 0.6) = +0.427,
not -ln 0.6, because suppressing one weekday also lowers the mean it is normalised by.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from src.analysis.dose_response import (
    BLACKOUT,
    INDEX,
    WINDOWS,
    _ci,
    _inside,
    bootstrap,
    did,
    disjoint_designs,
    log_weights,
    roles,
    shift,
    week_sums,
)
from src.config import settings

MONDAY = dt.date(2024, 1, 1)
WED = 2
TRUE_DID = float(np.log((4 + 0.6) / 5 / 0.6))


def _weekdays(n_weeks: int) -> list[dt.date]:
    return [MONDAY + dt.timedelta(weeks=w, days=d) for w in range(n_weeks) for d in range(5)]


def _z2(n_weeks: int, wed_mult: float, rng: np.random.Generator, rho: float = 0.7) -> np.ndarray:
    """Squared correlated Gaussian returns for (treated, control); treated Wednesday scaled."""
    n = 5 * n_weeks
    common = rng.standard_normal(n)
    eps = rho * common + np.sqrt(1 - rho**2) * rng.standard_normal((2, n))
    scale = np.ones((2, n))
    scale[0, WED::5] = wed_mult
    return scale * eps**2


def _sample(n_weeks: int, rng: np.random.Generator) -> tuple:
    dates = _weekdays(n_weeks)
    window = (dates[0], dates[-1])
    pre = week_sums(_z2(n_weeks, 0.6, rng), dates, window, set())
    post = week_sums(_z2(n_weeks, 1.0, rng), dates, window, set())
    return pre, post


def test_week_sums_conserve_and_exclude():
    dates = _weekdays(3)
    z2 = np.arange(2 * 15, dtype=float).reshape(2, 15)
    sums, counts = week_sums(z2, dates, (dates[0], dates[-1]), exclude={dates[7]})
    assert sums.shape == (2, 3, 5) and counts.sum() == 14
    assert np.allclose(sums.sum(axis=(1, 2)), z2.sum(axis=1) - z2[:, 7])


def test_weights_average_one_over_sessions():
    pre, _ = _sample(30, np.random.default_rng(1))
    w = np.exp(log_weights(*pre))
    per_weekday = pre[1].sum(axis=0)
    assert np.allclose((w * per_weekday).sum(axis=1) / per_weekday.sum(), 1.0)


def test_did_recovers_planted_effect():
    pre, post = _sample(20_000, np.random.default_rng(2))
    est = float(did(log_weights(*pre), log_weights(*post), 0, 1, WED))
    # SE here is about 0.02; -ln 0.6 = 0.511, the un-normalised answer, is four SE away.
    assert abs(est - TRUE_DID) < 0.05
    # An untreated weekday moves by the normalisation alone: -ln(5 / 4.6) = -0.083.
    monday = float(did(log_weights(*pre), log_weights(*post), 0, 1, 0))
    assert abs(monday + np.log(5 / 4.6)) < 0.05


def test_bootstrap_sd_matches_monte_carlo_sd():
    """An independent method: the sampling sd over fresh datasets against the week-block bootstrap
    sd averaged over a few datasets. 26 weeks, the size of one real window."""
    rng = np.random.default_rng(3)
    mc = [float(did(log_weights(*a), log_weights(*b), 0, 1, WED)) for a, b in (_sample(26, rng) for _ in range(800))]
    boot_sd = []
    for _ in range(20):
        pre, post = _sample(26, rng)
        a, b = bootstrap(pre, post, 400, rng)
        boot_sd.append(float(did(a, b, 0, 1, WED).std()))
    assert abs(np.mean(boot_sd) / np.std(mc) - 1) < 0.15


def test_shift_and_ratio_signs():
    pre = np.log(np.array([[[1.0, 1.0, 1.0, 0.5, 1.0], [1.0, 1.0, 1.0, 0.9, 1.0]]]))[0]
    post = np.log(np.array([[[1.0, 0.5, 1.0, 1.0, 1.0], [1.0, 0.9, 1.0, 1.0, 1.0]]]))[0]
    s_high, s_low = shift(pre, post, 0, 3, 1), shift(pre, post, 1, 3, 1)
    assert np.isclose(s_high, 2 * np.log(0.5)) and np.isclose(s_low, 2 * np.log(0.9))
    assert 0 < s_low / s_high < 1


def test_roles_follow_the_event_not_the_symbol():
    dates = _weekdays(20)
    pre, post = (dates[0], dates[49]), (dates[50], dates[-1])
    wed = [d for d in dates if d.weekday() == 2]
    thu = [d for d in dates if d.weekday() == 3]
    expiries = {
        "A": [d for d in wed if d <= pre[1]] + [d for d in thu if d >= post[0]][::4],
        "B": thu,
    }
    rl = roles(expiries, dates, pre, post)
    assert rl["A"]["role"] == "treated" and (rl["A"]["old"], rl["A"]["new"]) == ("Wed", "Thu")
    assert rl["B"]["role"] == "control" and rl["B"]["dose_pre"] == 1.0
    assert 0.2 <= rl["A"]["dose_post"] <= 0.3


def test_disjoint_designs_counts_non_overlapping_windows():
    # Designs every 5 sessions over 0..300 with h = 63 span 126 sessions each: taus 0, 130, 260.
    assert disjoint_designs(list(range(0, 301, 5)), 63) == 3
    assert disjoint_designs([], 63) == 0
    # A gap (the blackout) restarts the count at the first tau after it.
    assert disjoint_designs([0, 5, 10, 400, 405], 63) == 2


def test_ci_counts_the_non_finite_draws_it_leaves_out():
    x = np.concatenate([np.linspace(0.0, 1.0, 101), [np.inf, -np.inf, np.nan]])
    out = _ci(x)
    assert out["boot_nonfinite"] == 3
    assert np.isclose(out["ci_lo"], 0.025) and np.isclose(out["ci_hi"], 0.975)


def _lot_sets(symbol: str) -> dict[dt.date, frozenset]:
    files = sorted(settings.panel_dir.glob("fo_*.parquet"))
    if not files:
        pytest.skip("no option panel")
    panel = pl.concat([pl.scan_parquet(f) for f in files], how="vertical_relaxed")
    lots = (panel.filter((pl.col("symbol") == symbol) & (pl.col("instr") == "IDO"))
            .group_by("trade_date").agg(pl.col("lot_size").unique()).collect())
    return {d: frozenset(v) for d, v in lots.iter_rows()}


def test_window_cut_offs_match_the_measured_lot_staircase():
    """The WINDOWS dates are not from memory: every window has one lot size per unit except the
    ones the module comment names, and the D-10 blackout ends the day both overlaps resolve."""
    lots = {s: _lot_sets(s) for s in INDEX}
    mixed = set()
    for event, variant, pre, post in WINDOWS:
        for side, window in (("pre", pre), ("post", post)):
            for symbol, by_day in lots.items():
                if len({v for d, v in by_day.items() if _inside(d, window)}) > 1:
                    mixed.add((event, variant, side, symbol))
    assert mixed == {
        ("nov2024", "full", "post", "BANKNIFTY"),
        ("sep2025", "full", "pre", "BANKNIFTY"),
        ("sep2025", "pre_lot_change", "pre", "BANKNIFTY"),
        ("sep2025", "full", "post", "BANKNIFTY"),
        ("sep2025", "full", "post", "NIFTY"),
    }
    last_overlap = BLACKOUT[1] - dt.timedelta(days=1)
    for by_day in lots.values():
        assert len(by_day[last_overlap]) == 2 and len(by_day[BLACKOUT[1]]) == 1

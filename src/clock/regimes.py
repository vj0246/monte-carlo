"""Every within-regime clock fit, persisted with its bootstrap draws (D-08b, D-22, D-07).

The regime, half-split and Sensex numbers in D-08b and D-22 were logged and the bootstrap draws
discarded, so nothing downstream could propagate clock uncertainty or redraw a figure. This module
reruns those fits and writes them out:

* ``clock_fits.parquet``: one row per (fit, parameter): point, 95% bootstrap interval, condition
  number, VIF, the share of draws outside [0.1, 10], and an ``identified`` flag. A fit is flagged
  when cond > 30 or any multiplier, weekend and macro included, leaves [0.1, 10] (a parameter
  running to a boundary); ``flag`` names what tripped it. ``weekdays_in_range`` says whether the
  five weekday weights themselves stay inside, because a weekend multiplier running to zero
  inflates the condition number of a fit whose weekday weights are sound. A refused fit is a row
  of nulls with the reason, never a dropped row.
* ``clock_boot.parquet``: every normalised bootstrap draw.
* ``clock_obs.parquet``: the two NIFTY regimes observation by observation, demeaned observed and
  fitted log total variance.

**Window conventions are the ones that reproduce the published numbers**, found by rerunning, not
chosen here, and recorded per row in ``split_rule``. They are reproduction conventions, not a rule,
so the half table is not evidence of stability: full regimes take 300 draws, halves 100, seed 0 throughout. The NIFTY Thursday
"halves" are the two sides of the Nov-2024 blackout (D-10). NIFTY and Sensex after Sep-2025 split
at the end of the D-10 post-window, 2026-02-28. Sensex Tuesday splits at its calendar midpoint.
The Sensex Friday split, 2024-06-18 (sharing that date), was located by scanning split dates and
has no derivation; its second-half point ranges 1.398 to 1.426 over neighbouring dates.

Every fit runs twice: weekday-only (``dummies=("weekend",)``, the published specification) and
with the RBI MPC / Budget dummy, the D-07 contaminant check.

BANKNIFTY weekdays are measured from the ``atm_variance`` expiries (no timeline file exists):
weekly and monthly Wednesday to 2024-11-13, monthly Thursday 2025-01-30 to 2025-08-28, monthly
Tuesday from 2025-09-30. BANKEX from ``regime_timeline_bankex.json``. Both reuse the Sensex /
NIFTY window edges, so the Nov-2024 blackout is excluded.

Run:  uv run python -m src.clock.regimes
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
import polars as pl

from src.clock.estimate import WEEKDAYS, ClockFit, fit
from src.config import Settings, settings
from src.provenance import manifest, write_manifest

log = logging.getLogger(__name__)

D = dt.date
SEED = 0
FULL_DRAWS = 300
HALF_DRAWS = 100
SPECS = {"weekday": ("weekend",), "macro": ("weekend", "macro_event")}
MAX_CONDITION = 30.0
WEIGHT_RANGE = (0.1, 10.0)
EXTRA_PARAMS = ("weekend", "macro_event")


@dataclass(frozen=True)
class Window:
    symbol: str
    label: str
    half: str  # "full", "h1" or "h2"
    start: dt.date
    end: dt.date
    split_rule: str = ""

    @property
    def n_boot(self) -> int:
        return FULL_DRAWS if self.half == "full" else HALF_DRAWS


BLACKOUT_SIDES = "two sides of the D-10 blackout"
POST_WINDOW_END = "end of the D-10 post-window"
MIDPOINT = "calendar midpoint"
SCANNED = "scanned to reproduce D-22; no derivation"


def _regime(symbol: str, label: str, start: D, end: D, split: tuple[D, D] | None = None,
            rule: str = ""):
    """The full window and, given ``split = (end of h1, start of h2)``, its two halves."""
    out = [Window(symbol, label, "full", start, end)]
    if split:
        out += [Window(symbol, label, "h1", start, split[0], rule),
                Window(symbol, label, "h2", split[1], end, rule)]
    return out


WINDOWS = (
    _regime("NIFTY", "Thu regime", D(2024, 1, 2), D(2025, 8, 28),
            (D(2024, 11, 12), D(2025, 2, 3)), BLACKOUT_SIDES)
    + _regime("NIFTY", "Tue regime", D(2025, 9, 2), D(2026, 9, 4),
              (D(2026, 2, 28), D(2026, 3, 1)), POST_WINDOW_END)
    + _regime("SENSEX", "Fri regime", D(2024, 1, 2), D(2024, 11, 12), (D(2024, 6, 18),) * 2, SCANNED)
    + _regime("SENSEX", "Tue regime", D(2025, 2, 3), D(2025, 8, 29),
              (D(2025, 5, 17), D(2025, 5, 18)), MIDPOINT)
    + _regime("SENSEX", "Thu regime", D(2025, 9, 2), D(2026, 9, 4),
              (D(2026, 2, 28), D(2026, 3, 1)), POST_WINDOW_END)
    + _regime("BANKNIFTY", "Wed regime", D(2024, 1, 2), D(2024, 11, 12))
    + _regime("BANKNIFTY", "Thu regime", D(2025, 2, 3), D(2025, 8, 28))
    + _regime("BANKNIFTY", "Tue regime", D(2025, 9, 2), D(2026, 9, 4))
    + _regime("BANKEX", "Mon regime", D(2024, 1, 2), D(2024, 11, 12))
    + _regime("BANKEX", "Tue regime", D(2025, 2, 3), D(2025, 8, 29))
    + _regime("BANKEX", "Thu regime", D(2025, 9, 2), D(2026, 9, 4))
)


def _out_of_range(f: ClockFit) -> dict[str, float]:
    lo, hi = WEIGHT_RANGE
    return {k: v for k, v in f.weights.items() if not lo <= v <= hi}


def _flag(f: ClockFit) -> str:
    """Why a fit is not identified, or "" if it is."""
    reasons = []
    if not f.condition_number <= MAX_CONDITION:
        reasons.append(f"cond {f.condition_number:.3g} > {MAX_CONDITION:g}")
    reasons += [f"{k} {v:.3g} outside [0.1, 10]" for k, v in _out_of_range(f).items()]
    return "; ".join(reasons)


def _fit_rows(w: Window, spec: str, f: ClockFit | None, refusal: str = "") -> list[dict]:
    base = {"symbol": w.symbol, "label": w.label, "start": w.start, "end": w.end, "half": w.half,
            "split_rule": w.split_rule, "spec": spec, "n_boot": w.n_boot}
    if f is None:
        refused = {"identified": False, "weekdays_in_range": False, "flag": f"refused: {refusal}"}
        return [{**base, "parameter": p, **refused} for p in WEEKDAYS]
    flag = _flag(f)
    weekdays_ok = not set(_out_of_range(f)) & set(WEEKDAYS)
    lo, hi = WEIGHT_RANGE
    return [
        {
            **base,
            "parameter": name,
            "point": value,
            "lo": f.boot.get(name, (None, None))[0],
            "hi": f.boot.get(name, (None, None))[1],
            "vif": f.vif.get(name),  # Monday is the pinned reference and has none
            "condition_number": f.condition_number,
            "n_obs": f.n_obs,
            "n_dates": f.n_groups,
            "rmse": f.rmse,
            "n_draws": len(f.boot_draws),
            # A finite point with a run-away tail: the interval is then partly a boundary artefact.
            "boundary_share": float(np.mean([not lo <= d[name] <= hi for d in f.boot_draws]))
            if f.boot_draws else None,
            "dropped": ",".join(f.dropped),
            "identified": not flag,
            "weekdays_in_range": weekdays_ok,
            "flag": flag,
        }
        for name, value in f.weights.items()
    ]


def _boot_rows(w: Window, spec: str, f: ClockFit) -> list[dict]:
    names = WEEKDAYS + EXTRA_PARAMS
    return [
        {"symbol": w.symbol, "label": w.label, "half": w.half, "spec": spec, "draw": i}
        | {n: draw.get(n) for n in names}
        for i, draw in enumerate(f.boot_draws)
    ]


def _obs_rows(w: Window, f: ClockFit) -> pl.DataFrame:
    assert f.observations is not None
    return f.observations.select(
        pl.lit(w.symbol).alias("symbol"),
        pl.lit(w.label).alias("label"),
        "trade_date",
        "expiry",
        pl.col("trade_date").dt.strftime("%a").alias("open_weekday"),
        "sessions",
        pl.col("observed").alias("observed_dm"),
        pl.col("fitted").alias("fitted_dm"),
    )


def run(cfg: Settings = settings) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    fit_rows: list[dict] = []
    boot_rows: list[dict] = []
    obs: list[pl.DataFrame] = []
    for w in WINDOWS:
        for spec, dummies in SPECS.items():
            try:
                f = fit(cfg, w.symbol, "total_var_corr", window=(w.start, w.end),
                        n_boot=w.n_boot, seed=SEED, dummies=dummies)
            except ValueError as err:
                log.info("%-9s %-10s %-4s %-7s refused: %s", w.symbol, w.label, w.half, spec, err)
                fit_rows += _fit_rows(w, spec, None, str(err))
                continue
            if len(f.boot_draws) < w.n_boot:  # fit() drops a draw whose least squares fails
                log.warning("%s %s %s %s: %d of %d bootstrap draws converged", w.symbol, w.label,
                            w.half, spec, len(f.boot_draws), w.n_boot)
            fit_rows += _fit_rows(w, spec, f)
            boot_rows += _boot_rows(w, spec, f)
            if w.symbol == "NIFTY" and w.half == "full" and spec == "weekday":
                obs.append(_obs_rows(w, f))
            log.info(
                "%-9s %-10s %-4s %-7s cond %8.3g  %s%s",
                w.symbol, w.label, w.half, spec, f.condition_number,
                "  ".join(f"{k} {v:.3f}" for k, v in f.weights.items()),
                f"  [{_flag(f)}]" if _flag(f) else "",
            )
    schema = {"n_boot": pl.Int64, "point": pl.Float64, "lo": pl.Float64, "hi": pl.Float64, "vif": pl.Float64,
              "condition_number": pl.Float64, "n_obs": pl.Int64, "n_dates": pl.Int64,
              "rmse": pl.Float64, "n_draws": pl.Int64, "boundary_share": pl.Float64,
              "dropped": pl.String}
    fits = pl.DataFrame(fit_rows, schema_overrides=schema, infer_schema_length=None)
    boots = pl.DataFrame(boot_rows, schema_overrides={n: pl.Float64 for n in EXTRA_PARAMS},
                         infer_schema_length=None)
    return fits, boots, pl.concat(obs)


def macro_shift(fits: pl.DataFrame) -> pl.DataFrame:
    """How far each weekday weight moves when the macro dummy is added (D-07), full windows."""
    full = fits.filter((pl.col("half") == "full") & pl.col("point").is_not_null())
    keys = ["symbol", "label", "parameter"]
    wd = full.filter(pl.col("spec") == "weekday").select(*keys, pl.col("point").alias("weekday"))
    mac = full.filter(pl.col("spec") == "macro").select(*keys, pl.col("point").alias("macro"))
    return wd.join(mac, on=keys).with_columns(
        (pl.col("macro") / pl.col("weekday") - 1).alias("rel_change")
    )


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fits, boots, obs = run(cfg)
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    inputs = (
        cfg.tables_dir / "atm_variance.parquet",
        cfg.raw_dir / "calendar.json",
        cfg.reference_dir / "macro_events.json",
    )
    windows = [
        {"symbol": w.symbol, "label": w.label, "half": w.half, "start": w.start.isoformat(),
         "end": w.end.isoformat(), "n_boot": w.n_boot}
        for w in WINDOWS
    ]
    for name, table, what in (
        ("clock_fits", fits, "within-regime clock fits"),
        ("clock_boot", boots, "clock bootstrap draws"),
        ("clock_obs", obs, "NIFTY regime observations, demeaned observed and fitted"),
    ):
        path = cfg.tables_dir / f"{name}.parquet"
        table.write_parquet(path)
        write_manifest(path, manifest(what, cfg, seed=SEED, inputs=inputs, extra={
            "windows": windows, "specs": {k: list(v) for k, v in SPECS.items()},
            "variance_col": "total_var_corr", "max_condition": MAX_CONDITION,
            "weight_range": list(WEIGHT_RANGE),
        }))
        log.info("wrote %s (%d rows)", path, table.height)

    log.info("\nD-07 check: weekday weight with the macro dummy against without (full windows)")
    for r in macro_shift(fits).filter(pl.col("parameter").is_in(WEEKDAYS)).iter_rows(named=True):
        log.info("%-9s %-10s %s  %.3f -> %.3f  (%+.1f%%)", r["symbol"], r["label"], r["parameter"],
                 r["weekday"], r["macro"], 100 * r["rel_change"])
    m = fits.filter((pl.col("parameter") == "macro_event") & (pl.col("half") == "full"))
    for r in m.iter_rows(named=True):
        log.info("%-9s %-10s macro x%.3f [%.3f, %.3f]  VIF %.1f  cond %.3g", r["symbol"],
                 r["label"], r["point"], r["lo"], r["hi"], r["vif"], r["condition_number"])


if __name__ == "__main__":
    main()

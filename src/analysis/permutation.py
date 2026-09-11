"""Randomization inference for the Sep-2025 event (D-11).

The question is not "is the effect significant against its own sample" -- the bootstrap already
says yes -- but "how unusual is this effect among all the dates it could have been measured at?".
So the regime-change statistic is recomputed at every eligible pseudo-event date and the true date
is ranked against that distribution.

Statistic, pre-registered by the hypothesis rather than chosen after looking:

    S(tau) = [ln w_Tue(post) - ln w_Tue(pre)] - [ln w_Thu(post) - ln w_Thu(pre)]

The rule moved expiry from Thursday to Tuesday and expiry sessions carry less variance, so Tuesday
should fall and Thursday should rise: S is predicted negative at the true date and near zero at
dates inside one regime. One-sided p-value: share of pseudo dates with S at least as negative.

Computed on two channels that share no data:

* options channel -- weekday weights from ``src.clock.estimate``, within-regime specification;
* stock channel -- mean squared single-stock-future return by weekday. No model fit involved.

Eligibility. A pseudo window [tau-h, tau+h) must lie inside the sample, must not contain the real
Sep-2025 boundary, and must not overlap the Nov-2024 blackout (2024-11-13, the last Bank Nifty
weekly, to 2025-01-31, the end of the lot-size overlap; D-10). Windows are counted in trading
sessions, never calendar days.

**Degenerate fits.** On three-month windows a quarter of the options-channel fits are numerically
broken: weekday weights outside [0.1, 10] and condition numbers up to 1e9. Those are failed
estimates, not draws from the null, and they put log-weight differences of +/-200 into the null
distribution. A sensitivity row drops any date whose fit has condition number above ``MAX_COND``
or any weekday weight outside [0.1, 10]. That rule was written *after* the raw test had already
triggered the D-11 kill condition, so it is reported beside the raw result and never instead of it.

Honest limit: adjacent pseudo dates share almost all their data. The number of *independent*
pseudo windows is roughly the eligible span divided by the window length -- about five at h = 63.
No p-value from this design can be much smaller than one in six, however the ranks fall.

Run:  uv run python -m src.analysis.permutation
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import polars as pl

from src.analysis.expiry_effect import stock_returns
from src.clock.estimate import WEEKDAYS, fit
from src.config import Settings, settings

log = logging.getLogger(__name__)

EVENT = dt.date(2025, 9, 1)
BLACKOUT = (dt.date(2024, 11, 13), dt.date(2025, 1, 31))
STEP = 5  # one candidate per trading week; finer steps only add near-duplicate windows
MAX_COND = 30.0
MAX_ABS_LOGW = float(np.log(10.0))


def _sessions(cfg: Settings) -> list[dt.date]:
    cal = json.loads((cfg.raw_dir / "calendar.json").read_text(encoding="utf-8"))
    return [dt.date.fromisoformat(d) for d in cal["trading_days"]]


def _eligible(sessions: list[dt.date], h: int) -> list[int]:
    split = next(i for i, d in enumerate(sessions) if d >= EVENT)
    out = []
    for tau in range(h, len(sessions) - h + 1, STEP):
        lo, hi = sessions[tau - h], sessions[tau + h - 1]
        crosses_event = tau - h < split < tau + h
        hits_blackout = lo <= BLACKOUT[1] and hi >= BLACKOUT[0]
        if not crosses_event and not hits_blackout:
            out.append(tau)
    return out


def _options_stat(cfg: Settings, pre: tuple, post: tuple) -> tuple[float, float, float]:
    """S, the worse condition number of the two fits, and the largest |ln weight| in either."""
    a = fit(cfg, "NIFTY", "total_var_corr", window=pre, dummies=("weekend",))
    b = fit(cfg, "NIFTY", "total_var_corr", window=post, dummies=("weekend",))
    s = float(np.log(b.weights["Tue"] / a.weights["Tue"]) - np.log(b.weights["Thu"] / a.weights["Thu"]))
    extreme = max(abs(np.log(f.weights[d])) for f in (a, b) for d in WEEKDAYS)
    return s, max(a.condition_number, b.condition_number), float(extreme)


def _stock_stat(rets: pl.DataFrame, pre: tuple, post: tuple) -> float:
    def by_weekday(window: tuple) -> dict[str, float]:
        sub = rets.filter((pl.col("trade_date") >= window[0]) & (pl.col("trade_date") <= window[1]))
        grouped = sub.group_by(pl.col("trade_date").dt.strftime("%a").alias("wd")).agg(
            pl.col("sq").mean()
        )
        return dict(zip(grouped["wd"].to_list(), grouped["sq"].to_list()))

    a, b = by_weekday(pre), by_weekday(post)
    return float(np.log(b["Tue"] / a["Tue"]) - np.log(b["Thu"] / a["Thu"]))


def run(cfg: Settings = settings, h: int = 63) -> pl.DataFrame:
    sessions = _sessions(cfg)
    rets = stock_returns(cfg)
    split = next(i for i, d in enumerate(sessions) if d >= EVENT)

    rows = []
    for tau in [split, *_eligible(sessions, h)]:
        pre = (sessions[tau - h], sessions[tau - 1])
        post = (sessions[tau], sessions[tau + h - 1])
        try:
            s_opt, cond, extreme = _options_stat(cfg, pre, post)
        except ValueError:  # too few observations in a window; the date is not usable
            s_opt, cond, extreme = float("nan"), float("inf"), float("inf")
        rows.append(
            {
                "tau": sessions[tau],
                "is_event": tau == split,
                "s_options": s_opt,
                "cond": cond,
                "max_abs_logw": extreme,
                "s_stocks": _stock_stat(rets, pre, post) if rets is not None else float("nan"),
            }
        )
    return pl.DataFrame(rows)


def summarise(df: pl.DataFrame, h: int) -> None:
    healthy = (pl.col("cond") <= MAX_COND) & (pl.col("max_abs_logw") <= MAX_ABS_LOGW)
    log.info("--- randomization inference, h = %d sessions ---", h)
    for col, label, frame in (
        ("s_options", "options, all fits", df),
        ("s_options", "options, degenerate excluded", df.filter(healthy)),
        ("s_stocks", "stocks", df),
    ):
        event = frame.filter(pl.col("is_event"))
        if event.is_empty():
            log.info("%-30s true event itself fails the conditioning rule; not evaluable", label)
            continue
        star = event[col][0]
        null = frame.filter(~pl.col("is_event"))[col].drop_nans().to_numpy()
        p = (1 + int((null <= star).sum())) / (1 + len(null))
        pct = 100 * float((null < star).mean())
        verdict = "inside middle 90%: NULL" if 5 <= pct <= 95 else "outside middle 90%"
        log.info(
            "%-30s n=%3d  S*=%+.3f  null median=%+.3f  pct=%5.1f  p=%.3f  %s",
            label, len(null), star, float(np.median(null)), pct, p, verdict,
        )


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    frames = []
    for h in (63, 42):
        df = run(cfg, h)
        summarise(df, h)
        frames.append(df.with_columns(pl.lit(h).alias("h")))
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(cfg.tables_dir / "permutation.parquet")


if __name__ == "__main__":
    main()

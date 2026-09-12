"""Randomization inference for the Sep-2025 event (D-11, D-22).

Recompute the regime-change statistic at every eligible pseudo-event date and rank the true date
against that distribution. Statistic, pre-registered before any result:

    S(tau) = [ln w_Tue(post) - ln w_Tue(pre)] - [ln w_Thu(post) - ln w_Thu(pre)]

At Sep-2025 Nifty moved Thursday to Tuesday and Sensex moved Tuesday to Thursday (measured,
``regime_timeline*.json``). If each clock tracks its own expiry (H_own, D-22), ``S_NIFTY < 0``,
``S_SENSEX > 0``, and the difference ``DiD = S_NIFTY - S_SENSEX`` is strongly negative. Any
calendar shock common to both markets cancels in the difference, which is the reason Sensex was
added: the NSE-only test (``S_NIFTY`` alone) came back null. If the suppression is market-wide and
follows the dominant NSE expiry (H_market), ``S_SENSEX`` looks like ``S_NIFTY`` and DiD is near 0.

Also reported: the stock-future channel (mean squared return by weekday, no model fit). Stocks trade
on NSE only, so they carry no DiD.

Eligibility. A pseudo window [tau-h, tau+h) must lie inside the sample, must not contain the real
Sep-2025 boundary, and must not overlap the Nov-2024 blackout (2024-11-13 to 2025-01-31), which
also covers the Sensex Friday-to-Tuesday change of 2025-01-03 / 01-07 and its lot-size overlap.
Windows are counted in trading sessions.

Degenerate fits. On three-month windows some clock fits are numerically broken (weights outside
[0.1, 10], condition numbers to 1e9). A sensitivity row drops them. That rule was written after
the NSE-only test had come back null, so it is reported beside the pre-registered rows, never
instead of them.

Honest limit: adjacent pseudo dates share almost all their data; there are about five independent
pseudo windows at h = 63, so no p-value from this design can be much smaller than one in six. The
DiD removes common shocks; it does not add independent windows.

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
SYMBOLS = ("NIFTY", "SENSEX")


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


def _options_stat(cfg: Settings, symbol: str, pre: tuple, post: tuple) -> tuple[float, float, float]:
    """S, the worse condition number of the two fits, and the largest |ln weight| in either."""
    a = fit(cfg, symbol, "total_var_corr", window=pre, dummies=("weekend",))
    b = fit(cfg, symbol, "total_var_corr", window=post, dummies=("weekend",))
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
        row: dict = {"tau": sessions[tau], "is_event": tau == split}
        for symbol in SYMBOLS:
            key = symbol.lower()
            try:
                s, cond, extreme = _options_stat(cfg, symbol, pre, post)
            except ValueError:  # too few observations in a window; the date is not usable
                s, cond, extreme = float("nan"), float("inf"), float("inf")
            row |= {f"s_{key}": s, f"cond_{key}": cond, f"logw_{key}": extreme}
        row["did"] = row["s_nifty"] - row["s_sensex"]
        row["s_stocks"] = _stock_stat(rets, pre, post) if rets is not None else float("nan")
        rows.append(row)
    return pl.DataFrame(rows)


def summarise(df: pl.DataFrame, h: int) -> None:
    healthy = pl.lit(True)
    for key in ("nifty", "sensex"):
        healthy = healthy & (pl.col(f"cond_{key}") <= MAX_COND) & (pl.col(f"logw_{key}") <= MAX_ABS_LOGW)

    log.info("--- randomization inference, h = %d sessions ---", h)
    # (label, column, frame, predicted sign under H_own)
    for label, col, frame, sign in (
        ("NIFTY options", "s_nifty", df, -1),
        ("SENSEX options", "s_sensex", df, +1),
        ("DiD NIFTY - SENSEX", "did", df, -1),
        ("DiD, degenerate excluded", "did", df.filter(healthy), -1),
        ("stocks (NSE)", "s_stocks", df, -1),
    ):
        event = frame.filter(pl.col("is_event"))
        if event.is_empty() or np.isnan(event[col][0]):
            log.info("%-26s true event not evaluable", label)
            continue
        star = float(event[col][0])
        null = frame.filter(~pl.col("is_event"))[col].drop_nans().to_numpy()
        extreme = (null <= star) if sign < 0 else (null >= star)
        p = (1 + int(extreme.sum())) / (1 + len(null))
        pct = 100 * float((null < star).mean())
        verdict = "inside middle 90%: NULL" if 5 <= pct <= 95 else "outside middle 90%"
        log.info(
            "%-26s n=%3d  S*=%+.3f  null median=%+.3f  pct=%5.1f  p=%.3f (%s-sided)  %s",
            label, len(null), star, float(np.median(null)), pct, p, "lower" if sign < 0 else "upper", verdict,
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

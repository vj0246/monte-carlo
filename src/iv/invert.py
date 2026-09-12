"""Invert option settlement prices to Black-76 implied volatility on the parity forwards.

Time to expiry stays ACT/365 calendar time. The project claims variance does not accrue uniformly in
calendar time, so the inversion keeps the naive convention and lets src.clock do the reshaping.
Baking a clock in here would assume the answer.

Enforced here rather than left to the caller:

* No-arbitrage bounds. An undiscounted call must sit strictly between intrinsic and the forward.
  Real settlements violate this (D-05 found a 25050 call above the 25000 call), and outside the
  bounds no implied vol exists. Those rows return null and are counted, never clipped into range.
* The D-06 liquidity and moneyness filters -- an IV off an untraded strike is the exchange model.
* ``dte_trd``, distance to expiry in trading sessions, since the clock sums weights over sessions.

Run:  uv run python -m src.iv.invert
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import polars as pl
from scipy.stats import norm

from src.config import Settings, settings

log = logging.getLogger(__name__)

_MIN_VOL, _MAX_VOL = 1e-4, 5.0
_NEWTON_STEPS = 64
_TOL = 1e-9


def black76_undiscounted(sigma, fwd, strike, ttm, is_call):
    """Black-76 price divided by the discount factor. Vectorised over numpy arrays."""
    sqrt_t = np.sqrt(ttm)
    d1 = (np.log(fwd / strike) + 0.5 * sigma**2 * ttm) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    call = fwd * norm.cdf(d1) - strike * norm.cdf(d2)
    return np.where(is_call, call, call - fwd + strike)  # put-call parity, undiscounted


def black76_vega(sigma, fwd, strike, ttm):
    """dPrice/dSigma, undiscounted. Identical for calls and puts."""
    sqrt_t = np.sqrt(ttm)
    d1 = (np.log(fwd / strike) + 0.5 * sigma**2 * ttm) / (sigma * sqrt_t)
    return fwd * norm.pdf(d1) * sqrt_t


def implied_vol(price, fwd, strike, ttm, discount, is_call):
    """Vectorised Black-76 inversion. Returns NaN where no implied volatility exists.

    Newton from a Brenner-Subrahmanyam start, then vectorised bisection on the stragglers. Newton
    alone is not enough: vega collapses for short-dated away-from-the-money contracts, which is
    exactly the corner of the surface this project lives in, and a near-zero derivative sends
    Newton anywhere.
    """
    price = np.asarray(price, dtype=float) / np.asarray(discount, dtype=float)
    fwd, strike, ttm = (np.asarray(x, dtype=float) for x in (fwd, strike, ttm))
    is_call = np.asarray(is_call, dtype=bool)

    intrinsic = np.where(is_call, np.maximum(fwd - strike, 0.0), np.maximum(strike - fwd, 0.0))
    ceiling = np.where(is_call, fwd, strike)
    # Strict inequalities: at either bound the implied vol is 0 or infinite, neither of which is a
    # measurement. Tolerance is relative to the forward so it scales with the index level.
    eps = 1e-8 * fwd
    feasible = (price > intrinsic + eps) & (price < ceiling - eps) & (ttm > 0)

    sigma = np.full(price.shape, np.nan)
    if not feasible.any():
        return sigma

    f, k, t, p, c = (x[feasible] for x in (fwd, strike, ttm, price, is_call))
    guess = np.clip(np.sqrt(2.0 * np.pi / t) * p / f, _MIN_VOL, _MAX_VOL)
    for _ in range(_NEWTON_STEPS):
        diff = black76_undiscounted(guess, f, k, t, c) - p
        vega = black76_vega(guess, f, k, t)
        # Guard the division itself, not just its result: vega underflows to zero for short-dated
        # away-from-the-money contracts, and evaluating diff/vega there raises before np.where
        # ever gets to discard it.
        step = np.divide(diff, vega, out=np.zeros_like(diff), where=vega > 1e-12)
        guess = np.clip(guess - step, _MIN_VOL, _MAX_VOL)

    resid = np.abs(black76_undiscounted(guess, f, k, t, c) - p)
    stuck = resid > _TOL * np.maximum(f, 1.0)
    if stuck.any():
        guess[stuck] = _bisect(p[stuck], f[stuck], k[stuck], t[stuck], c[stuck])

    sigma[feasible] = guess
    return sigma


def _bisect(p, f, k, t, c, steps: int = 60) -> np.ndarray:
    """Vectorised bisection on [_MIN_VOL, _MAX_VOL] for the contracts Newton could not finish.

    Replaces a per-contract ``brentq`` loop. With Sensex added the loop ran long enough, on a
    memory-starved machine, that the job was killed twice. Price is increasing in volatility, so
    bisection is exact; 60 halvings of a width-5 bracket is far below the pricing tolerance. A
    price the bracket cannot reach has no implied volatility and returns NaN, as ``brentq`` did.
    """
    lo = np.full(p.shape, _MIN_VOL)
    hi = np.full(p.shape, _MAX_VOL)
    reachable = (black76_undiscounted(lo, f, k, t, c) <= p) & (black76_undiscounted(hi, f, k, t, c) >= p)
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        above = black76_undiscounted(mid, f, k, t, c) > p
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    return np.where(reachable, 0.5 * (lo + hi), np.nan)


def _session_index(cfg: Settings) -> pl.DataFrame:
    cal = json.loads((cfg.raw_dir / "calendar.json").read_text(encoding="utf-8"))
    days = [dt.date.fromisoformat(d) for d in cal["trading_days"]]
    return pl.DataFrame(
        {"day": days, "session": list(range(len(days)))},
        schema={"day": pl.Date, "session": pl.Int64},
    )


def build_surface(cfg: Settings = settings) -> pl.DataFrame:
    # Liquidity filters (D-06) first, and lazily. The panel is 2.2M contract-days across NSE and
    # BSE and most never traded; loading all of it and mapping sessions through Python lists ran the
    # machine out of memory once Sensex was added. The filters commute, so the output is unchanged.
    panel = (
        pl.concat(
            [pl.scan_parquet(f) for f in sorted(cfg.panel_dir.glob("fo_*.parquet"))],
            how="vertical_relaxed",
        )
        .filter(
            (pl.col("instr") == "IDO")
            & (pl.col("volume") > 0)
            & (pl.col("n_trades") >= 5)
            & (pl.col("open_int") >= 500)
        )
        .select(
            "trade_date", "symbol", "expiry", "tenor", "dte_cal", "strike", "opt_type",
            "settle", "volume", "n_trades", "open_int",
        )
        .collect()
    )
    forwards = pl.read_parquet(cfg.tables_dir / "forwards.parquet").select(
        ["trade_date", "symbol", "expiry", "forward", "discount", "n_pairs"]
    )
    sessions = _session_index(cfg)

    df = (
        panel.join(forwards, on=["trade_date", "symbol", "expiry"], how="inner")
        .join(sessions.rename({"day": "trade_date", "session": "trd_session"}), on="trade_date", how="left")
        .join(sessions.rename({"day": "expiry", "session": "exp_session"}), on="expiry", how="left")
        .with_columns(
            (pl.col("exp_session") - pl.col("trd_session")).alias("dte_trd"),
            (pl.col("strike") / pl.col("forward")).log().alias("log_moneyness"),
            (pl.col("dte_cal") / 365.0).alias("ttm"),
        )
    )

    # D-06, the parts that need the forward and the session map. dte_trd is null for expiries past
    # the end of the sample; those are long-dated and fall out here.
    kept = df.filter((pl.col("log_moneyness").abs() <= 0.15) & (pl.col("dte_trd") >= 2))

    sigma = implied_vol(
        kept["settle"].to_numpy(),
        kept["forward"].to_numpy(),
        kept["strike"].to_numpy(),
        kept["ttm"].to_numpy(),
        kept["discount"].to_numpy(),
        (kept["opt_type"] == "CE").to_numpy(),
    )
    out = kept.with_columns(pl.Series("iv", sigma)).with_columns(
        pl.Series("vega", black76_vega(sigma, kept["forward"].to_numpy(), kept["strike"].to_numpy(), kept["ttm"].to_numpy()))
        * pl.col("discount"),
        (pl.col("iv") ** 2 * pl.col("ttm")).alias("total_var"),
    )
    return out.select(
        "trade_date", "symbol", "expiry", "tenor", "dte_cal", "dte_trd", "ttm",
        "strike", "opt_type", "forward", "discount", "log_moneyness",
        "settle", "volume", "n_trades", "open_int", "iv", "vega", "total_var",
    )


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    surface = build_surface(cfg)
    path = cfg.tables_dir / "iv_surface.parquet"
    surface.write_parquet(path)
    n_null = surface.filter(pl.col("iv").is_null() | pl.col("iv").is_nan()).height
    log.info("wrote %s: %d quotes", path, surface.height)
    log.info("no implied vol (outside no-arbitrage bounds): %d (%.3f%%)", n_null, 100 * n_null / max(surface.height, 1))
    ok = surface.filter(pl.col("iv").is_not_nan() & pl.col("iv").is_not_null())
    for tenor in ("weekly", "monthly"):
        sub = ok.filter(pl.col("tenor") == tenor)
        if sub.is_empty():
            continue
        log.info(
            "%s: n=%d  median IV=%.1f%%  median vega=%.1f",
            tenor, sub.height, 100 * sub["iv"].median(), sub["vega"].median(),
        )


if __name__ == "__main__":
    main()

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
from scipy.optimize import brentq
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

    Newton from a Brenner-Subrahmanyam start, then Brent on the stragglers. Newton alone is not
    enough: vega collapses for short-dated away-from-the-money contracts, which is exactly the
    corner of the surface this project lives in, and a near-zero derivative sends Newton anywhere.
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
    for i in np.flatnonzero(stuck):
        def objective(s, i=i):
            return float(black76_undiscounted(s, f[i], k[i], t[i], c[i]) - p[i])

        try:
            guess[i] = brentq(objective, _MIN_VOL, _MAX_VOL, xtol=1e-10)
        except ValueError:
            guess[i] = np.nan

    sigma[feasible] = guess
    return sigma


def _session_index(cfg: Settings) -> dict[dt.date, int]:
    cal = json.loads((cfg.raw_dir / "calendar.json").read_text(encoding="utf-8"))
    return {dt.date.fromisoformat(d): i for i, d in enumerate(cal["trading_days"])}


def build_surface(cfg: Settings = settings) -> pl.DataFrame:
    panel = pl.concat(
        [pl.read_parquet(f) for f in sorted(cfg.panel_dir.glob("fo_*.parquet"))],
        how="vertical_relaxed",
    ).filter(pl.col("instr") == "IDO")
    forwards = pl.read_parquet(cfg.tables_dir / "forwards.parquet").select(
        ["trade_date", "symbol", "expiry", "forward", "discount", "n_pairs"]
    )

    df = panel.join(forwards, on=["trade_date", "symbol", "expiry"], how="inner")

    sessions = _session_index(cfg)
    expiry_session = pl.Series(
        "exp_session", [sessions.get(d) for d in df["expiry"].to_list()], dtype=pl.Int64
    )
    trade_session = pl.Series(
        "trd_session", [sessions.get(d) for d in df["trade_date"].to_list()], dtype=pl.Int64
    )
    df = df.with_columns(expiry_session, trade_session).with_columns(
        (pl.col("exp_session") - pl.col("trd_session")).alias("dte_trd"),
        (pl.col("strike") / pl.col("forward")).log().alias("log_moneyness"),
        (pl.col("dte_cal") / 365.0).alias("ttm"),
    )

    # D-06. dte_trd is null for expiries beyond the end of the sample; those are long-dated
    # contracts and are excluded by the tenor filter downstream anyway.
    kept = df.filter(
        (pl.col("volume") > 0)
        & (pl.col("n_trades") >= 5)
        & (pl.col("open_int") >= 500)
        & (pl.col("log_moneyness").abs() <= 0.15)
        & (pl.col("dte_trd") >= 2)
    )

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

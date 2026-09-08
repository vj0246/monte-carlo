"""Recover the forward per (trade date, symbol, expiry) from the put-call parity regression.

NSE lists three monthly index futures against ~18 quoted option expiries, so every weekly -- the
object of this project -- has no matching future. The forward comes from the option surface itself:

    C(K) - P(K)  =  D * (F - K)

with D = exp(-r*T) held fixed. D is deliberately not estimated: free-slope fits returned discount
factors above 1.0 (negative rates), and a shared-rate fit wandered between -1.03% and 11.98% across
four dates whose true rate barely moved. The rate is not identifiable at these maturities. It also
does not matter -- F moved 0.04 index points between r=0 and r=11.98% -- so r is an assumption whose
irrelevance is measured, not asserted. ``forward_rate_sensitivity`` reports it.

Dispersion of the per-strike forwards is the parity violation, so gate A2b falls out of the
estimation rather than being bolted on.

Run:  uv run python -m src.iv.forward
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
import polars as pl

from src.config import Settings, settings

log = logging.getLogger(__name__)

#: Assumed continuously-compounded INR rate. An assumption, not an estimate -- see module docstring.
DEFAULT_RATE = 0.065
#: Minimum liquid call/put strike pairs for a forward to be reported at all.
MIN_PAIRS = 6


@dataclass(frozen=True)
class Forward:
    trade_date: dt.date
    symbol: str
    expiry: dt.date
    tenor: str
    dte_cal: int
    n_pairs: int
    forward: float
    discount: float
    fwd_dev_max: float
    fwd_mad: float


def _liquid_pairs(day_df: pl.DataFrame, min_trades: int) -> pl.DataFrame:
    """Strikes quoted liquidly on *both* legs, which is what parity needs.

    D-02a: for an untraded strike the settlement price is an exchange model output equal to
    discounted intrinsic, which satisfies parity by construction. Including those strikes would
    make the parity residual measure the exchange's model rather than the market, and would make
    gate A2b pass for the wrong reason.
    """
    liquid = day_df.filter((pl.col("volume") > 0) & (pl.col("n_trades") >= min_trades))
    calls = liquid.filter(pl.col("opt_type") == "CE").select(["strike", "settle"]).rename(
        {"settle": "call"}
    )
    puts = liquid.filter(pl.col("opt_type") == "PE").select(["strike", "settle"]).rename(
        {"settle": "put"}
    )
    return calls.join(puts, on="strike", how="inner").drop_nulls().sort("strike")


def forward_for_expiry(
    day_df: pl.DataFrame, dte_cal: int, rate: float = DEFAULT_RATE, min_trades: int = 5
) -> tuple[float, float, int, float, float] | None:
    """Return ``(forward, discount, n_pairs, fwd_dev_max, fwd_mad)`` or ``None`` if unidentified."""
    if dte_cal < 1:
        # D-02a: on expiry day the settlement column for the expiring series is the underlying's
        # final settlement value, not an option price. Fitting it returned a residual of 2471
        # index points against a typical 20 to 40. Refuse rather than trust the caller to filter.
        raise ValueError(f"dte_cal={dte_cal}: the expiring series carries no option prices (D-02a)")

    pairs = _liquid_pairs(day_df, min_trades)
    if pairs.height < MIN_PAIRS:
        return None

    strikes = pairs["strike"].to_numpy()
    basis = (pairs["call"] - pairs["put"]).to_numpy()
    discount = float(np.exp(-rate * dte_cal / 365.0))

    # With D fixed, every strike yields its own independent estimate of the forward. Take the
    # median, not the mean.
    #
    # The mean is what a fixed-slope least-squares fit computes, and it is not robust here.
    # Settlement prices on thinly traded strikes violate no-arbitrage outright: on 2025-07-17 the
    # 25050 call settled at 548.35 while the 25000 call settled at 414.20 -- a higher strike priced
    # above a lower one -- on 6 and 22 trades respectively. Two such strikes dragged the OLS
    # forward 21 points off a tight cluster formed by the other six. The median ignores them.
    per_strike = strikes + basis / discount
    forward = float(np.median(per_strike))
    # Dispersion of the per-strike forwards is the parity violation in index points, and it is the
    # A2b statistic. MAD rather than standard deviation, for the same robustness reason.
    dispersion = float(np.median(np.abs(per_strike - forward)))
    return forward, discount, pairs.height, float(np.abs(per_strike - forward).max()), dispersion


def build_forwards(
    cfg: Settings = settings, rate: float = DEFAULT_RATE, tenors: tuple[str, ...] = ("weekly", "monthly")
) -> pl.DataFrame:
    frames = sorted(cfg.panel_dir.glob("fo_*.parquet"))
    panel = pl.concat([pl.read_parquet(f) for f in frames], how="vertical_relaxed")
    panel = panel.filter(
        (pl.col("instr") == "IDO") & pl.col("tenor").is_in(list(tenors)) & (pl.col("dte_cal") >= 1)
    )

    rows: list[Forward] = []
    keys = panel.select(["trade_date", "symbol", "expiry", "tenor", "dte_cal"]).unique()
    for key in keys.sort(["trade_date", "symbol", "expiry"]).iter_rows(named=True):
        group = panel.filter(
            (pl.col("trade_date") == key["trade_date"])
            & (pl.col("symbol") == key["symbol"])
            & (pl.col("expiry") == key["expiry"])
        )
        got = forward_for_expiry(group, key["dte_cal"], rate=rate)
        if got is None:
            continue
        fwd, disc, n, dev_max, mad = got
        rows.append(
            Forward(**key, n_pairs=n, forward=fwd, discount=disc, fwd_dev_max=dev_max, fwd_mad=mad)
        )

    return pl.DataFrame([r.__dict__ for r in rows]).sort(["trade_date", "symbol", "expiry"])


def forward_rate_sensitivity(
    cfg: Settings = settings, rates: tuple[float, ...] = (0.0, 0.065, 0.12)
) -> pl.DataFrame:
    """How far the forward moves across the plausible rate range. D-05 requires this be reported."""
    base = build_forwards(cfg, rate=rates[1]).select(
        ["trade_date", "symbol", "expiry", "forward"]
    ).rename({"forward": "f_base"})
    out = base
    for r in (rates[0], rates[2]):
        alt = build_forwards(cfg, rate=r).select(["trade_date", "symbol", "expiry", "forward"])
        out = out.join(
            alt.rename({"forward": f"f_{r:g}"}), on=["trade_date", "symbol", "expiry"], how="inner"
        )
    return out.with_columns(
        (pl.col(f"f_{rates[0]:g}") - pl.col(f"f_{rates[2]:g}")).abs().alias("forward_span")
    )


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fwd = build_forwards(cfg)
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.tables_dir / "forwards.parquet"
    fwd.write_parquet(path)
    log.info("wrote %s: %d expiry-days", path, fwd.height)
    for tenor in ("weekly", "monthly"):
        sub = fwd.filter(pl.col("tenor") == tenor)
        if sub.is_empty():
            continue
        log.info(
            "%s: n=%d  median pairs=%.0f  median MAD=%.2f  p95 max-dev=%.2f",
            tenor,
            sub.height,
            sub["n_pairs"].median(),
            sub["fwd_mad"].median(),
            sub["fwd_dev_max"].quantile(0.95),
        )


if __name__ == "__main__":
    main()

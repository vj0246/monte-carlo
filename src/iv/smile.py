"""Collapse the implied-vol surface to one at-the-money total variance per (date, symbol, expiry).

What the clock estimator consumes. Two requirements from D-08a.

Vega weighting: IV measurement error scales as (price error)/vega, so a quote variance goes as
1/vega**2 and the least-squares weight is vega**2. Unweighted, a 2-DTE wing quote with vega ~87 gets
the same say as a 13-DTE at-the-money quote with vega ~1600.

Noise de-biasing: the clock fits total variance, and E[iv_hat**2] = iv**2 + Var(iv_hat), so squaring
turns measurement noise into an upward variance bias -- largest exactly where the expiry day
dominates the remaining-day set. Var(iv_hat) is the sampling variance of the WLS intercept,
available in closed form, and is subtracted. Measured, it is small (~0.01% of variance) because the
fit averages ~50 quotes; it would be ~5% if the clock consumed single quotes.

Both corrected and uncorrected total variance are written out. An expiry-day effect that exists only
in the uncorrected column is measurement error.

Run:  uv run python -m src.iv.smile
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl

from src.config import Settings, settings

log = logging.getLogger(__name__)

#: Quadratic in log-moneyness needs 3 parameters; 6 leaves degrees of freedom for a variance.
MIN_QUOTES = 6


def fit_smile(
    log_moneyness: np.ndarray, iv: np.ndarray, vega: np.ndarray
) -> tuple[float, float, float, int] | None:
    """Vega-weighted quadratic smile fit.

    Returns ``(atm_iv, var_atm_iv, resid_rms, n)`` where ``atm_iv`` is the fitted value at
    log-moneyness zero and ``var_atm_iv`` is its sampling variance.
    """
    n = len(iv)
    if n < MIN_QUOTES:
        return None
    design = np.vstack([np.ones(n), log_moneyness, log_moneyness**2]).T
    weights = vega**2
    if not np.isfinite(weights).all() or weights.sum() <= 0:
        return None

    wx = design * weights[:, None]
    xtwx = design.T @ wx
    try:
        xtwx_inv = np.linalg.inv(xtwx)
    except np.linalg.LinAlgError:
        return None
    beta = xtwx_inv @ (wx.T @ iv)

    resid = iv - design @ beta
    dof = n - 3
    # Scale factor for the weights: they are proportional to the inverse error variance, not equal
    # to it, so the common factor has to be estimated from the residuals.
    scale = float(resid @ (weights * resid) / dof)
    var_atm = float(scale * xtwx_inv[0, 0])
    return float(beta[0]), var_atm, float(np.sqrt(np.mean(resid**2))), n


def build_atm(cfg: Settings = settings) -> pl.DataFrame:
    surface = pl.read_parquet(cfg.tables_dir / "iv_surface.parquet").filter(
        pl.col("iv").is_not_null() & pl.col("iv").is_not_nan() & (pl.col("vega") > 0)
    )

    rows: list[dict] = []
    for key, group in surface.group_by(["trade_date", "symbol", "expiry"], maintain_order=True):
        fit = fit_smile(
            group["log_moneyness"].to_numpy(), group["iv"].to_numpy(), group["vega"].to_numpy()
        )
        if fit is None:
            continue
        atm_iv, var_atm, resid_rms, n = fit
        if not np.isfinite([atm_iv, var_atm]).all() or atm_iv <= 0:
            continue
        ttm = float(group["ttm"][0])
        # E[iv_hat^2] = iv^2 + Var(iv_hat). Subtract the second term (D-08a).
        iv2_corrected = atm_iv**2 - var_atm
        rows.append(
            {
                "trade_date": key[0],
                "symbol": key[1],
                "expiry": key[2],
                "tenor": group["tenor"][0],
                "dte_cal": int(group["dte_cal"][0]),
                "dte_trd": int(group["dte_trd"][0]),
                "ttm": ttm,
                "n_quotes": n,
                "atm_iv": atm_iv,
                "var_atm_iv": var_atm,
                "resid_rms": resid_rms,
                "total_var_raw": atm_iv**2 * ttm,
                # Null rather than clipped when the correction exceeds the estimate: that means the
                # observation carries no usable variance signal, and a floored value would enter
                # the clock fit as if it did.
                "total_var_corr": iv2_corrected * ttm if iv2_corrected > 0 else None,
                "noise_share": var_atm / atm_iv**2,
            }
        )

    return pl.DataFrame(rows).sort(["symbol", "trade_date", "expiry"])


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    atm = build_atm(cfg)
    path = cfg.tables_dir / "atm_variance.parquet"
    atm.write_parquet(path)
    log.info("wrote %s: %d expiry-days", path, atm.height)
    log.info("dropped by correction (noise exceeded signal): %d", atm["total_var_corr"].null_count())
    buckets = atm.with_columns(
        pl.when(pl.col("dte_trd") <= 3)
        .then(pl.lit("2-3"))
        .when(pl.col("dte_trd") <= 6)
        .then(pl.lit("4-6"))
        .when(pl.col("dte_trd") <= 12)
        .then(pl.lit("7-12"))
        .otherwise(pl.lit("13+"))
        .alias("bucket")
    )
    for row in (
        buckets.group_by("bucket")
        .agg(
            pl.len().alias("n"),
            pl.col("atm_iv").median().alias("iv"),
            pl.col("noise_share").median().alias("noise"),
        )
        .sort("bucket")
        .iter_rows()
    ):
        log.info(
            "dte %-5s n=%5d  median ATM IV=%5.1f%%  median noise share of variance=%6.3f%%",
            row[0], row[1], 100 * row[2], 100 * row[3],
        )


if __name__ == "__main__":
    main()

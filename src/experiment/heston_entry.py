"""Does the entry-price headline survive stochastic volatility? (D-08c, D-14, D-24)

``src.experiment.mispricing`` prices the calendar-time trader's error under GBM: -21.8% on a
one-session straddle opened Monday. D-24 only ever tested the hedging share under Heston. Here both
traders price in the same time-changed Heston model (``v0 = theta``, so they agree on the expected
level and differ only in the clock's shape), exactly, by Fourier (``src.sim.heston_clock``).

**What to expect, derived before running.** Write ``X = M - V/2`` with ``M = int sqrt(w v) dW1`` and
``V = int w v dt``. Ito on ``M^3`` and ``M^4``, with ``Cov(v_s, v_t) ~ xi^2 theta min(s, t)`` (kappa
dropped: ``kappa T <= 0.05`` here), gives the leading cumulants

    m  = E[V] = theta int w,              S(t) = int_0^t sqrt(w),   Var(X) = m - k3/3
    k3 = 3 rho xi theta int w S dt,       k4 = 3 Var(V) + 6 rho^2 xi^2 theta int w S^2 dt
    Var(V) = 2 xi^2 theta int w(t) int_0^t w(s) s ds dt

and an Edgeworth expansion of ``E|e^X - 1|`` (the skew enters twice: through ``E[X|X|]`` and through
its coupling to the ``-V/2`` drift) gives

    straddle = Black76(m) [1 + k3/(12m) + (k3^2/m^3 - k4/m^2)/24]

At ``w = 1`` that is an implied-variance shift ``T (rho xi theta/4 + xi^2 (rho^2/4 - 1)/12)``. Two
consequences, both falsifiable by the table:

1. On one piece of constant ``w``, ``M / sqrt(w)`` has a ``w``-free law (the variance process is not
   clocked, only what the spot sees), so ``k3^2/m^3`` and ``k4/m^2`` cancel between the traders and
   the Monday error is the GBM error up to ``rho xi T (sqrt(w) - 1)/8``: under 0.01 pp.
2. Across pieces the weight on ``w(t)`` in ``Var(V)`` grows with ``t``, and the clock lightens the
   last session (the expiry Tuesday), so ``k4/m^2`` is smaller on the clock and the error moves up
   with ``xi^2``. The correction itself is about ``-xi^2 T / (24 theta)``, -1.2% at ``xi = 0.5`` over
   a week. The clock changes it by a few percent of itself over a full week (Tue, +0.04 pp at
   ``xi = 0.5``) and by far more on Fri, whose span is the heaviest session followed by the lightest
   (+0.13 pp). The rough guess written here first, "hundredths of a pp everywhere", was right for
   the week and wrong by a factor of ten for Fri; the ``predicted_error`` column is the formula
   itself, evaluated per product (the spread leg by leg), and that is what the table tests.

**Monte Carlo at equal cost.** The same prices by six estimators (``src.sim.terminal_mc``), scored
against Fourier: RMSE against ``n`` over ``MC_REPS`` seeds, and its log-log slope. Efficiency,
``1 / (variance x seconds)``, needs better numbers than 16 seeds give: a variance from 16 draws has
a 37% relative SE, and one wall-clock reading moved by a third between runs. So the cost is the
fastest of ``TIMING_REPEATS`` runs of ``BIG_PATHS`` paths, and for the pseudo-random methods the
variance is read off the i.i.d. units of those same runs. RQMC's units are not independent, so its
variance stays the spread over its randomisations, the only honest error bar it has.

Run:  uv run python -m src.experiment.heston_entry
"""

from __future__ import annotations

import logging
import time

import numpy as np
import polars as pl

from src.config import Settings, settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPANS, SPOT, TUE_REGIME
from src.provenance import manifest, write_manifest
from src.sim import heston_clock, terminal_mc
from src.sim.engine import straddle_price
from src.sim.heston import HestonParams

KAPPA = 2.0
THETA = ANNUAL_VOL**2
DT = 1.0 / SESSIONS_PER_YEAR
RHOS = (0.0, -0.3, -0.6, -0.9)
XIS = (0.2, 0.5, 0.8)
OPENED = ("Mon", "Fri", "Thu", "Wed", "Tue")
#: Monday near/far calendar spread: long next week's straddle, short tomorrow's.
SPREAD_DAYS = SPANS["Mon"] + SPANS["Tue"]

MC_CELLS = ((-0.6, 0.5), (-0.9, 0.8))
MC_PRODUCTS = ("Mon", "Fri", "spread_Mon")
MC_PATHS = tuple(2**k for k in range(10, 16))
MC_REPS = 16
BIG_PATHS = 2**17
TIMING_REPEATS = 3
MC_STEPS_PER_SESSION = 8
BIAS_STEPS_PER_SESSION = (1, 2, 4, 8, 16)
SEED = 29


def clock(days: list[str]) -> np.ndarray:
    mean = float(np.mean(list(TUE_REGIME.values())))
    return np.array([TUE_REGIME[d] / mean for d in days])


def price(weights: np.ndarray, p: HestonParams | None, near: int = 0) -> float:
    """ATM straddle on ``weights``; minus the straddle on the first ``near`` pieces if given.
    ``p=None`` is the GBM limit, priced by Black-76 directly."""
    def one(w: np.ndarray) -> float:
        if p is None:
            return straddle_price(SPOT, SPOT, THETA * DT * float(w.sum()))
        return heston_clock.straddle_price(SPOT, SPOT, w, DT, p)

    return one(weights) - (one(weights[:near]) if near else 0.0)


def correction(weights: np.ndarray, p: HestonParams, per_session: int = 64) -> float:
    """``1 + k3/(12m) + (k3^2/m^3 - k4/m^2)/24``: the stochastic-vol factor on an ATM straddle.

    Cumulants of ``X`` to leading order in ``T`` with ``kappa`` dropped (``kappa T <= 0.05`` here),
    on a midpoint grid of ``per_session`` points per session. See the module docstring.
    """
    h = DT / per_session
    w = np.repeat(weights, per_session)
    t = (np.arange(w.size) + 0.5) * h
    m = THETA * float(w.sum()) * h
    root_clock = (np.cumsum(np.sqrt(w)) - 0.5 * np.sqrt(w)) * h  # int_0^t sqrt(w) ds
    weighted_time = (np.cumsum(w * t) - 0.5 * w * t) * h  # int_0^t w(s) s ds
    k3 = 3 * p.rho * p.xi * THETA * float((w * root_clock).sum()) * h
    var_v = 2 * p.xi**2 * THETA * float((w * weighted_time).sum()) * h
    k4 = 3 * var_v + 6 * p.rho**2 * p.xi**2 * THETA * float((w * root_clock**2).sum()) * h
    return 1 + k3 / (12 * m) + (k3**2 / m**3 - k4 / m**2) / 24


def approx_price(weights: np.ndarray, p: HestonParams, near: int = 0) -> float:
    """``price`` with each leg's Heston correction replaced by ``correction``. The near leg of a
    spread is a straddle on its own pieces (``v0 = theta``), so the factor applies leg by leg."""
    def one(w: np.ndarray) -> float:
        return correction(w, p) * straddle_price(SPOT, SPOT, THETA * DT * float(w.sum()))

    return one(weights) - (one(weights[:near]) if near else 0.0)


def products() -> dict[str, tuple[np.ndarray, int]]:
    """name -> (clock weights, near-leg length; 0 for a plain straddle)."""
    out = {day: (clock(SPANS[day]), 0) for day in OPENED}
    out["spread_Mon"] = (clock(SPREAD_DAYS), len(SPANS["Mon"]))
    return out


def entry_errors() -> pl.DataFrame:
    rows = []
    gbm = {}
    for name, (weights, near) in products().items():
        flat = np.ones_like(weights)
        gbm[name] = price(weights, None, near) / price(flat, None, near) - 1
        rows.append({"rho": 0.0, "xi": 0.0, "product": name, "sessions": weights.size,
                     "clock_price": price(weights, None, near), "flat_price": price(flat, None, near),
                     "error": gbm[name], "gbm_error": gbm[name], "predicted_error": gbm[name]})
    for rho in RHOS:
        for xi in XIS:
            p = HestonParams(v0=THETA, kappa=KAPPA, theta=THETA, xi=xi, rho=rho)
            for name, (weights, near) in products().items():
                flat = np.ones_like(weights)
                clock_price, flat_price = price(weights, p, near), price(flat, p, near)
                predicted = approx_price(weights, p, near) / approx_price(flat, p, near) - 1
                rows.append({"rho": rho, "xi": xi, "product": name, "sessions": weights.size,
                             "clock_price": clock_price, "flat_price": flat_price,
                             "error": clock_price / flat_price - 1, "gbm_error": gbm[name],
                             "predicted_error": predicted})
    table = pl.DataFrame(rows)
    return table.with_columns(
        shift_pp=100 * (pl.col("error") - pl.col("gbm_error")),
        feller_ok=2 * KAPPA * THETA >= pl.col("xi") ** 2,
    )


def legs_of(name: str, per_session: int) -> tuple[np.ndarray, tuple[tuple[int, float], ...]]:
    weights, near = products()[name]
    step_weights = np.repeat(weights, per_session)
    return step_weights, ((step_weights.size, 1.0),) + (((near * per_session, -1.0),) if near else ())


def mc_run(p: HestonParams, name: str, method: str, n_paths: int, per_session: int, reps: int,
           exact: float) -> dict:
    step_weights, legs = legs_of(name, per_session)
    est = np.array([
        terminal_mc.estimate(method, legs, p, step_weights, DT / per_session, SPOT, n_paths,
                             SEED + 1000 * per_session + 100 * int(np.log2(n_paths)) + r)
        for r in range(reps)
    ])
    var = float(est.var(ddof=1))
    return {"rho": p.rho, "xi": p.xi, "product": name, "method": method, "n_paths": n_paths,
            "steps_per_session": per_session, "reps": reps, "exact": exact,
            "bias": float(est.mean() - exact), "bias_se": float(np.sqrt(var / reps)),
            "sd": float(np.sqrt(var)), "rmse": float(np.sqrt(((est - exact) ** 2).mean()))}


def mc_cost(p: HestonParams, name: str, method: str) -> dict:
    """Seconds per path, and for pseudo-random methods variance per path, from big runs.

    Seconds are the fastest of ``TIMING_REPEATS`` runs: other processes only ever add time.
    ``path_variance`` is ``n Var(estimate)`` from all runs' independent units pooled, with relative
    SE ``sqrt((kurtosis - 1) / units)``. NaN for RQMC, whose units are not independent.
    """
    step_weights, legs = legs_of(name, MC_STEPS_PER_SESSION)
    seconds, units = [], []
    for r in range(TIMING_REPEATS):
        start = time.perf_counter()
        units.append(terminal_mc.samples(method, legs, p, step_weights, DT / MC_STEPS_PER_SESSION,
                                         SPOT, BIG_PATHS, SEED + 10**6 + r))
        seconds.append(time.perf_counter() - start)
    pooled = np.concatenate(units)
    centred = pooled - pooled.mean()
    unit_var = float((centred**2).mean())
    pseudo = terminal_mc.METHODS[method][0] != "sobol"
    return {"rho": p.rho, "xi": p.xi, "product": name, "method": method,
            "seconds_per_path": min(seconds) / BIG_PATHS,
            "timing_spread": max(seconds) / min(seconds) - 1,
            "path_variance": unit_var * BIG_PATHS / units[0].size if pseudo else np.nan,
            "variance_rel_se": float(np.sqrt(((centred**4).mean() / unit_var**2 - 1) / pooled.size))
            if pseudo else float(np.sqrt(2 / (MC_REPS - 1)))}


def with_efficiency(grid: pl.DataFrame, costs: pl.DataFrame) -> pl.DataFrame:
    """``1 / (variance x seconds)`` at each ``n``, and its ratio to plain MC with a 95% interval.

    The interval covers the two variance estimates only; ``timing_spread`` reports the clock's noise.
    """
    cell = ["rho", "xi", "product", "n_paths"]
    out = grid.join(costs, on=["rho", "xi", "product", "method"]).with_columns(
        variance=pl.when(pl.col("path_variance").is_nan()).then(pl.col("sd") ** 2)
        .otherwise(pl.col("path_variance") / pl.col("n_paths")),
        seconds=pl.col("seconds_per_path") * pl.col("n_paths"),
    ).with_columns(efficiency=1 / (pl.col("variance") * pl.col("seconds")))
    plain = out.filter(pl.col("method") == "plain").select(
        *cell, plain_eff="efficiency", plain_rse="variance_rel_se")
    half = 1.96 * (pl.col("variance_rel_se") ** 2 + pl.col("plain_rse") ** 2).sqrt()
    return (
        out.join(plain, on=cell)
        .with_columns(efficiency_vs_plain=pl.col("efficiency") / pl.col("plain_eff"))
        .with_columns(eff_vs_plain_lo=pl.col("efficiency_vs_plain") * (-half).exp(),
                      eff_vs_plain_hi=pl.col("efficiency_vs_plain") * half.exp())
        .drop("plain_eff", "plain_rse")
    )


def mc_efficiency() -> pl.DataFrame:
    grid, bias_rows, costs = [], [], []
    for rho, xi in MC_CELLS:
        p = HestonParams(v0=THETA, kappa=KAPPA, theta=THETA, xi=xi, rho=rho)
        for name in MC_PRODUCTS:
            weights, near = products()[name]
            exact = price(weights, p, near)
            for method in terminal_mc.METHODS:
                for n_paths in MC_PATHS:
                    grid.append(mc_run(p, name, method, n_paths, MC_STEPS_PER_SESSION, MC_REPS, exact))
                costs.append(mc_cost(p, name, method))
                logging.info("  %s %s %s done", (rho, xi), name, method)
            # Discretisation bias, read off the estimator whose noise is far below it.
            for per_session in (s for s in BIAS_STEPS_PER_SESSION if s != MC_STEPS_PER_SESSION):
                bias_rows.append(mc_run(p, name, "rqmc_conditional", MC_PATHS[-1], per_session,
                                        MC_REPS, exact))
    table = with_efficiency(pl.DataFrame(grid), pl.DataFrame(costs))
    slopes = table.group_by("rho", "xi", "product", "method").agg(
        rmse_slope=pl.cov(pl.col("n_paths").log(), pl.col("rmse").log()) / pl.col("n_paths").log().var()
    )
    table = table.join(slopes, on=["rho", "xi", "product", "method"])
    return pl.concat([table, pl.DataFrame(bias_rows)], how="diagonal")


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    extra = {"kappa": KAPPA, "theta": THETA, "clock": TUE_REGIME, "spans": SPANS}

    errors = entry_errors()
    path = cfg.tables_dir / "heston_entry_error.parquet"
    errors.write_parquet(path)
    write_manifest(path, manifest("Heston entry-price error", cfg, extra={
        **extra, "rho_grid": list(RHOS), "xi_grid": list(XIS)}))
    logging.info("wrote %s", path)
    for row in errors.iter_rows(named=True):
        logging.info("rho %-5.1f xi %-4.1f %-11s err %8.3f%%  gbm %8.3f%%  shift %7.3fpp  pred %8.3f%%",
                     row["rho"], row["xi"], row["product"], 100 * row["error"],
                     100 * row["gbm_error"], row["shift_pp"], 100 * row["predicted_error"])

    efficiency = mc_efficiency()
    path = cfg.tables_dir / "mc_efficiency.parquet"
    efficiency.write_parquet(path)
    write_manifest(path, manifest("terminal MC efficiency against exact Fourier", cfg, seed=SEED, extra={
        **extra, "cells": [list(c) for c in MC_CELLS], "paths": list(MC_PATHS), "reps": MC_REPS,
        "big_paths": BIG_PATHS, "timing_repeats": TIMING_REPEATS,
        "steps_per_session": MC_STEPS_PER_SESSION, "methods": list(terminal_mc.METHODS)}))
    logging.info("wrote %s", path)
    top = efficiency.filter((pl.col("n_paths") == MC_PATHS[-1])
                            & (pl.col("steps_per_session") == MC_STEPS_PER_SESSION))
    for row in top.iter_rows(named=True):
        logging.info("%-5.1f %-4.1f %-11s %-17s sd %9.5f rmse %9.5f slope %6.2f "
                     "eff x%8.2f [%8.2f, %8.2f] timing +%3.0f%%",
                     row["rho"], row["xi"], row["product"], row["method"], row["sd"], row["rmse"],
                     row["rmse_slope"], row["efficiency_vs_plain"], row["eff_vs_plain_lo"],
                     row["eff_vs_plain_hi"], 100 * row["timing_spread"])


if __name__ == "__main__":
    main()

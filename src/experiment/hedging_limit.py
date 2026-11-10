"""The continuous-hedging limit of the clock experiment, by multilevel Monte Carlo (D-27).

D-25 found that the correct-clock hedger's error falls like ``c m^-1/2`` in the rebalancing count
``m`` while the calendar-minus-clock difference does not fall at all: it converges to the
pathwise EKJS integral ``I = int 0.5 S^2 Gamma_flat (dV_flat - dV_true)``, whose standard
deviation ``F`` is a floor no hedging frequency removes. This module puts numbers on that sentence:

- ``F``, the floor, as ``sqrt(lim E[diff_m^2])`` (the mean is exactly zero on every grid, D-25);
- ``c`` for the clock trader, and the matching constant for the calendar trader's excess over ``F``;
- the crossover ``m* = (c / F)^2`` beyond which the clock, not the rebalancing, is the larger risk.

**Why MLMC.** ``E[diff_m^2]`` approaches ``F^2`` at rate ``1/m``, so reaching the limit means a fine
grid, and plain MC pays for every step of it on every path. Level ``l`` hedges ``2^l`` times per
session. GBM is simulated exactly, so the coarse path is the fine path at every second point: the
same Brownian path, an exact coupling, and ``Var[diff_l^2 - diff_(l-1)^2]`` falls like ``2^-l``.

**Why MLMC does not pay here, and what does.** That ``2^-l`` only matches the cost growth ``2^l``
(beta = gamma, Giles' boundary case, approached from below: the fit over the levels run is under
one), and its constant is large: the difference's own hedging noise
around ``I`` has rate constant ``c_diff`` above ``F`` (``m*_diff = (c_diff / F)^2`` is under two
hedges a session), so a coarse level predicts the fine one poorly and ``V_l C_l`` is flat in ``l``.
At this ``L`` single-level MC is cheaper. The EKJS sum on the same path is the right tool instead: a
control variate whose mean ``E[I_m^2]`` is known exactly (``continuous_hedge``), which leaves only
``E[diff^2 - I^2]`` to estimate. The table reports all three costs; the MLMC run is kept because its
levels are the sd-against-``m`` curve, and because ``lim E[diff^2 - I^2] = 0`` is itself the test
that the hedge's limit is the EKJS integral.

**Why only the floor is telescoped.** The clock and calendar columns ride along on the same paths,
but their constants come from each level's own fine sample. Telescoping a quantity that tends to
zero (``E[clock_m^2] ~ c^2 / m``) is hopeless: the level-0 term has variance ``~2 c^4`` and buries a
level-8 value of ``c^2 / 256``.

**Checks.** The floor is estimated four ways that share no code path beyond the GBM simulator: the
hedge (MLMC), the EKJS sum on the same paths (MLMC), the EKJS sum on a fine grid with an independent
seed, and the Gaussian-kernel closed form of ``src.sim.continuous_hedge``. The rate constants are
checked against that module's leading-order formula.

Run:  uv run python -m src.experiment.hedging_limit
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl

from src.config import Settings, settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT, TUE_REGIME
from src.experiment.sensitivity import SESSION_ORDER
from src.provenance import manifest, write_manifest
from src.sim import continuous_hedge, mlmc
from src.sim.analytic import believed_paths, ekjs_pathwise
from src.sim.engine import hedge_short_straddle, simulate, straddle_price

LEVELS = 9  # m_l = 2^l hedges per session for l = 0..8: up to 256, past the crossover near 140
EPS = 0.5  # target RMSE on E[diff^2], points^2: about 0.02 index points on the floor
N_PILOT = 2_000
SEED = 31
EKJS_PER_SESSION = 256
EKJS_PAIRS = 200_000
CHUNK = 10_000_000  # path-array elements per simulate call (80 MB), to stay well inside memory

COLUMNS = ("diff", "clock", "calendar", "ekjs", "cv", "gap")
SIGMA = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
WEIGHTS = np.array([TUE_REGIME[d] for d in SESSION_ORDER])
WEIGHTS = WEIGHTS / WEIGHTS.mean()
ENTRY = straddle_price(SPOT, SPOT, float(SIGMA**2 * WEIGHTS.sum()))


def _remaining(per_step: np.ndarray) -> np.ndarray:
    """Variance a hedger believes remains at the start of each step, that step included."""
    return per_step[::-1].cumsum()[::-1]


def _pair_mean(x: np.ndarray) -> np.ndarray:
    """``simulate`` mirrors its second half on its first (D-14); averaging each antithetic pair
    gives iid samples, which the MLMC variance estimates assume."""
    half = len(x) // 2
    return 0.5 * (x[:half] + x[half:])


def squares(paths: np.ndarray, per_session: int) -> np.ndarray:
    """Per path: ``diff^2, clock^2, calendar^2, I_m^2``, hedging ``per_session`` times a session,
    then ``diff^2 - I_m^2`` and ``(diff - I_m)^2``.

    The first four are second moments equal to variances, because every mean is exactly zero
    (D-25). ``cv`` is the hedge's second moment with the EKJS sum as a control variate: ``E[I_m^2]``
    is known exactly on every grid, so only ``E[cv]`` has to be estimated. ``gap`` is the pathwise
    distance of the hedge difference from the EKJS sum, which should fall like
    ``continuous_hedge.rate_constant(..., "difference")^2 / m``.
    """
    truth, flat = believed_paths(WEIGHTS, per_session)
    clock = hedge_short_straddle(paths, SPOT, SIGMA**2 * _remaining(truth), ENTRY, 0.0)
    calendar = hedge_short_straddle(paths, SPOT, SIGMA**2 * _remaining(flat), ENTRY, 0.0)
    ekjs = ekjs_pathwise(paths, SPOT, SIGMA**2 * truth, SIGMA**2 * flat)
    diff = calendar - clock
    columns = (diff**2, clock**2, calendar**2, ekjs**2, diff**2 - ekjs**2, (diff - ekjs) ** 2)
    return np.column_stack(columns)


def sample_level(level: int, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """``n`` pair-averaged samples on ``2^level`` hedges per session and on half that, coupled by
    taking the coarse path as every second point of the fine one."""
    per_session = 2**level
    truth, _ = believed_paths(WEIGHTS, per_session)
    chunk = max(1, CHUNK // (2 * (truth.size + 1)))
    fine, coarse = [], []
    for start in range(0, n, chunk):
        k = min(chunk, n - start)
        paths = simulate(truth, SIGMA, SPOT, 2 * k, rng)
        fine.append(_pair_mean(squares(paths, per_session)))
        if level == 0:
            coarse.append(np.zeros((k, len(COLUMNS))))
        else:
            coarse.append(_pair_mean(squares(paths[:, ::2], per_session // 2)))
    return np.concatenate(fine), np.concatenate(coarse)


def level_costs() -> np.ndarray:
    """Hedges per session paid per sample: the fine grid, plus the coarse one above level 0."""
    m = 2.0 ** np.arange(LEVELS)
    return m + np.where(m > 1, m / 2, 0.0)


def ekjs_floor(per_session: int, n_pairs: int, seed: int) -> tuple[float, float]:
    """``sd(I_m)`` and its standard error from the EKJS sum alone, on a fine grid.

    No hedge is involved, and the bias of the second moment is known exactly
    (``continuous_hedge.riemann_second_moment``), so a single fine level is an independent check of
    the hedge's limit.
    """
    truth, flat = believed_paths(WEIGHTS, per_session)
    rng = np.random.default_rng(seed)
    chunk = max(1, CHUNK // (2 * (truth.size + 1)))
    samples = []
    for start in range(0, n_pairs, chunk):
        paths = simulate(truth, SIGMA, SPOT, 2 * min(chunk, n_pairs - start), rng)
        ekjs = ekjs_pathwise(paths, SPOT, SIGMA**2 * truth, SIGMA**2 * flat)
        samples.append(_pair_mean(ekjs**2))
    second = np.concatenate(samples)
    floor = float(np.sqrt(second.mean()))
    return floor, float(second.std(ddof=1) / np.sqrt(second.size) / (2.0 * floor))


def level_table(levels: list[mlmc.Level]) -> pl.DataFrame:
    """One row per level and quantity. ``exact_p`` is the known ``E[I_m^2]`` of the EKJS column."""
    rows = []
    for lev in levels:
        exact = continuous_hedge.riemann_second_moment(WEIGHTS, SIGMA, SPOT, 2**lev.level)
        for j, name in enumerate(COLUMNS):
            rows.append(
                {
                    "level": lev.level,
                    "hedges_per_session": 2**lev.level,
                    "quantity": name,
                    "n_pairs": lev.n,
                    "cost_per_sample": lev.cost,
                    "seconds": lev.seconds,
                    "mean_p": float(lev.mean("fine")[j]),
                    "exact_p": exact if name == "ekjs" else float("nan"),
                    "se_p": float(np.sqrt(lev.var("fine")[j] / lev.n)),
                    "sd": float(np.sqrt(lev.mean("fine")[j])) if name != "cv" else float("nan"),
                    "mean_y": float(lev.mean("y")[j]),
                    "var_y": float(lev.var("y")[j]),
                }
            )
    return pl.DataFrame(rows)


def _ratio_se(num: float, num_se: float, den: float, den_se: float) -> float:
    """Delta-method standard error of ``(num / den)^2`` for independent inputs."""
    return 2.0 * (num / den) ** 2 * np.hypot(num_se / num, den_se / den)


def _root(p: float, p_se: float, scale: float = 1.0) -> tuple[float, float]:
    """``sqrt(scale p)`` and its delta-method standard error: an sd or a rate constant from a
    second moment."""
    r = np.sqrt(scale * p)
    return float(r), float(r * p_se / (2.0 * p))


def _fine(lev: mlmc.Level, column: str) -> tuple[float, float]:
    """Mean of a column on a level's own fine sample, and its standard error."""
    j = COLUMNS.index(column)
    return float(lev.mean("fine")[j]), float(np.sqrt(lev.var("fine")[j] / lev.n))


def summary(levels: list[mlmc.Level], ekjs_fine: tuple[float, float]) -> pl.DataFrame:
    rows: list[dict] = []

    def add(name: str, method: str, value: float, se: float = float("nan"), unit: str = "") -> None:
        rows.append({"name": name, "method": method, "value": value, "se": se, "unit": unit})

    top = levels[-1]
    m_top = 2**top.level
    pts, per, work = "points", "hedges/session", "hedges/session x pairs"
    # E[diff_m^2] - F^2 = O(1/m): diff_m = I + d_m with E[d_m^2] ~ k/m and E[I d_m] = O(1/m), so the
    # Richardson tail uses alpha = 1. The fitted alpha is reported as the check on that.
    est = mlmc.estimate(levels, column=0, alpha=1.0)
    fitted = mlmc.estimate(levels, column=0)
    floor, floor_se = _root(est.limit, est.limit_se)
    add("floor", "mlmc hedge, Richardson limit", floor, floor_se, pts)
    add("floor", f"mlmc hedge, E[P_L] at m={m_top}", *_root(est.value, est.se), pts)
    ek = mlmc.estimate(levels, column=COLUMNS.index("ekjs"), alpha=1.0)
    add("floor", "mlmc EKJS sum, Richardson limit", *_root(ek.limit, ek.limit_se), pts)
    add("floor", f"EKJS sum, single level m={EKJS_PER_SESSION}", *ekjs_fine, pts)
    exact = continuous_hedge.riemann_second_moment(WEIGHTS, SIGMA, SPOT, EKJS_PER_SESSION)
    add("floor", f"EKJS sum, exact at m={EKJS_PER_SESSION} (known answer)", np.sqrt(exact),
        unit=pts)
    closed = np.sqrt(continuous_hedge.floor_second_moment(WEIGHTS, SIGMA, SPOT))
    add("floor", "closed form (Gaussian kernels)", closed, unit=pts)
    # Control variate on the finest level: E[diff_L^2] = E[I_L^2] (exact) + E[diff_L^2 - I_L^2].
    exact_top = continuous_hedge.riemann_second_moment(WEIGHTS, SIGMA, SPOT, m_top)
    cv_p, cv_se = _fine(top, "cv")
    add("floor", f"control variate, E[P_L] at m={m_top}", *_root(exact_top + cv_p, cv_se), pts)
    gap = mlmc.estimate(levels, column=COLUMNS.index("cv"), alpha=1.0)
    add("hedge_minus_ekjs", "mlmc lim E[diff^2 - I^2] (theory: 0)", gap.limit, gap.limit_se,
        "points^2")

    add("alpha", "fit |E[Y_l]|, l>=2 (theory: 1)", fitted.alpha)
    add("beta", "fit Var[Y_l], l>=2 (theory: 1)", fitted.beta)
    add("gamma", "cost model", 1.0)
    bias = abs(top.mean("y")[0]) / (2.0**est.alpha - 1.0)
    add("bias_check", "|E[Y_L]|/(2^alpha-1) vs eps/sqrt2", bias,
        unit=f"points^2; eps/sqrt2={EPS / np.sqrt(2):.3f}")
    cost_ml = mlmc.mlmc_cost(levels, EPS)
    cost_sl = mlmc.single_level_cost(levels, EPS, fine_cost=m_top)
    cost_cv = 2.0 / EPS**2 * top.var("fine")[COLUMNS.index("cv")] * m_top
    add("cost", "mlmc", cost_ml, unit=work)
    add("cost", "single level at L", cost_sl, unit=work)
    add("cost", "control variate, single level at L", cost_cv, unit=work)
    add("cost_saving", "single / mlmc", cost_sl / cost_ml)
    add("cost_saving", "single / control variate", cost_sl / cost_cv)

    # Rate constants from the finest level's own sample (module docstring), and leading order.
    c_clock, c_clock_se = _root(*_fine(top, "clock"), m_top)
    c_clock_cf = continuous_hedge.rate_constant(WEIGHTS, SIGMA, SPOT, "clock")
    add("c_clock", f"sqrt(m E[clock^2]) at m={m_top}", c_clock, c_clock_se, pts)
    add("c_clock", "closed form, leading order", c_clock_cf, unit=pts)

    cal_p, cal_se = _fine(top, "calendar")
    c_cal, c_cal_se = _root(cal_p - est.limit, np.hypot(cal_se, est.limit_se), m_top)
    c_cal_cf = continuous_hedge.rate_constant(WEIGHTS, SIGMA, SPOT, "calendar")
    add("c_calendar", f"sqrt(m (E[cal^2] - F^2)) at m={m_top}", c_cal, c_cal_se, pts)
    add("c_calendar", "closed form, leading order (noise only)", c_cal_cf, unit=pts)

    # The difference's own noise around I: what keeps Var[Y_l] large at every level.
    c_diff_cf = continuous_hedge.rate_constant(WEIGHTS, SIGMA, SPOT, "difference")
    c_diff = _root(*_fine(top, "gap"), m_top)
    add("c_difference", f"sqrt(m E[(diff - I)^2]) at m={m_top}", *c_diff, pts)
    add("c_difference", "closed form, leading order", c_diff_cf, unit=pts)

    add("m_star_clock", "(c/F)^2, mlmc", (c_clock / floor) ** 2,
        _ratio_se(c_clock, c_clock_se, floor, floor_se), per)
    add("m_star_clock", "(c/F)^2, closed forms", (c_clock_cf / closed) ** 2, unit=per)
    add("m_star_calendar", "(c/F)^2, mlmc", (c_cal / floor) ** 2,
        _ratio_se(c_cal, c_cal_se, floor, floor_se), per)
    add("m_star_calendar", "(c/F)^2, closed forms", (c_cal_cf / closed) ** 2, unit=per)
    add("m_star_difference", "(c/F)^2, closed forms", (c_diff_cf / closed) ** 2, unit=per)

    # Economic units. Vega per vol point by central difference on the annual vol.
    def price(vol: float) -> float:
        return straddle_price(SPOT, SPOT, float((vol**2 / SESSIONS_PER_YEAR) * WEIGHTS.sum()))

    vega = (price(ANNUAL_VOL + 1e-4) - price(ANNUAL_VOL - 1e-4)) / 2e-4 / 100.0
    add("entry_premium", "Black-Scholes, 13% vol, 5 sessions", ENTRY, unit=pts)
    add("vega", "per vol point", vega, unit="points per vol point")
    add("floor_pct_premium", "mlmc floor / entry", 100 * floor / ENTRY, 100 * floor_se / ENTRY, "%")
    add("floor_vol_points", "mlmc floor / vega", floor / vega, floor_se / vega, "vol points")
    return pl.DataFrame(rows).with_columns(pl.col("value", "se").cast(pl.Float64))


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    levels = mlmc.run(sample_level, level_costs(), EPS, N_PILOT, SEED)
    levels_df = level_table(levels)
    summary_df = summary(levels, ekjs_floor(EKJS_PER_SESSION, EKJS_PAIRS, SEED + 1))

    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    extra = {
        "levels": LEVELS, "eps": EPS, "n_pilot": N_PILOT, "clock": TUE_REGIME,
        "session_order": list(SESSION_ORDER), "ekjs_per_session": EKJS_PER_SESSION,
        "ekjs_pairs": EKJS_PAIRS, "ekjs_seed": SEED + 1, "cost_bp": 0.0,
    }
    for name, table in (("mlmc_levels", levels_df), ("hedging_limit", summary_df)):
        path = cfg.tables_dir / f"{name}.parquet"
        table.write_parquet(path)
        write_manifest(path, manifest(f"hedging limit: {name}", cfg, seed=SEED, extra=extra))
        logging.info("wrote %s (%d rows)", path, table.height)

    shown = levels_df.filter(pl.col("quantity").is_in(["diff", "clock", "calendar"]))
    for row in shown.iter_rows(named=True):
        logging.info(
            "l=%d m=%-4d %-9s n=%-8d sd %8.3f  E[Y] %+10.4f  V[Y] %12.4g  %.1fs",
            row["level"], row["hedges_per_session"], row["quantity"], row["n_pairs"], row["sd"],
            row["mean_y"], row["var_y"], row["seconds"],
        )
    for row in summary_df.iter_rows(named=True):
        logging.info(
            "%-18s %-44s %12.4f +/- %-8.4f %s",
            row["name"], row["method"], row["value"], row["se"], row["unit"],
        )


if __name__ == "__main__":
    main()

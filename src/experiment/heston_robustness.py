"""Does the clock conclusion survive stochastic volatility and leverage? (D-12, D-24)

Time-changed Heston, swept over the two parameters this project never estimates. The clock scales
the variance the spot sees, never the grid (D-13), and ``v0 = theta`` forces both traders to the same
entry price. As vol-of-vol goes to zero the construction collapses exactly to the time-changed GBM
used for the headline, which is the cross-check in ``tests/test_sim.py``.

Read the ``share_of_sd`` column: how much of a correct-clock hedger's P&L standard deviation the
clock error adds. Under GBM that is 2.1 to 2.3% across the whole A7 grid. If it stays near there
across ``(rho, xi)``, the headline does not depend on assuming constant volatility.

Means are not interpretable in this table (D-24): the entry price is approximated, which shifts both
traders equally and cancels in every dispersion measure but not in a mean.

Run:  uv run python -m src.experiment.heston_robustness
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl
from scipy.stats import norm

from src.config import Settings, settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT, TUE_REGIME
from src.provenance import manifest, write_manifest
from src.sim.heston import PSI_C, HestonParams, straddle_price

KAPPA = 2.0
THETA = ANNUAL_VOL**2
RHOS = (0.0, -0.3, -0.6, -0.9)
XIS = (0.2, 0.5, 0.8)
REBALANCES_PER_SESSION = (1, 4)
SESSION_ORDER = ("Wed", "Thu", "Fri", "Mon", "Tue")
N_PATHS = 100_000
SEED = 23
#: Stands in for xi = 0 in the reference rows; small enough that the variance is deterministic.
XI_GBM_LIMIT = 1e-6


def _qe_step(
    v: np.ndarray, dt: float, p: HestonParams, rng: np.random.Generator
) -> np.ndarray:
    """One Andersen QE step of the variance process."""
    decay = np.exp(-p.kappa * dt)
    mean = p.theta + (v - p.theta) * decay
    var = (v * p.xi**2 * decay * (1.0 - decay)) / p.kappa + (
        p.theta * p.xi**2 * (1.0 - decay) ** 2
    ) / (2.0 * p.kappa)
    psi = var / np.maximum(mean**2, 1e-300)

    inv = 2.0 / np.maximum(psi, 1e-300)
    b2 = np.maximum(inv - 1.0 + np.sqrt(np.maximum(inv * (inv - 1.0), 0.0)), 0.0)
    a = mean / (1.0 + b2)
    quadratic = a * (np.sqrt(b2) + rng.standard_normal(v.size)) ** 2

    prob = np.clip((psi - 1.0) / (psi + 1.0), 0.0, 1.0 - 1e-15)
    beta = (1.0 - prob) / np.maximum(mean, 1e-300)
    unif = rng.random(v.size)
    exponential = np.where(
        unif <= prob, 0.0, np.log(np.maximum((1.0 - prob) / (1.0 - unif), 1e-300)) / beta
    )
    return np.where(psi < PSI_C, quadratic, exponential)


def simulate_clocked(
    p: HestonParams,
    spot: float,
    step_weights: np.ndarray,
    dt: float,
    n_paths: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Spot paths and the variance at the start of each step. See D-24 for the discretisation."""
    n_steps = step_weights.size
    paths = np.empty((n_paths, n_steps + 1))
    paths[:, 0] = spot
    variance = np.empty((n_paths, n_steps))
    v = np.full(n_paths, p.v0)
    log_spot = np.zeros(n_paths)

    for i, weight in enumerate(step_weights):
        variance[:, i] = v
        v_next = _qe_step(v, dt, p, rng)
        v_bar = 0.5 * (v + v_next)
        log_spot += (
            -0.5 * weight * v_bar * dt
            + (p.rho * np.sqrt(weight) / p.xi) * (v_next - v - p.kappa * (p.theta - v_bar) * dt)
            + np.sqrt(np.maximum((1.0 - p.rho**2) * weight * v_bar * dt, 0.0))
            * rng.standard_normal(n_paths)
        )
        paths[:, i + 1] = spot * np.exp(log_spot)
        v = v_next
    return paths, variance


def believed_remaining(
    step_weights: np.ndarray, dt: float, variance: np.ndarray, p: HestonParams
) -> np.ndarray:
    """Expected remaining variance at each step, per path. Closed form under Heston (D-24)."""
    n_steps = step_weights.size
    offsets = np.arange(n_steps + 1) * dt
    out = np.empty_like(variance)
    for i in range(n_steps):
        # From the start of step i to expiry, so step i is included: a hedger setting a delta now
        # faces this step's variance too. Summing from i+1 leaves both traders one step short and
        # inflates the hedging error, which is what the GBM-limit reference row caught.
        weights_tail = step_weights[i:]
        lags = offsets[i:n_steps] - offsets[i]
        decay = (np.exp(-p.kappa * lags) - np.exp(-p.kappa * (lags + dt))) / p.kappa
        const = float((weights_tail * p.theta * dt).sum())
        slope = float((weights_tail * decay).sum())
        out[:, i] = const + (variance[:, i] - p.theta) * slope
    return np.maximum(out, 1e-12)


def hedge(
    paths: np.ndarray, remaining: np.ndarray, strike: float, entry: float, cost_bp: float
) -> np.ndarray:
    """Short straddle, delta-hedged on a per-path believed remaining variance.

    Separate from ``src.sim.engine.hedge_short_straddle`` because there the hedger's belief is a
    deterministic schedule; here it moves with the simulated variance, which is the whole point.
    """
    cash = np.full(paths.shape[0], entry)
    position = np.zeros(paths.shape[0])
    for i in range(remaining.shape[1]):
        total_var = remaining[:, i]
        d1 = (np.log(paths[:, i] / strike) + 0.5 * total_var) / np.sqrt(total_var)
        target = -(2.0 * norm.cdf(d1) - 1.0)
        trade = target - position
        cash -= trade * paths[:, i] + np.abs(trade) * paths[:, i] * cost_bp * 1e-4
        position = target
    cash += position * paths[:, -1]
    return cash - np.abs(paths[:, -1] - strike)


def _cell(
    rho: float, xi: float, per_session: int, weights: np.ndarray, n_paths: int, seed: int
) -> dict:
    p = HestonParams(v0=THETA, kappa=KAPPA, theta=THETA, xi=xi, rho=rho)
    # Rate multipliers, not variance amounts: each session's weight applies to every sub-step.
    step_weights = np.repeat(weights, per_session)
    ttm = len(weights) / SESSIONS_PER_YEAR
    dt = ttm / step_weights.size

    paths, variance = simulate_clocked(p, SPOT, step_weights, dt, n_paths, np.random.default_rng(seed))
    remaining = {
        "clock": believed_remaining(step_weights, dt, variance, p),
        "calendar": believed_remaining(np.ones_like(step_weights), dt, variance, p),
    }
    entry = straddle_price(SPOT, SPOT, ttm, p)
    pnl = {name: hedge(paths, rem, SPOT, entry, 0.0) for name, rem in remaining.items()}
    diff = pnl["calendar"] - pnl["clock"]
    return {
        "rho": rho,
        "xi": xi,
        "kappa": KAPPA,
        "rebalances_per_session": per_session,
        "feller_ok": bool(2 * KAPPA * THETA >= xi**2),
        "clock_sd": float(pnl["clock"].std()),
        "calendar_sd": float(pnl["calendar"].std()),
        "diff_sd": float(diff.std()),
        "share_of_sd": float(diff.std() / pnl["clock"].std()),
        "clock_mean_not_interpretable": float(pnl["clock"].mean()),
        "min_variance_seen": float(variance.min()),
        "n_paths": n_paths,
    }


def run(cfg: Settings = settings, n_paths: int = N_PATHS, seed: int = SEED) -> pl.DataFrame:
    weights = np.array([TUE_REGIME[d] for d in SESSION_ORDER])
    weights = weights / weights.mean()
    rows = []
    for per_session in REBALANCES_PER_SESSION:
        # Reference row: vol-of-vol at zero must reproduce the time-changed GBM headline.
        rows.append(_cell(0.0, XI_GBM_LIMIT, per_session, weights, n_paths, seed))
        for rho in RHOS:
            for xi in XIS:
                rows.append(_cell(rho, xi, per_session, weights, n_paths, seed))
    return pl.DataFrame(rows)


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    table = run(cfg)
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.tables_dir / "heston_robustness.parquet"
    table.write_parquet(path)
    write_manifest(
        path,
        manifest(
            "Heston robustness sweep",
            cfg,
            seed=SEED,
            extra={
                "kappa": KAPPA,
                "theta": THETA,
                "rho_grid": list(RHOS),
                "xi_grid": list(XIS),
                "clock": TUE_REGIME,
            },
        ),
    )
    logging.info("wrote %s (%d cells)", path, table.height)
    logging.info("%-6s %-6s %-6s %-10s %-9s %-7s %s", "rho", "xi", "hedges", "clock sd", "diff sd", "share", "feller")
    for row in table.iter_rows(named=True):
        label = "GBM limit" if row["xi"] < 1e-3 else f"{row['xi']:.1f}"
        logging.info(
            "%-6.1f %-6s %-6d %-10.2f %-9.2f %-7.2f%% %s",
            row["rho"], label, row["rebalances_per_session"], row["clock_sd"],
            row["diff_sd"], 100 * row["share_of_sd"], row["feller_ok"],
        )
    shares = table.filter(pl.col("xi") > 1e-3)["share_of_sd"]
    logging.info("stochastic-vol cells: share spans %.2f%% to %.2f%%", 100 * shares.min(), 100 * shares.max())


if __name__ == "__main__":
    main()

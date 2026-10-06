"""The hedging result as a surface over rebalance frequency and trading cost (gate A7).

Both of those are researcher choices, so a single number invites the charge that it was the
flattering one. The whole grid is published instead, with provenance attached (D-19).

Reading it: ``share_of_sd`` is how much of a correct-clock hedger's P&L standard deviation the clock
error adds. On this sample it stays near 2%, because discrete rebalancing error swamps the clock.
That is the honest headline for the hedging experiment, and D-08c says so.

Run:  uv run python -m src.experiment.sensitivity
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl

from src.config import Settings, settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT, TUE_REGIME
from src.provenance import manifest, write_manifest
from src.sim.analytic import believed_paths
from src.sim.engine import hedge_short_straddle, simulate, straddle_price

REBALANCES_PER_SESSION = (1, 2, 4, 8)
COSTS_BP = (0.0, 0.5, 1.0, 2.0)
SESSION_ORDER = ("Wed", "Thu", "Fri", "Mon", "Tue")  # opened Wednesday, expiring Tuesday
N_PATHS = 200_000
SEED = 7


def _remaining(per_step: np.ndarray) -> np.ndarray:
    """Variance a hedger believes remains at the start of each step."""
    return per_step.sum() - np.concatenate([[0.0], np.cumsum(per_step)[:-1]])


def run(cfg: Settings = settings, n_paths: int = N_PATHS, seed: int = SEED) -> pl.DataFrame:
    sigma = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
    weights = np.array([TUE_REGIME[d] for d in SESSION_ORDER])
    weights = weights / weights.mean()

    rows = []
    for per_session in REBALANCES_PER_SESSION:
        # Per-step variance weights on the refined grid; both totals equal weights.sum(), which is
        # what makes the two traders enter at the same price (D-15).
        truth, flat = believed_paths(weights, per_session)
        entry = straddle_price(SPOT, SPOT, float(sigma**2 * weights.sum()))
        paths = simulate(truth, sigma, SPOT, n_paths, np.random.default_rng(seed))

        remaining = {
            "clock": sigma**2 * _remaining(truth),
            "calendar": sigma**2 * _remaining(flat),
        }
        for cost_bp in COSTS_BP:
            pnl = {
                name: hedge_short_straddle(paths, SPOT, rem, entry, cost_bp)
                for name, rem in remaining.items()
            }
            diff = pnl["calendar"] - pnl["clock"]
            rows.append(
                {
                    "rebalances_per_session": per_session,
                    "cost_bp": cost_bp,
                    "entry_price": entry,
                    "clock_mean": float(pnl["clock"].mean()),
                    "clock_sd": float(pnl["clock"].std()),
                    "calendar_mean": float(pnl["calendar"].mean()),
                    "calendar_sd": float(pnl["calendar"].std()),
                    "diff_mean": float(diff.mean()),
                    "diff_sd": float(diff.std()),
                    "share_of_sd": float(diff.std() / pnl["clock"].std()),
                    "clock_skew": float(
                        ((pnl["clock"] - pnl["clock"].mean()) ** 3).mean() / pnl["clock"].std() ** 3
                    ),
                    "clock_q05": float(np.percentile(pnl["clock"], 5)),
                    "clock_q95": float(np.percentile(pnl["clock"], 95)),
                    "n_paths": n_paths,
                }
            )
    return pl.DataFrame(rows)


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    table = run(cfg)
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.tables_dir / "sensitivity.parquet"
    table.write_parquet(path)
    write_manifest(
        path,
        manifest(
            "hedging sensitivity surface",
            cfg,
            seed=SEED,
            extra={
                "grid": {"rebalances_per_session": list(REBALANCES_PER_SESSION), "cost_bp": list(COSTS_BP)},
                "clock": TUE_REGIME,
                "session_order": list(SESSION_ORDER),
            },
        ),
    )
    logging.info("wrote %s (%d cells)", path, table.height)
    logging.info("%-10s %-8s %-10s %-10s %-10s", "rebal/sess", "cost bp", "clock sd", "diff sd", "share")
    for row in table.iter_rows(named=True):
        logging.info(
            "%-10d %-8.1f %-10.2f %-10.2f %.1f%%",
            row["rebalances_per_session"], row["cost_bp"], row["clock_sd"], row["diff_sd"],
            100 * row["share_of_sd"],
        )


if __name__ == "__main__":
    main()

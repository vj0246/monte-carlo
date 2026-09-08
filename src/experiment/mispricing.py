"""What the clock is worth: entry-price error, and hedging-error inflation.

Two experiments on the same fitted clock, because they answer different questions and only one of
them produces a large number.

**Hedging (D-15).** Short an at-the-money weekly straddle, delta-hedge once per session, force both
traders to enter at the same price so the whole difference lands in the risk outcome. Result: the
clock moves the P&L standard deviation by about 2% of its own size. Discrete rebalancing error
swamps it. This is the experiment the original plan made the headline, and on this sample it is
close to a null.

**Entry price.** A calendar-time trader misprices an option by the ratio between the clock variance
of the sessions it actually spans and the flat variance it assumes. Over a full week the weekday
mix averages out and the error vanishes. Over one session it does not: an option opened on a Monday
expiring the next day spans only the Tuesday weight, which the fitted Tuesday-regime clock puts at
0.61 of an average session.

So the clock is a one-day-to-expiry story, not a weekly one, and it shows up in the price rather
than in the hedge.

Run:  uv run python -m src.experiment.mispricing
"""

from __future__ import annotations

import logging

import numpy as np

from src.config import Settings, settings
from src.sim.engine import hedge_short_straddle, simulate, straddle_price

log = logging.getLogger(__name__)

#: Fitted Tuesday-regime weekday weights, mean one over the window (src.clock.estimate,
#: within-regime specification per D-08b). Tuesday is the expiry weekday.
TUE_REGIME = {"Mon": 1.459, "Tue": 0.607, "Wed": 1.093, "Thu": 0.928, "Fri": 0.880}

#: Sessions remaining from each opening weekday to the following Tuesday expiry.
SPANS = {
    "Mon": ["Tue"],
    "Fri": ["Mon", "Tue"],
    "Thu": ["Fri", "Mon", "Tue"],
    "Wed": ["Thu", "Fri", "Mon", "Tue"],
    "Tue": ["Wed", "Thu", "Fri", "Mon", "Tue"],
}

SPOT = 25_000.0
ANNUAL_VOL = 0.13
SESSIONS_PER_YEAR = 252


def entry_price_error(weights: dict[str, float] = TUE_REGIME) -> None:
    sigma = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
    mean_w = float(np.mean(list(weights.values())))
    log.info("--- entry-price error for a calendar-time trader ---")
    log.info("%-8s %-8s %10s %10s %11s", "opened", "sessions", "clock var", "flat var", "price err")
    for day in ("Mon", "Fri", "Thu", "Wed", "Tue"):
        span = SPANS[day]
        w_clock = sum(weights[d] for d in span) / mean_w
        w_flat = float(len(span))
        p_clock = straddle_price(SPOT, SPOT, sigma**2 * w_clock)
        p_flat = straddle_price(SPOT, SPOT, sigma**2 * w_flat)
        log.info(
            "%-8s %-8d %10.3f %10.1f %10.1f%%",
            day, len(span), w_clock, w_flat, 100 * (p_clock / p_flat - 1),
        )


def hedging_experiment(
    weights: dict[str, float] = TUE_REGIME, n_paths: int = 200_000, seed: int = 7
) -> None:
    sigma = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
    order = ["Wed", "Thu", "Fri", "Mon", "Tue"]  # opened Wednesday, expiring Tuesday
    w = np.array([weights[d] for d in order])
    w = w / w.mean()
    total_var = float((sigma**2 * w).sum())
    entry = straddle_price(SPOT, SPOT, total_var)

    rng = np.random.default_rng(seed)
    paths = simulate(w, sigma, SPOT, n_paths, rng)  # the world follows the clock

    n = len(w)
    remaining = {
        "clock": np.array([(sigma**2 * w[i:]).sum() for i in range(n)]),
        # Same total variance, spread evenly: this is the calendar-time trader, and the equal total
        # is why both enter at the same price (D-15).
        "calendar": np.array([total_var * (n - i) / n for i in range(n)]),
    }

    log.info("--- delta-hedged short straddle, entry %.2f, %d paths ---", entry, n_paths)
    log.info("%9s %-9s %9s %9s %8s %10s", "cost(bp)", "trader", "mean", "sd", "skew", "p05")
    for cost in (0.0, 0.5, 2.0):
        pnl = {}
        for name, rem in remaining.items():
            pnl[name] = hedge_short_straddle(paths, SPOT, rem, entry, cost)
            skew = float(((pnl[name] - pnl[name].mean()) ** 3).mean() / pnl[name].std() ** 3)
            log.info(
                "%9.1f %-9s %9.2f %9.2f %8.2f %10.2f",
                cost, name, pnl[name].mean(), pnl[name].std(), skew, np.percentile(pnl[name], 5),
            )
        diff = pnl["calendar"] - pnl["clock"]
        log.info(
            "%9s %-9s %9.2f %9.2f   share of hedging-error sd: %.1f%%",
            "", "diff", diff.mean(), diff.std(), 100 * diff.std() / pnl["clock"].std(),
        )


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    entry_price_error()
    hedging_experiment()


if __name__ == "__main__":
    main()

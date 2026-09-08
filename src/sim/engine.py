"""Time-changed GBM paths and delta-hedging, on a session grid.

The clock is applied to the variance increment, not to the grid spacing (D-13): every path uses the
same uniform one-session step, and only the variance assigned to that step changes. That keeps
discretisation error identical between two traders who disagree about the clock, so the difference
between them measures the clock and nothing else.

Rates and dividends are zero throughout. D-05 measured the discount factor to be unidentifiable
from this data and the forward to be insensitive to it, so carrying a rate here would add a
parameter that changes nothing.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def simulate(
    weights: np.ndarray, sigma: float, spot: float, n_paths: int, rng: np.random.Generator
) -> np.ndarray:
    """Paths on a session grid. ``weights[i]`` scales the variance of session ``i``.

    Returns an ``(n_paths, len(weights) + 1)`` array whose first column is ``spot``.
    """
    var = sigma**2 * weights
    shocks = rng.standard_normal((n_paths, len(weights)))
    # Antithetic pairing (D-14): the second half mirrors the first, so the sample mean of every
    # odd moment of the driving noise is exact.
    half = n_paths // 2
    shocks[half:] = -shocks[:half]
    steps = -0.5 * var + np.sqrt(var) * shocks
    return spot * np.exp(np.concatenate([np.zeros((n_paths, 1)), np.cumsum(steps, axis=1)], axis=1))


def straddle_price(fwd: float, strike: float, total_var: float) -> float:
    sd = np.sqrt(total_var)
    d1 = (np.log(fwd / strike) + 0.5 * total_var) / sd
    d2 = d1 - sd
    call = fwd * norm.cdf(d1) - strike * norm.cdf(d2)
    put = strike * norm.cdf(-d2) - fwd * norm.cdf(-d1)
    return call + put


def straddle_delta(fwd: np.ndarray, strike: float, total_var: float) -> np.ndarray:
    """Delta of a long straddle. ``total_var`` is the variance the hedger believes remains."""
    if total_var <= 0:
        return np.where(fwd > strike, 1.0, -1.0)
    sd = np.sqrt(total_var)
    d1 = (np.log(fwd / strike) + 0.5 * total_var) / sd
    return 2.0 * norm.cdf(d1) - 1.0


def hedge_short_straddle(
    paths: np.ndarray, strike: float, remaining_var: np.ndarray, entry_price: float, cost_bp: float
) -> np.ndarray:
    """P&L of shorting one straddle at ``entry_price`` and delta-hedging once per session.

    ``remaining_var[i]`` is the variance the hedger *believes* remains at the start of session
    ``i``. Two traders differ only in this vector, which is the whole experiment (D-15).
    """
    n_paths, n_points = paths.shape
    n_sessions = n_points - 1
    cash = np.full(n_paths, entry_price)
    position = np.zeros(n_paths)  # units of the underlying held as a hedge

    for i in range(n_sessions):
        target = -straddle_delta(paths[:, i], strike, remaining_var[i])
        trade = target - position
        cash -= trade * paths[:, i]
        cash -= np.abs(trade) * paths[:, i] * cost_bp * 1e-4
        position = target

    cash += position * paths[:, -1]
    payoff = np.abs(paths[:, -1] - strike)
    return cash - payoff

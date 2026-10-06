"""The El Karoui-Jeanblanc-Shreve benchmark for hedging under a deterministic variance clock.

D-16: hedging at the wrong volatility has a closed form, so the simulation has to be validated
against it rather than treated as the only way to get the answer. For a short straddle hedged on a
believed variance path while the world realises another,

    E[P&L] = sum_i E[ 0.5 * S^2 * Gamma_i ] * (dV_believed_i - dV_realised_i)

with Gamma evaluated at the variance the hedger *believes* remains. Both traders enter at the same
price, so the two variance paths have equal totals and the bracket is a signed measure summing to
zero: the result is driven entirely by how gamma weights it, which is the project's central claim.

``expected_half_s2_gamma`` is that expectation in closed form, so the benchmark needs no simulation
of its own. Gate A6 checks the simulator against it, and checks that a trader using the right clock
has mean zero, which the same formula returns identically.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def expected_half_s2_gamma(
    spot: float, strike: float, var_elapsed: float, var_remaining: float
) -> float:
    """``E[0.5 * S^2 * Gamma]`` for a long straddle, under zero rates.

    ``var_elapsed`` is the variance the world has realised up to now, which is what spreads the
    spot; ``var_remaining`` is what the hedger believes is left, which is what sets gamma. Gamma of
    a straddle is twice a call's, so ``0.5 * S^2 * Gamma`` reduces to ``S * phi(d1) / sqrt(w)``, and
    the expectation of that against a lognormal spot is a Gaussian integral.
    """
    w = var_remaining
    if w <= 0.0:
        return 0.0
    if var_elapsed <= 0.0:
        d1 = (np.log(spot / strike) + 0.5 * w) / np.sqrt(w)
        return float(spot * norm.pdf(d1) / np.sqrt(w))

    v = var_elapsed
    mu = np.log(spot / strike) - 0.5 * v
    a = 0.5 * (1.0 / v + 1.0 / w)
    b = mu / v + 0.5
    c = -(mu**2) / (2.0 * v) - w / 8.0
    integral = (1.0 / np.sqrt(2.0 * np.pi * v)) * np.sqrt(np.pi / a) * np.exp(b**2 / (4.0 * a) + c)
    return float(strike / (np.sqrt(w) * np.sqrt(2.0 * np.pi)) * integral)


def ekjs_short_straddle_mean(
    spot: float, strike: float, realised: np.ndarray, believed: np.ndarray
) -> float:
    """Expected P&L of shorting a straddle and hedging on ``believed`` while ``realised`` happens.

    Both arrays are per-step variance on the same grid. Equal totals mean a correct-clock hedger
    gets exactly zero, which is the other half of gate A6.
    """
    realised = np.asarray(realised, dtype=float)
    believed = np.asarray(believed, dtype=float)
    if realised.shape != believed.shape:
        raise ValueError("realised and believed variance paths must share a grid")

    remaining_believed = believed.sum() - np.concatenate([[0.0], np.cumsum(believed)[:-1]])
    elapsed = np.concatenate([[0.0], np.cumsum(realised)[:-1]])
    return float(
        sum(
            expected_half_s2_gamma(spot, strike, elapsed[i], remaining_believed[i])
            * (believed[i] - realised[i])
            for i in range(len(realised))
        )
    )


def refine(weights: np.ndarray, per_session: int) -> np.ndarray:
    """Split each session into ``per_session`` equal-variance steps, total unchanged."""
    return np.repeat(np.asarray(weights, dtype=float) / per_session, per_session)


def believed_paths(weights: np.ndarray, per_session: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-step variance for the clock-aware and the calendar-time trader on a refined grid.

    Totals are equal by construction, which is what forces both to enter at the same price (D-15).
    """
    truth = refine(weights, per_session)
    flat = np.full(truth.shape, truth.sum() / truth.size)
    return truth, flat

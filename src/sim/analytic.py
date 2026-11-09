"""The El Karoui-Jeanblanc-Shreve benchmark for hedging under a deterministic variance clock.

D-16: hedging at the wrong volatility has a closed form, so the simulation has to be validated
against it rather than treated as the only way to get the answer. For a short straddle hedged on a
believed variance path while the world realises another,

    E[P&L] = sum_i E[ 0.5 * S^2 * Gamma_i ] * (dV_believed_i - dV_realised_i)

with Gamma evaluated at the variance the hedger *believes* remains. Both traders enter at the same
price, so the two variance paths have equal totals and the bracket is a signed measure summing to
zero: the result is driven entirely by how gamma weights it, which is the project's central claim.

``expected_half_s2_gamma`` is that expectation in closed form, so the benchmark needs no simulation
of its own.

**What the mean actually is (D-25).** Under zero rates the spot is a martingale, so for *any*
adapted hedge ``E[sum delta_i dS_i] = 0``, and the entry price equals ``E[payoff]`` because both
traders agree on total variance. The exact mean P&L is therefore zero on every grid, for both
traders. ``ekjs_short_straddle_mean`` is the expectation of the second-order (gamma) proxy, not of
the P&L; its value (-0.699 at one hedge per session) is the proxy's Riemann-sum bias. In continuous
time it is exactly zero: the expected dollar gamma depends only on realised-elapsed plus
believed-remaining variance, ``g(V_total - D)`` with ``D = V_believed - V_realised``, so the EKJS mean
is ``integral g(V_total - D) dD``, an exact differential between ``D(0) = D(T) = 0``.

Gate A6 therefore tests the *pathwise* identity, which carries real information: along each path
the simulated P&L difference converges in L2 to ``ekjs_pathwise``.
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
    """Expectation of the gamma proxy for hedging on ``believed`` while ``realised`` happens.

    Not the exact mean P&L, which is zero (module docstring). Both arrays are per-step variance on
    the same grid; equal arrays give exactly zero.
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


def ekjs_pathwise(
    paths: np.ndarray, strike: float, realised: np.ndarray, believed: np.ndarray
) -> np.ndarray:
    """Per-path EKJS integral ``sum_i 0.5 * S_i^2 * Gamma_i * (believed_i - realised_i)``.

    Gamma is the straddle's at the variance the hedger believes remains at the start of step ``i``
    (step ``i`` included). For a short straddle hedged on ``believed`` this is the continuous-time
    P&L; a hedger on the true clock gets zero, so it is also the limit of the P&L difference.
    """
    remaining = believed.sum() - np.concatenate([[0.0], np.cumsum(believed)[:-1]])
    spot = paths[:, :-1]
    d1 = (np.log(spot / strike) + 0.5 * remaining) / np.sqrt(remaining)
    half_s2_gamma = spot * norm.pdf(d1) / np.sqrt(remaining)
    return (half_s2_gamma * (believed - realised)).sum(axis=1)


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

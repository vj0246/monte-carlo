"""Unit tests for the simulation layer. Fast, no data dependency, fixed seeds."""

from __future__ import annotations

import numpy as np
import pytest

from src.experiment.heston_robustness import XI_GBM_LIMIT, sweep_cell
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT, TUE_REGIME
from src.iv.invert import black76_undiscounted
from src.sim.analytic import believed_paths, ekjs_short_straddle_mean, expected_half_s2_gamma
from src.sim.engine import hedge_short_straddle, simulate, straddle_price
from src.sim.heston import HestonParams, call_price, simulate_qe


def test_fourier_price_collapses_to_black76() -> None:
    """With vol-of-vol near zero and v0 == theta, Heston is Black-76 at sqrt(v0).

    Tolerance is loose on purpose: the C term carries a kappa*theta/xi^2 prefactor, so the limit is
    numerically ill-conditioned in exactly the direction that makes it checkable. It still catches a
    wrong characteristic function, which would be wrong by percent, not by 1e-4.
    """
    p = HestonParams(v0=0.04, kappa=1.0, theta=0.04, xi=1e-3, rho=0.0)
    for ttm in (0.1, 0.5, 1.0):
        for strike in (85.0, 100.0, 115.0):
            got = call_price(100.0, strike, ttm, p)
            want = float(black76_undiscounted(np.sqrt(0.04), 100.0, strike, ttm, True))
            assert got == pytest.approx(want, rel=2e-3, abs=2e-3)


def test_qe_variance_mean_reverts_to_theta() -> None:
    p = HestonParams(v0=0.09, kappa=2.0, theta=0.04, xi=0.6, rho=-0.5)
    ttm = 1.0
    _, variance = simulate_qe(p, 100.0, ttm, 256, 200_000, np.random.default_rng(0))
    want = p.theta + (p.v0 - p.theta) * np.exp(-p.kappa * ttm)
    assert variance.mean() == pytest.approx(want, rel=0.02)
    assert (variance >= 0).all(), "QE must never return a negative variance"


def test_expected_half_s2_gamma_matches_brute_force() -> None:
    rng = np.random.default_rng(3)
    for var_elapsed, var_remaining, spot, strike in (
        (0.0004, 0.0010, 25_000.0, 25_000.0),
        (0.0020, 0.0005, 25_000.0, 25_200.0),
        (0.0050, 0.0040, 100.0, 100.0),
    ):
        z = rng.standard_normal(2_000_000)
        path = spot * np.exp(-0.5 * var_elapsed + np.sqrt(var_elapsed) * z)
        d1 = (np.log(path / strike) + 0.5 * var_remaining) / np.sqrt(var_remaining)
        sample = path * np.exp(-0.5 * d1**2) / np.sqrt(2 * np.pi) / np.sqrt(var_remaining)
        se = sample.std(ddof=1) / np.sqrt(sample.size)
        got = expected_half_s2_gamma(spot, strike, var_elapsed, var_remaining)
        assert abs(got - sample.mean()) < 4 * se


def test_expected_half_s2_gamma_deterministic_spot_limit() -> None:
    """Zero elapsed variance is a separate code path; it must agree with the limit of the other."""
    spot, strike, w = 25_000.0, 24_800.0, 0.002
    tiny = expected_half_s2_gamma(spot, strike, 1e-12, w)
    exact = expected_half_s2_gamma(spot, strike, 0.0, w)
    assert tiny == pytest.approx(exact, rel=1e-6)


def test_correct_clock_benchmark_is_exactly_zero() -> None:
    weights = np.array([1.1, 0.934, 0.886, 1.469, 0.611])
    truth, _ = believed_paths(weights / weights.mean(), 4)
    assert ekjs_short_straddle_mean(25_000.0, 25_000.0, truth, truth) == pytest.approx(0.0, abs=1e-12)


def test_refined_grids_preserve_total_variance() -> None:
    weights = np.array([1.1, 0.934, 0.886, 1.469, 0.611])
    for per_session in (1, 4, 16):
        truth, flat = believed_paths(weights, per_session)
        assert truth.sum() == pytest.approx(weights.sum())
        assert flat.sum() == pytest.approx(weights.sum())
        assert truth.size == weights.size * per_session


def test_heston_gbm_limit_matches_the_time_changed_gbm_engine() -> None:
    """Vol-of-vol at zero must collapse the Heston sweep onto the engine behind the headline.

    This is the check that caught a real bug rather than a hypothetical one: believed remaining
    variance had excluded the step about to happen, which inflated the clock standard deviation from
    497 to 540 and the clock share from 2.3% to 3.5%. Both constructions are plausible on their own;
    only the comparison exposed it.
    """
    order = ("Wed", "Thu", "Fri", "Mon", "Tue")
    weights = np.array([TUE_REGIME[d] for d in order])
    weights = weights / weights.mean()
    n_paths, seed = 50_000, 23

    cell = sweep_cell(0.0, XI_GBM_LIMIT, 1, weights, n_paths, seed)

    sigma = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
    truth, flat = believed_paths(weights, 1)

    def remaining(per_step: np.ndarray) -> np.ndarray:
        return per_step.sum() - np.concatenate([[0.0], np.cumsum(per_step)[:-1]])

    entry = straddle_price(SPOT, SPOT, float(sigma**2 * weights.sum()))
    paths = simulate(truth, sigma, SPOT, n_paths, np.random.default_rng(seed))
    clock = hedge_short_straddle(paths, SPOT, sigma**2 * remaining(truth), entry, 0.0)
    calendar = hedge_short_straddle(paths, SPOT, sigma**2 * remaining(flat), entry, 0.0)

    assert cell["clock_sd"] == pytest.approx(clock.std(), rel=0.02)
    assert abs(cell["share_of_sd"] - (calendar - clock).std() / clock.std()) < 0.004

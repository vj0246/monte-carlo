"""Known-answer tests for the clocked Heston pricer, its expansion, and the terminal estimators."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from src.experiment.heston_entry import correction
from src.experiment.heston_robustness import simulate_clocked
from src.sim import heston, heston_clock, terminal_mc
from src.sim.engine import straddle_price
from src.sim.heston import HestonParams

THETA = 0.13**2
DT = 1.0 / 252
FWD = 25_000.0
#: Tuesday-regime clock, mean one: Fri, Mon, Tue and the Monday spread's far leg.
TWO_SESSIONS = np.array([1.469, 0.611])
SIX_SESSIONS = np.array([0.611, 1.100, 0.934, 0.886, 1.469, 0.611])
U = np.concatenate([np.linspace(-300.0, 300.0, 61), [1500.0, 4000.0]]) - 0.5j


def params(rho: float, xi: float) -> HestonParams:
    return HestonParams(v0=THETA, kappa=2.0, theta=THETA, xi=xi, rho=rho)


@pytest.mark.parametrize("rho, xi", [(0.0, 0.5), (-0.6, 0.5), (-0.9, 0.8)])
def test_unit_clock_reproduces_plain_heston(rho: float, xi: float) -> None:
    p = params(rho, xi)
    got = heston_clock.char_fn(U, p, np.ones(5), DT)
    want = heston.char_fn(U, p, 5 * DT)
    assert np.abs(got - want).max() < 1e-12


def test_splitting_a_piece_changes_nothing() -> None:
    p = params(-0.9, 0.8)
    whole = heston_clock.char_fn(U, p, TWO_SESSIONS, DT)
    split = heston_clock.char_fn(U, p, np.repeat(TWO_SESSIONS, 4), DT / 4)
    assert np.abs(whole - split).max() < 1e-12


def test_closed_form_matches_numerical_riccati() -> None:
    """Integrate the D-24 Riccati pair backward from expiry with a complex RK solver."""
    p = params(-0.6, 0.8)
    u = np.array([3.0 - 0.5j, 40.0 - 0.5j, -150.0 - 0.5j, 400.0 - 0.5j])

    def rhs(_tau: float, y: np.ndarray, w: float) -> np.ndarray:
        b = y[len(u):]
        db = (0.5 * w * (-(u**2) - 1j * u) + (p.rho * p.xi * np.sqrt(w) * 1j * u - p.kappa) * b
              + 0.5 * p.xi**2 * b**2)
        return np.concatenate([p.kappa * p.theta * b, db])

    y = np.zeros(2 * len(u), dtype=complex)
    for w in reversed(SIX_SESSIONS):
        y = solve_ivp(rhs, (0.0, DT), y, args=(w,), method="DOP853", rtol=1e-12, atol=1e-14).y[:, -1]
    numerical = np.exp(y[: len(u)] + y[len(u):] * p.v0)
    assert np.abs(heston_clock.char_fn(u, p, SIX_SESSIONS, DT) - numerical).max() < 1e-9


@pytest.mark.parametrize("weights", [np.array([0.611]), TWO_SESSIONS, SIX_SESSIONS])
def test_vanishing_vol_of_vol_is_black76(weights: np.ndarray) -> None:
    """``xi = 1e-3``: the true gap is ``~xi^2 T / (24 theta)`` relative, 1e-8; smaller xi only adds
    rounding through the ``1/xi^2`` prefactor."""
    got = heston_clock.straddle_price(FWD, FWD, weights, DT, params(0.0, 1e-3))
    want = straddle_price(FWD, FWD, THETA * DT * weights.sum())
    assert got == pytest.approx(want, rel=2e-7)


@pytest.mark.parametrize("rho, xi", [(0.0, 0.5), (-0.6, 0.5), (-0.9, 0.8)])
def test_edgeworth_correction_tracks_the_exact_price(rho: float, xi: float) -> None:
    """The derived factor must capture the stochastic-vol correction to within 10% of itself."""
    for weights in (np.array([0.611]), TWO_SESSIONS, SIX_SESSIONS[1:]):
        exact = heston_clock.straddle_price(FWD, FWD, weights, DT, params(rho, xi))
        black = straddle_price(FWD, FWD, THETA * DT * weights.sum())
        assert correction(weights, params(rho, xi)) - 1 == pytest.approx(exact / black - 1, rel=0.1)


@pytest.mark.slow
@pytest.mark.parametrize("weights", [np.array([0.611]), TWO_SESSIONS])
def test_clocked_qe_simulation_matches_fourier(weights: np.ndarray) -> None:
    """Independent check: the D-24 simulator, not the terminal estimators, against Fourier."""
    p = params(-0.9, 0.8)
    step_weights = np.repeat(weights, 8)
    payoffs = []
    for seed in range(4):
        paths, _ = simulate_clocked(p, FWD, step_weights, DT / 8, 100_000, np.random.default_rng(seed))
        payoffs.append(np.abs(paths[:, -1] - FWD))
    payoff = np.concatenate(payoffs)
    se = payoff.std(ddof=1) / np.sqrt(payoff.size)
    exact = heston_clock.straddle_price(FWD, FWD, weights, DT, p)
    assert abs(payoff.mean() - exact) < 3 * se


def test_pca_increments_are_orthogonal() -> None:
    m = terminal_mc.pca_increments(48)
    assert np.abs(m @ m.T - np.eye(48)).max() < 1e-12


def test_single_normal_qe_has_the_right_conditional_mean() -> None:
    """QE matches ``E[v_{t+dt} | v_t]`` exactly; both branches must survive driving by one normal."""
    p = params(-0.9, 0.8)
    z = np.random.default_rng(3).standard_normal(400_000)
    for dt, v in ((DT / 8, THETA), (DT, 0.02 * THETA)):  # quadratic branch, then exponential
        nxt = terminal_mc.qe_step(np.full(z.size, v), dt, p, z)
        want = p.theta + (v - p.theta) * np.exp(-p.kappa * dt)
        assert nxt.mean() == pytest.approx(want, rel=5 * nxt.std() / want / np.sqrt(z.size))


def test_control_variate_is_exact_in_the_gbm_limit() -> None:
    """As ``xi -> 0``, ``I -> J`` and ``V -> theta int w``, so the controlled estimator collapses to
    Black-76 on every sample, at any ``rho``."""
    step_weights = np.repeat(TWO_SESSIONS, 8)
    got = terminal_mc.estimate("conditional_cv", ((16, 1.0),), params(-0.6, 1e-6), step_weights,
                               DT / 8, FWD, 4096, 0)
    assert got == pytest.approx(straddle_price(FWD, FWD, THETA * DT * TWO_SESSIONS.sum()), rel=1e-6)


@pytest.mark.slow
@pytest.mark.parametrize("method", list(terminal_mc.METHODS))
def test_every_estimator_is_unbiased_on_the_monday_spread(method: str) -> None:
    """Long next week's straddle, short tomorrow's: two legs on one path, against Fourier."""
    p = params(-0.6, 0.5)
    step_weights = np.repeat(SIX_SESSIONS, 4)
    legs = ((24, 1.0), (4, -1.0))
    est = np.array([terminal_mc.estimate(method, legs, p, step_weights, DT / 4, FWD, 2**12, s)
                    for s in range(16)])
    exact = (heston_clock.straddle_price(FWD, FWD, SIX_SESSIONS, DT, p)
             - heston_clock.straddle_price(FWD, FWD, SIX_SESSIONS[:1], DT, p))
    # 4 SE plus 0.02 index points of QE discretisation bias at 4 steps per session.
    assert abs(est.mean() - exact) < 4 * est.std(ddof=1) / 4 + 0.02

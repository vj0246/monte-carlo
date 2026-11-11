"""Known-answer tests for the MLMC estimator, the continuous-hedging closed forms, and the coupled
sampler of the hedging-limit experiment (D-27). Fixed seeds throughout."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.integrate import quad

from src.experiment import hedging_limit as hl
from src.sim import continuous_hedge as ch
from src.sim import mlmc

TOY_VAR = 1.0  # total variance of the toy GBM log path


def _toy_sampler(level: int, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """``P_l = (sum of squared log increments on 2^l steps)^2``; the coarse grid sums pairs of the
    same increments, so the coupling is exact."""
    steps = 2**level
    v = TOY_VAR / steps
    dx = -0.5 * v + np.sqrt(v) * rng.standard_normal((n, steps))
    fine = (dx**2).sum(axis=1) ** 2
    if level == 0:
        return fine[:, None], np.zeros((n, 1))
    coarse = ((dx[:, ::2] + dx[:, 1::2]) ** 2).sum(axis=1) ** 2
    return fine[:, None], coarse[:, None]


def _toy_exact(steps: int) -> float:
    """``E[Q^2] = Var Q + (E Q)^2`` with ``dx ~ N(-v/2, v)``: ``Var Q = n (2 v^2 + v^3)``,
    ``E Q = n (v + v^2 / 4)``. Tends to ``V^2`` like ``2.5 V^2 / n`` for ``V = 1``."""
    v = TOY_VAR / steps
    return steps * (2 * v**2 + v**3) + (steps * (v + v**2 / 4)) ** 2


def test_mlmc_recovers_closed_form_on_toy() -> None:
    n_levels = 7
    costs = 2.0 ** np.arange(n_levels)
    levels = mlmc.run(_toy_sampler, costs, eps=0.03, n_pilot=2_000, seed=11)
    est = mlmc.estimate(levels)

    assert abs(est.value - _toy_exact(2 ** (n_levels - 1))) < 4 * est.se
    # The Richardson limit leaves the O(n^-2) term: -2.125 V^2 / n^2 for alpha = 1.
    assert abs(est.limit - TOY_VAR**2) < 4 * est.limit_se + 2.2 / 4 ** (n_levels - 1)
    assert est.alpha == pytest.approx(1.0, abs=0.25)
    # Var[Y_l] -> 8 V^4 / n (beta = 1), but from above: 2^l Var[Y_l] is 65, 28, 16, 12, 10 at
    # l = 2..6, so the fitted beta over those levels is pre-asymptotically larger than 1.
    assert 0.9 < est.beta < 2.0
    assert est.se < 0.03 / np.sqrt(2) * 1.2, "allocation must meet the variance half of the budget"


@pytest.mark.parametrize("alpha", [1.0, 2.0])
def test_richardson_tail_and_its_se_are_exact(alpha: float) -> None:
    """Deterministic levels: ``Y_0 = 1``, ``Y_l = r^l`` with ``r = 2^-alpha``, plus a +/-s pattern
    of known variance. The limit must be the geometric sum ``1 / (1 - r)`` to rounding, and its se
    must count level L's variance ``(2^alpha / (2^alpha - 1))^2`` times. A missing tail misses the
    limit by ``r^(L+1) / (1 - r)`` (0.03 at alpha = 1), a wrong-sign tail by twice that."""
    r, n_levels, n, s = 2.0**-alpha, 6, 10, 0.1
    pattern = s * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)[:, None]
    levels = []
    for level in range(n_levels):
        p_l = (1.0 - r ** (level + 1)) / (1.0 - r)
        p_prev = (1.0 - r**level) / (1.0 - r)
        lev = mlmc.Level(level, 2.0**level)
        lev.add(np.full((n, 1), p_l) + pattern, np.full((n, 1), p_prev), 0.0)
        levels.append(lev)
    est = mlmc.estimate(levels, alpha=alpha)
    assert est.limit == pytest.approx(1.0 / (1.0 - r), abs=1e-12)
    var_y = s**2 * n / (n - 1)
    inflate = (2.0**alpha / (2.0**alpha - 1.0)) ** 2
    assert est.limit_se == pytest.approx(np.sqrt((n_levels - 1 + inflate) * var_y / n), rel=1e-9)
    assert mlmc.estimate(levels).alpha == pytest.approx(alpha, abs=1e-9)


def test_optimal_samples_spend_half_the_mse_budget() -> None:
    variances = np.array([50.0, 20.0, 9.0, 4.0, 1.5])
    costs = 2.0 ** np.arange(5)
    eps = 0.1
    n = mlmc.optimal_samples(variances, costs, eps)
    spent = np.sum(variances / n)
    assert 0.99 * eps**2 / 2 < spent <= eps**2 / 2
    # Lagrange condition: N_l proportional to sqrt(V_l / C_l).
    ratio = n / np.sqrt(variances / costs)
    assert ratio.max() / ratio.min() == pytest.approx(1.0, abs=1e-3)


def test_floor_mean_is_exactly_zero() -> None:
    """E[I] = 0 in continuous time (an exact differential, D-25); the grid must reproduce it."""
    scale = np.sqrt(ch.floor_second_moment(hl.WEIGHTS, hl.SIGMA, hl.SPOT))
    assert abs(ch.floor_mean(hl.WEIGHTS, hl.SIGMA, hl.SPOT)) < 1e-10 * scale


def test_floor_quadrature_is_converged() -> None:
    coarse = ch.floor_second_moment(hl.WEIGHTS, hl.SIGMA, hl.SPOT, nodes=100)
    fine = ch.floor_second_moment(hl.WEIGHTS, hl.SIGMA, hl.SPOT, nodes=400)
    assert coarse == pytest.approx(fine, rel=1e-4)


def test_riemann_moment_converges_to_floor_at_rate_one() -> None:
    """The exact discrete E[I_m^2] approaches the continuous one like a/m: halving the step halves
    the gap, and one Richardson step closes it to well under 0.1%."""
    floor2 = ch.floor_second_moment(hl.WEIGHTS, hl.SIGMA, hl.SPOT, nodes=400)
    e128, e256 = (ch.riemann_second_moment(hl.WEIGHTS, hl.SIGMA, hl.SPOT, m) for m in (128, 256))
    assert (floor2 - e128) / (floor2 - e256) == pytest.approx(2.0, abs=0.15)
    assert 2 * e256 - e128 == pytest.approx(floor2, rel=1e-3)


@pytest.mark.parametrize("believer", ["clock", "calendar"])
def test_rate_constant_quadrature_is_converged(believer: str) -> None:
    """Same closed-form integrand, integrated by QUADPACK in t per session instead of Gauss-Legendre
    in u. This checks the quadrature, not the formula; the formula's checks are the simulated
    hedges below."""
    w, sigma, strike = hl.WEIGHTS, hl.SIGMA, hl.SPOT
    cum = np.concatenate([[0.0], np.cumsum(w)])
    total = sigma**2 * cum[-1]
    horizon = len(w)

    def integrand(t: float, i: int) -> float:
        v = sigma**2 * (cum[i] + w[i] * (t - i))
        rem = total - v if believer == "clock" else total * (horizon - t) / horizon
        e_g2 = strike**2 * np.exp(-0.125 * (rem + v) ** 2 / (v + 0.5 * rem)) / np.sqrt(
            2 * np.pi * (v + 0.5 * rem)
        ) / (2 * np.sqrt(np.pi * rem))
        return 2 * sigma**4 * e_g2 * w[i] ** 2

    c2 = sum(quad(integrand, i, i + 1, args=(i,), limit=200)[0] for i in range(horizon))
    assert ch.rate_constant(w, sigma, strike, believer) == pytest.approx(np.sqrt(c2), rel=1e-6)


def test_pair_kernel_matches_numerical_integral() -> None:
    """``k`` at two times, and at one time with two different remaining variances (the cross term
    of the "difference" constant), against integrating the Gaussians over ``x`` directly."""
    v_s, d, r_s, r_t = 0.002, 0.001, 0.003, 0.0015

    def n(x: float, mean: float, var: float) -> float:
        return float(np.exp(-0.5 * (x - mean) ** 2 / var) / np.sqrt(2 * np.pi * var))

    lim = 12 * np.sqrt(v_s + d)
    # Two times: x_t = x_s + N(-d/2, d), so the inner expectation is itself a Gaussian density.
    def expect(r_later: float, shift: float) -> float:
        def f(x: float) -> float:
            later = n(x, (r_later + shift) / 2, r_later + shift)
            return n(x, r_s / 2, r_s) * later * n(x, -v_s / 2, v_s)

        return quad(f, -lim, lim)[0]

    def kernel(*values: float) -> float:
        return float(ch.pair_kernel(*[np.array([a]) for a in values])[0])

    assert kernel(v_s, r_s, v_s + d, r_t) == pytest.approx(expect(r_t, d), rel=1e-8)
    assert kernel(v_s, r_s, v_s, r_t) == pytest.approx(expect(r_t, 0.0), rel=1e-8)


def test_uniform_clock_has_no_floor() -> None:
    """Identity: if the true clock is flat the two traders coincide, so the floor and the
    difference constant vanish and the two single-trader constants agree."""
    flat = np.ones(5)
    assert ch.floor_second_moment(flat, hl.SIGMA, hl.SPOT) == pytest.approx(0.0, abs=1e-12)
    assert ch.rate_constant(flat, hl.SIGMA, hl.SPOT, "difference") == pytest.approx(0.0, abs=1e-6)
    clock = ch.rate_constant(flat, hl.SIGMA, hl.SPOT, "clock")
    assert ch.rate_constant(flat, hl.SIGMA, hl.SPOT, "calendar") == pytest.approx(clock, rel=1e-12)


@pytest.mark.parametrize(("column", "believer"), [("gap", "difference"), ("cal_gap", "calendar")])
def test_noise_constants_match_simulated_gap(column: str, believer: str) -> None:
    """``m E[(P&L_m - I_m)^2]`` at m = 16 against the leading-order constant, for the hedge
    difference and for the calendar trader alone: both converge to the EKJS sum pathwise, at the
    predicted rate constant. (Measured at m = 16: 15.45 against 15.15, 138.0 against 138.8.)"""
    level = 4
    fine, _ = hl.sample_level(level, 10_000, np.random.default_rng(13))
    col = fine[:, hl.COLUMNS.index(column)]
    c_sim = np.sqrt(2**level * col.mean())
    c_se = c_sim * col.std(ddof=1) / np.sqrt(col.size) / (2 * col.mean())
    c_cf = ch.rate_constant(hl.WEIGHTS, hl.SIGMA, hl.SPOT, believer)
    assert abs(c_sim - c_cf) < 0.03 * c_cf + 4 * c_se


def test_ekjs_column_matches_exact_moment_on_both_grids() -> None:
    """The coupled sampler's EKJS column has a known mean on the fine grid and, because the coarse
    path is the fine one subsampled, on the coarse grid too. Checks simulate, the subsampling, the
    pair averaging and ekjs_pathwise together."""
    level = 3
    fine, coarse = hl.sample_level(level, 20_000, np.random.default_rng(5))
    for sample, per_session in ((fine, 2**level), (coarse, 2 ** (level - 1))):
        col = sample[:, hl.COLUMNS.index("ekjs")]
        exact = ch.riemann_second_moment(hl.WEIGHTS, hl.SIGMA, hl.SPOT, per_session)
        assert abs(col.mean() - exact) < 4 * col.std(ddof=1) / np.sqrt(col.size)


@pytest.mark.slow
def test_clock_rate_constant_matches_simulated_hedge() -> None:
    """sqrt(m E[clock_m^2]) at m = 32 sits within 2% of the leading-order constant (D-25 measured
    143.7 against 144.8); the residual is the pre-asymptotic O(1/m) term."""
    level = 5
    fine, _ = hl.sample_level(level, 20_000, np.random.default_rng(9))
    col = fine[:, hl.COLUMNS.index("clock")]
    c_sim = np.sqrt(2**level * col.mean())
    c_se = c_sim * col.std(ddof=1) / np.sqrt(col.size) / (2 * col.mean())
    c_cf = ch.rate_constant(hl.WEIGHTS, hl.SIGMA, hl.SPOT, "clock")
    assert abs(c_sim - c_cf) < 0.02 * c_cf + 4 * c_se

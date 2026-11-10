"""Closed forms for the continuous-hedging limit of an at-the-money straddle on a variance clock.

Two numbers have to come out of the hedging-frequency limit (D-27), and each has an answer that
needs no hedging simulation at all, which is what makes the MLMC estimates checkable.

**The floor.** As rebalancing refines, the calendar-minus-clock P&L difference converges to the
EKJS integral ``I = int 0.5 S^2 Gamma_b (dV_b - dV_r)`` (D-16), with gamma at the believed remaining
variance ``R``. For a straddle, ``0.5 S^2 Gamma = S phi(d1) / sqrt(R) = K n(x; R/2, R)`` with
``x = log(S/K)`` and ``n(.; m, v)`` the normal density: dollar gamma is a Gaussian kernel in
log-moneyness. Under the true clock ``x`` is Brownian with ``x_t ~ N(-V_t/2, V_t)``, so

    E[I^2] = K^2 int int k(s, t) dmu(s) dmu(t),
    k(s, t) = E[n(x_s; R_s/2, R_s) n(x_t; R_t/2, R_t)]

and ``k`` is a product of Gaussians integrated against a Gaussian: closed form. The floor is
``F = sqrt(E[I^2])``, because ``E[I] = 0`` exactly (``src.sim.analytic``), which is also the check
on the quadrature here.

**The rate constant.** Over one step a hedger whose model is right loses
``-0.5 S^2 Gamma ((dS/S)^2 - v)``, with variance ``2 g^2 v^2``. Summed over ``m`` steps per session
that is ``c^2 / m`` with (Bertsimas, Kogan and Lo 2000)

    c^2 = 2 sigma^4 int E[g_t^2] w(t)^2 dt,
    E[g^2] = K^2 n((R + V)/2; 0, V + R/2) / (2 sqrt(pi R)).

``g`` uses the hedger's believed remaining variance, the noise uses the true per-step variance.
For a straddle ``E[g^2]`` blows up like ``R^-1/2`` at expiry, which is integrable, so the ``m^-1/2``
rate survives the kink in the payoff (Gobet and Temam 2001 show it does for Lipschitz payoffs).

Time ``t`` is measured in sessions, ``w`` holds the per-session clock weights, ``sigma`` is the
per-session volatility, and the strike is the entry spot (``x_0 = 0``). Both integrands carry an
inverse square root at expiry, so the quadrature runs in ``u`` with ``t = T - u^2``.
"""

from __future__ import annotations

import numpy as np

from src.sim.analytic import believed_paths


def _npdf(z: np.ndarray, var: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * z**2 / var) / np.sqrt(2.0 * np.pi * var)


def quadrature_grid(weights: np.ndarray, sigma: float, nodes: int) -> dict[str, np.ndarray]:
    """Gauss-Legendre nodes per session in ``u = sqrt(T - t)``, with what each node needs.

    Returns ``dt`` (quadrature weight in sessions), the true weight ``w``, true elapsed variance
    ``elapsed``, true and flat remaining variance, and ``dmu``, the believed-minus-realised variance
    rate of the calendar trader per session.
    """
    weights = np.asarray(weights, dtype=float)
    horizon = len(weights)
    x, wq = np.polynomial.legendre.leggauss(nodes)
    u_edges = np.sqrt(horizon - np.arange(horizon + 1.0))
    cum = np.concatenate([[0.0], np.cumsum(weights)])
    total = sigma**2 * cum[-1]

    keys = ("dt", "w", "elapsed", "true_rem", "flat_rem", "dmu")
    parts: dict[str, list[np.ndarray]] = {k: [] for k in keys}
    for i in range(horizon):
        lo, hi = u_edges[i + 1], u_edges[i]
        u = 0.5 * (hi - lo) * x + 0.5 * (hi + lo)
        t = horizon - u**2
        elapsed = sigma**2 * (cum[i] + weights[i] * (t - i))
        parts["dt"].append(wq * 0.5 * (hi - lo) * 2.0 * u)
        parts["w"].append(np.full(nodes, weights[i]))
        parts["elapsed"].append(elapsed)
        parts["true_rem"].append(total - elapsed)
        parts["flat_rem"].append(total * (horizon - t) / horizon)
        parts["dmu"].append(np.full(nodes, sigma**2 * (weights.mean() - weights[i])))
    return {k: np.concatenate(v) for k, v in parts.items()}


def expected_gamma_kernel(elapsed: np.ndarray, remaining: np.ndarray) -> np.ndarray:
    """``E[n(x; R/2, R)]`` for ``x ~ N(-V/2, V)``: expected dollar gamma over the strike."""
    return _npdf(0.5 * (remaining + elapsed), remaining + elapsed)


def pair_kernel(
    v_s: np.ndarray, r_s: np.ndarray, v_t: np.ndarray, r_t: np.ndarray
) -> np.ndarray:
    """``k(s, t)`` for ``s <= t``: elapsed variance ``v``, believed remaining variance ``r``.

    Condition on ``x_s``: the increment to ``t`` is ``N(-D/2, D)`` with ``D = v_t - v_s``, which
    turns the second kernel into ``n(x_s; (r_t + D)/2, r_t + D)``. Two Gaussians in ``x_s`` multiply
    into one, and its expectation against ``x_s ~ N(-v_s/2, v_s)`` is a third.
    """
    d = v_t - v_s
    m1, var1 = 0.5 * r_s, r_s
    m2, var2 = 0.5 * (r_t + d), r_t + d
    var12 = var1 * var2 / (var1 + var2)
    m12 = (m1 * var2 + m2 * var1) / (var1 + var2)
    return _npdf(m1 - m2, var1 + var2) * _npdf(m12 + 0.5 * v_s, var12 + v_s)


def _double_sum(elapsed: np.ndarray, remaining: np.ndarray, q: np.ndarray, strike: float) -> float:
    """``K^2 sum_ij q_i q_j k(t_i, t_j)`` over points with true elapsed and believed remaining
    variance, ``q`` the believed-minus-realised variance each point carries."""
    # Order each pair in time. Elapsed variance is increasing in t, so it serves as the time key.
    first = elapsed[:, None] <= elapsed[None, :]
    v_s = np.where(first, elapsed[:, None], elapsed[None, :])
    v_t = np.where(first, elapsed[None, :], elapsed[:, None])
    r_s = np.where(first, remaining[:, None], remaining[None, :])
    r_t = np.where(first, remaining[None, :], remaining[:, None])
    return float(strike**2 * q @ pair_kernel(v_s, r_s, v_t, r_t) @ q)


def floor_second_moment(
    weights: np.ndarray, sigma: float, strike: float, nodes: int = 200
) -> float:
    """``E[I^2]`` for the calendar trader against the true clock ``weights``."""
    g = quadrature_grid(weights, sigma, nodes)
    return _double_sum(g["elapsed"], g["flat_rem"], g["dt"] * g["dmu"], strike)


def riemann_second_moment(
    weights: np.ndarray, sigma: float, strike: float, per_session: int
) -> float:
    """``E[I_m^2]`` for the left-point sum ``src.sim.analytic.ekjs_pathwise`` computes on
    ``per_session`` steps a session, exactly: ``k`` is exact at any pair of times, and the sum only
    samples it. This gives every MLMC level of the EKJS column a known answer, and shows the
    Riemann bias to be ``a/m + O(m^-3/2)``, the second term from the ``R^-1/2`` corner at expiry.

    Memory is ``O((5 m)^2)``: keep ``per_session`` at or below 256.
    """
    truth, flat = believed_paths(weights, per_session)
    realised, believed = sigma**2 * truth, sigma**2 * flat
    elapsed = np.concatenate([[0.0], np.cumsum(realised)[:-1]])
    remaining = believed[::-1].cumsum()[::-1]
    return _double_sum(elapsed, remaining, believed - realised, strike)


def floor_mean(weights: np.ndarray, sigma: float, strike: float, nodes: int = 200) -> float:
    """``E[I]``, which is exactly zero: a known answer for the grid ``floor_second_moment`` uses."""
    g = quadrature_grid(weights, sigma, nodes)
    kernel = expected_gamma_kernel(g["elapsed"], g["flat_rem"])
    return float(strike * np.sum(g["dt"] * g["dmu"] * kernel))


def rate_constant(
    weights: np.ndarray, sigma: float, strike: float, believer: str, nodes: int = 200
) -> float:
    """Leading constant ``c`` in ``sd(hedging error) ~ c m^-1/2``, for ``believer`` in
    ``{"clock", "calendar", "difference"}``. For the calendar trader this is the noise around the
    floor only. ``"difference"`` is the calendar-minus-clock P&L around ``I``: both traders see the
    same return, so its per-step noise is ``(g_flat - g_true)((dS/S)^2 - v)`` and the constant needs
    ``E[g_a g_b]``, which is ``pair_kernel`` at one time."""
    g = quadrature_grid(weights, sigma, nodes)
    v, true, flat = g["elapsed"], g["true_rem"], g["flat_rem"]

    def e_gg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return strike**2 * pair_kernel(v, a, v, b)

    e_g2 = {
        "clock": lambda: e_gg(true, true),
        "calendar": lambda: e_gg(flat, flat),
        "difference": lambda: e_gg(flat, flat) - 2.0 * e_gg(flat, true) + e_gg(true, true),
    }[believer]()
    return float(np.sqrt(2.0 * sigma**4 * np.sum(g["dt"] * e_g2 * g["w"] ** 2)))

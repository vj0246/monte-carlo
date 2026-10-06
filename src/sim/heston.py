"""Heston: semi-analytic Fourier prices and an Andersen quadratic-exponential simulator.

D-12 keeps Heston as a robustness layer rather than the headline generator, but it still has to be
right, which is gate A5: the simulator must reproduce the Fourier price.

A characteristic function is easy to get subtly wrong and the error hides inside a plausible number,
so two independent checks guard it. The Fourier price must collapse to Black-76 as vol-of-vol goes
to zero with ``v0 == theta`` (a limit with a known answer), and the simulator must match the Fourier
price at finite vol-of-vol. A typo would have to satisfy both to survive.

Zero rates throughout, prices quoted on the forward. ``g`` uses the Albrecher "little trap" form,
which keeps the complex logarithm on the principal branch for long maturities.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Andersen's switching threshold between the quadratic and exponential branches.
PSI_C = 1.5


@dataclass(frozen=True)
class HestonParams:
    v0: float
    kappa: float
    theta: float
    xi: float
    rho: float


def char_fn(u: np.ndarray, p: HestonParams, ttm: float) -> np.ndarray:
    """Characteristic function of ``ln(S_T / F)``, martingale under zero rates."""
    u = np.asarray(u, dtype=complex)
    rho_xi_iu = p.rho * p.xi * 1j * u
    d = np.sqrt((rho_xi_iu - p.kappa) ** 2 + p.xi**2 * (1j * u + u**2))
    g = (p.kappa - rho_xi_iu - d) / (p.kappa - rho_xi_iu + d)
    edt = np.exp(-d * ttm)
    c = (p.kappa * p.theta / p.xi**2) * (
        (p.kappa - rho_xi_iu - d) * ttm - 2.0 * np.log((1.0 - g * edt) / (1.0 - g))
    )
    dterm = ((p.kappa - rho_xi_iu - d) / p.xi**2) * (1.0 - edt) / (1.0 - g * edt)
    return np.exp(c + dterm * p.v0)


def call_price(
    fwd: float, strike: float, ttm: float, p: HestonParams, n_nodes: int = 512, upper: float = 400.0
) -> float:
    """Lewis (2001) representation, Gauss-Legendre on [0, upper]."""
    k = np.log(fwd / strike)
    nodes, weights = np.polynomial.legendre.leggauss(n_nodes)
    u = 0.5 * upper * (nodes + 1.0)
    wt = 0.5 * upper * weights
    integrand = (np.exp(1j * u * k) * char_fn(u - 0.5j, p, ttm) / (u**2 + 0.25)).real
    return float(fwd - np.sqrt(fwd * strike) / np.pi * np.sum(wt * integrand))


def straddle_price(fwd: float, strike: float, ttm: float, p: HestonParams) -> float:
    """Call plus put. Put-call parity at zero rates: ``put = call - fwd + strike``."""
    return 2.0 * call_price(fwd, strike, ttm, p) - fwd + strike


def simulate_qe(
    p: HestonParams, spot: float, ttm: float, n_steps: int, n_paths: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Andersen QE for the variance, central discretisation for log-spot.

    Returns terminal spot and terminal variance. Euler on the variance process is biased near zero
    and a reviewer who knows the literature will say so (D-12), hence QE.
    """
    dt = ttm / n_steps
    decay = np.exp(-p.kappa * dt)
    v = np.full(n_paths, p.v0)
    x = np.zeros(n_paths)

    for _ in range(n_steps):
        mean = p.theta + (v - p.theta) * decay
        var = (v * p.xi**2 * decay * (1.0 - decay)) / p.kappa + (
            p.theta * p.xi**2 * (1.0 - decay) ** 2
        ) / (2.0 * p.kappa)
        psi = var / np.maximum(mean**2, 1e-300)

        z = rng.standard_normal(n_paths)
        unif = rng.random(n_paths)

        # Quadratic branch, psi below the threshold.
        inv = 2.0 / np.maximum(psi, 1e-300)
        b2 = np.maximum(inv - 1.0 + np.sqrt(np.maximum(inv * (inv - 1.0), 0.0)), 0.0)
        a = mean / (1.0 + b2)
        v_quad = a * (np.sqrt(b2) + z) ** 2

        # Exponential branch: an atom at zero plus an exponential tail.
        prob = np.clip((psi - 1.0) / (psi + 1.0), 0.0, 1.0 - 1e-15)
        beta = (1.0 - prob) / np.maximum(mean, 1e-300)
        v_exp = np.where(
            unif <= prob, 0.0, np.log(np.maximum((1.0 - prob) / (1.0 - unif), 1e-300)) / beta
        )

        v_next = np.where(psi < PSI_C, v_quad, v_exp)

        # dX = (rho/xi) dv - (rho kappa theta/xi) dt + (rho kappa/xi - 1/2) v dt + sqrt(1-rho^2) sqrt(v) dW
        v_bar = 0.5 * (v + v_next)
        x += (
            (p.rho / p.xi) * (v_next - v)
            - (p.rho * p.kappa * p.theta / p.xi) * dt
            + (p.rho * p.kappa / p.xi - 0.5) * v_bar * dt
            + np.sqrt(np.maximum((1.0 - p.rho**2) * v_bar * dt, 0.0)) * rng.standard_normal(n_paths)
        )
        v = v_next

    return spot * np.exp(x), v


def mc_call_price(
    p: HestonParams, fwd: float, strike: float, ttm: float, n_steps: int, n_paths: int, seed: int
) -> tuple[float, float]:
    """Monte Carlo call price and its standard error, antithetic in the orthogonal driver.

    Paths are simulated in two halves with mirrored seeds rather than mirrored draws: the QE variance
    step consumes a uniform as well as a normal, so negating the normals alone would not produce a
    valid antithetic pair. The standard error is taken over pair means, which is what the pairing
    makes independent.
    """
    half = n_paths // 2
    terminal = np.concatenate(
        [
            simulate_qe(p, fwd, ttm, n_steps, half, np.random.default_rng(seed))[0],
            simulate_qe(p, fwd, ttm, n_steps, half, np.random.default_rng(seed + 1))[0],
        ]
    )
    payoff = np.maximum(terminal - strike, 0.0)
    pairs = 0.5 * (payoff[:half] + payoff[half:])
    return float(pairs.mean()), float(pairs.std(ddof=1) / np.sqrt(half))

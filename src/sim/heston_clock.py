"""Exact prices for time-changed Heston with a piecewise-constant variance clock (D-24).

The D-24 dynamics put the clock on the variance the spot sees, not on the variance process:

    dX = -0.5 w(t) v dt + sqrt(w(t) v) dW1,   dv = kappa (theta - v) dt + xi sqrt(v) dW2,   corr rho

The model stays affine, so ``E[exp(i u X_T) | v_t] = exp(A(tau) + B(tau) v_t)`` with tau the time
left. Feynman-Kac on one piece of constant ``w`` gives the Riccati pair

    dB/dtau = 0.5 w (-u^2 - i u) + (rho xi sqrt(w) i u - kappa) B + 0.5 xi^2 B^2
    dA/dtau = kappa theta B

with constant coefficients, so each piece has a closed form. Write ``beta = kappa - rho xi sqrt(w) i u``,
``d = sqrt(beta^2 + xi^2 w (u^2 + i u))`` and the two roots ``B_pm = (beta pm d) / xi^2``. Shifting by
``B_-`` turns the Riccati equation into a Bernoulli one, and starting from ``B(0) = B0`` it solves to

    g = (B0 - B_-) / (B0 - B_+),   B(tau) = (B_- - g e B_+) / (1 - g e),   e = exp(-d tau)
    A(tau) = A0 + kappa theta [B_- tau - (2 / xi^2) ln((1 - g e) / (1 - g))]

which is Mikhailov & Noegel (2003) for time-dependent Heston. With ``B0 = 0`` it is the usual Heston
solution in Albrecher's "little trap" form, so ``w = 1`` everywhere must reproduce
``src.sim.heston.char_fn``. The pieces are chained backward from expiry: the last session is
integrated first, and its ``(A, B)`` is the initial condition of the one before.

Prices go through the same Lewis integral as ``src.sim.heston.call_price``. The only change is the
integration range: a one-session option has a log-return sd near 0.6%, so the integrand lives out to
``u ~ 1/sd``, far past the fixed 400 that is fine for maturities in months. Measured against adaptive
``scipy.integrate.quad`` on the 1-, 2- and 6-session products: a cut at 20/sd leaves 1e-4 index
points at rho = -0.9, xi = 0.8 (the Heston tail is exponential, not Gaussian), 40/sd leaves under
1e-6 everywhere. More nodes do not help: at 2048 or 4096 nodes the Black-76 limit itself drifts by
1e-5 index points with numpy's and scipy's Legendre rules alike, a rounding floor 1e-7 of the price.
"""

from __future__ import annotations

import numpy as np

from src.sim.heston import HestonParams, lewis_call

#: Integration range in units of 1/sd of the log return; the slower Heston tail sets the number.
UPPER_IN_SD = 40.0
N_NODES = 1024


def riccati_piece(
    u: np.ndarray, a0: np.ndarray, b0: np.ndarray, w: float, tau: float, p: HestonParams
) -> tuple[np.ndarray, np.ndarray]:
    """Advance ``(A, B)`` across one piece of constant clock weight ``w`` and length ``tau``."""
    beta = p.kappa - p.rho * p.xi * np.sqrt(w) * 1j * u
    d = np.sqrt(beta**2 + p.xi**2 * w * (u**2 + 1j * u))
    b_minus = (beta - d) / p.xi**2
    b_plus = (beta + d) / p.xi**2
    g = (b0 - b_minus) / (b0 - b_plus)
    e = np.exp(-d * tau)
    b = (b_minus - g * e * b_plus) / (1.0 - g * e)
    a = a0 + p.kappa * p.theta * (b_minus * tau - (2.0 / p.xi**2) * np.log((1.0 - g * e) / (1.0 - g)))
    return a, b


def char_fn(u: np.ndarray, p: HestonParams, weights: np.ndarray, dt: float) -> np.ndarray:
    """Characteristic function of ``ln(S_T / F)``; ``weights[j]`` is the clock on piece ``j``."""
    u = np.asarray(u, dtype=complex)
    a = np.zeros_like(u)
    b = np.zeros_like(u)
    for w in reversed(np.asarray(weights, dtype=float)):
        a, b = riccati_piece(u, a, b, float(w), dt, p)
    return np.exp(a + b * p.v0)


def call_price(
    fwd: float, strike: float, weights: np.ndarray, dt: float, p: HestonParams
) -> float:
    sd = np.sqrt(min(p.v0, p.theta) * float(np.sum(weights)) * dt)
    upper = max(400.0, UPPER_IN_SD / sd)
    return lewis_call(fwd, strike, lambda u: char_fn(u, p, weights, dt), N_NODES, upper)


def straddle_price(
    fwd: float, strike: float, weights: np.ndarray, dt: float, p: HestonParams
) -> float:
    """Call plus put; put-call parity at zero rates gives ``put = call - fwd + strike``."""
    return 2.0 * call_price(fwd, strike, weights, dt, p) - fwd + strike

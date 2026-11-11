"""Terminal-payoff Monte Carlo under the clocked Heston model, built to be compared (D-14, D-24).

D-14 promised antithetics, a control variate and scrambled Sobol for terminal pricing and they were
never built. The exact Fourier price in ``src.sim.heston_clock`` makes the comparison honest: every
estimator here is scored against a known answer, and the study in ``src.experiment.heston_entry``
charges each one its own wall-clock time.

**One variate per variance step.** Andersen's QE uses a normal on its quadratic branch and a uniform
on its exponential branch, never both on one path in one step, so both can come from a single normal
``z`` with ``u = Phi(z)``. That is what makes an antithetic (``z -> -z`` is ``u -> 1 - u``) and a Sobol
coordinate legitimate drivers. ``src.experiment.heston_robustness`` draws the two separately; that is
the same scheme in law, and it is the one the tests check against the Fourier price.

**Antithetics are kept as a measured negative.** An ATM straddle is nearly even in the spot normal,
so mirrored pairs are positively correlated and the pair mean is worse than two independent draws:
efficiency 0.47 to 0.67 of plain in ``mc_efficiency.parquet``, almost all of it variance. D-14
prescribed them for terminal pricing; for this payoff that was wrong.

**Conditional Monte Carlo** (Willard 1997; Romano and Touzi 1997). Split ``dW1 = rho dW2 +
sqrt(1 - rho^2) dB``. Given the variance path, the ``dB`` part is Gaussian, so

    ln(S_T / F) | v  ~  N( rho I - 0.5 V,  (1 - rho^2) V ),
    V = int w v dt,   I = int sqrt(w v) dW2 = sum sqrt(w) (dv - kappa (theta - vbar) dt) / xi

and the straddle is ``E[ Black76(F exp(rho I - 0.5 rho^2 V), K, (1 - rho^2) V) ]``. The spot noise is
integrated out exactly. On the grid this is the conditional expectation of the plain estimator, so
the two share every discretisation bias and differ only in variance.

**Control variates.** A linear control in ``I`` cannot work: the conditional straddle is even in
``rho I``, so its dependence on ``I`` is quadratic and its linear projection on ``I`` is near zero.
The control used is the same conditional price in the frozen-variance (GBM) model,
driven by the same normals: ``J = sum sqrt(w theta dt) z`` replaces ``I`` and ``theta * sum(w) dt``
replaces ``V``. ``rho J`` is exactly Gaussian, so this control's mean is exactly Black-76 at total
variance ``theta * sum(w) dt``, on the grid. At ``rho = 0`` it is a constant, so ``V`` (whose mean
QE gets exactly, because QE matches the conditional mean of ``v``) rides along as a second control.
Coefficients are fitted by least squares on the same sample (bias O(1/n)).

**Randomised QMC.** Scrambled Sobol, with the variance-path normals built by principal components
of the Brownian path so the leading coordinates carry the most variance. QE's branch switch and its
atom at zero make the integrand discontinuous, so the textbook ``n^-1`` rate is not assumed here; it
is measured.
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr, ndtri
from scipy.stats import norm, qmc

from src.sim.engine import straddle_price
from src.sim.heston import PSI_C, HestonParams

#: name -> (variate source, payoff evaluation, use controls)
METHODS = {
    "plain": ("pseudo", "payoff", False),
    "antithetic": ("antithetic", "payoff", False),
    "conditional": ("pseudo", "conditional", False),
    "conditional_cv": ("pseudo", "conditional", True),
    "rqmc_plain": ("sobol", "payoff", False),
    "rqmc_conditional": ("sobol", "conditional", False),
}


def qe_step(v: np.ndarray, dt: float, p: HestonParams, z: np.ndarray) -> np.ndarray:
    """One Andersen QE step driven by one standard normal per path."""
    decay = np.exp(-p.kappa * dt)
    mean = p.theta + (v - p.theta) * decay
    var = (v * p.xi**2 * decay * (1.0 - decay)) / p.kappa + (
        p.theta * p.xi**2 * (1.0 - decay) ** 2
    ) / (2.0 * p.kappa)
    psi = var / np.maximum(mean**2, 1e-300)

    inv = 2.0 / np.maximum(psi, 1e-300)
    b2 = np.maximum(inv - 1.0 + np.sqrt(np.maximum(inv * (inv - 1.0), 0.0)), 0.0)
    quadratic = mean / (1.0 + b2) * (np.sqrt(b2) + z) ** 2

    prob = np.clip((psi - 1.0) / (psi + 1.0), 0.0, 1.0 - 1e-15)
    beta = (1.0 - prob) / np.maximum(mean, 1e-300)
    # 1 - u taken as the survival function, so a large z does not round 1 - u to zero.
    exponential = np.where(
        ndtr(z) <= prob, 0.0, np.log(np.maximum((1.0 - prob) / norm.sf(z), 1.0)) / beta
    )
    return np.where(psi < PSI_C, quadratic, exponential)


def integrals(
    p: HestonParams,
    step_weights: np.ndarray,
    dt: float,
    z_var: np.ndarray,
    z_spot: np.ndarray | None,
    expiries: tuple[int, ...],
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """``(V, I, G)`` at each expiry (a step count). ``G = sum sqrt(w vbar dt) z_spot`` is the
    orthogonal Gaussian part of the log-return, zero when ``z_spot`` is None."""
    n_paths = z_var.shape[0]
    v = np.full(n_paths, p.v0)
    big_v, big_i, big_g = np.zeros(n_paths), np.zeros(n_paths), np.zeros(n_paths)
    out = {}
    for k, w in enumerate(step_weights):
        v_next = qe_step(v, dt, p, z_var[:, k])
        v_bar = 0.5 * (v + v_next)
        big_v += w * v_bar * dt
        big_i += np.sqrt(w) * (v_next - v - p.kappa * (p.theta - v_bar) * dt) / p.xi
        if z_spot is not None:
            big_g += np.sqrt(w * v_bar * dt) * z_spot[:, k]
        v = v_next
        if k + 1 in expiries:
            out[k + 1] = (big_v.copy(), big_i.copy(), big_g.copy())
    return out


def expected_v(p: HestonParams, step_weights: np.ndarray, dt: float, expiry: int) -> float:
    """``E[V]`` on the QE grid, exact: QE reproduces ``E[v_{t+dt} | v_t]``."""
    ev = p.theta + (p.v0 - p.theta) * np.exp(-p.kappa * dt * np.arange(expiry + 1))
    return float((step_weights[:expiry] * 0.5 * (ev[:-1] + ev[1:]) * dt).sum())


def pca_increments(n_steps: int) -> np.ndarray:
    """Matrix taking iid normals to iid Brownian increments, principal components first.

    ``L L^T = min(i, j)`` is the covariance of the path, differencing turns the path into
    increments, and ``D C D^T = I``, so the result is orthogonal and the increments stay iid N(0,1).
    """
    idx = np.arange(1, n_steps + 1)
    vals, vecs = np.linalg.eigh(np.minimum.outer(idx, idx).astype(float))
    order = np.argsort(vals)[::-1]
    root = vecs[:, order] * np.sqrt(vals[order])
    return np.diff(root, axis=0, prepend=0.0)


def normals(source: str, n_paths: int, dims: int, seed: int) -> np.ndarray:
    if source == "sobol":
        points = qmc.Sobol(dims, scramble=True, seed=seed).random_base2(int(np.log2(n_paths)))
        return ndtri(np.clip(points, 1e-16, 1.0 - 1e-16))
    rng = np.random.default_rng(seed)
    if source == "antithetic":
        z = rng.standard_normal((n_paths // 2, dims))
        return np.concatenate([z, -z])
    return rng.standard_normal((n_paths, dims))


def path_values(
    plain: bool, big_v: np.ndarray, big_i: np.ndarray, big_g: np.ndarray, p: HestonParams, fwd: float
) -> np.ndarray:
    """Per-path ATM straddle: the payoff, or (``plain=False``) its expectation given the variance path."""
    if plain:
        x = -0.5 * big_v + p.rho * big_i + np.sqrt(1.0 - p.rho**2) * big_g
        return np.abs(fwd * np.exp(x) - fwd)
    shifted = fwd * np.exp(p.rho * big_i - 0.5 * p.rho**2 * big_v)
    return straddle_price(shifted, fwd, (1.0 - p.rho**2) * big_v)


def samples(
    method: str,
    legs: tuple[tuple[int, float], ...],
    p: HestonParams,
    step_weights: np.ndarray,
    dt: float,
    fwd: float,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """Units whose mean is the price of ``sum sign * straddle(expiry)`` over ``legs = ((expiry_step,
    sign), ...)``, ATM: per-path values, pair means for antithetics, control-adjusted values for the
    CV. Pseudo-random units are independent; Sobol units are not, so only their mean means anything."""
    source, evaluation, use_controls = METHODS[method]
    n_steps = step_weights.size
    plain = evaluation == "payoff"
    z = normals(source, n_paths, n_steps * (2 if plain else 1), seed)
    if source == "sobol":
        # Interleaved so the leading Sobol coordinates go to the leading components of each driver.
        pca = pca_increments(n_steps)
        z = np.concatenate([z[:, 0::2] @ pca.T, z[:, 1::2] @ pca.T], axis=1) if plain else z @ pca.T
    z_spot, z_var = (z[:, :n_steps], z[:, n_steps:]) if plain else (None, z)

    by_expiry = integrals(p, step_weights, dt, z_var, z_spot, tuple(k for k, _ in legs))
    values = np.zeros(n_paths)
    controls = np.zeros((n_paths, 2))
    control_mean = np.zeros(2)
    for expiry, sign in legs:
        values += sign * path_values(plain, *by_expiry[expiry], p, fwd)
        if use_controls:
            frozen = p.theta * dt * step_weights[:expiry]
            big_j = z_var[:, :expiry] @ np.sqrt(frozen)
            gbm_fwd = fwd * np.exp(p.rho * big_j - 0.5 * p.rho**2 * frozen.sum())
            controls[:, 0] += sign * straddle_price(gbm_fwd, fwd, (1.0 - p.rho**2) * frozen.sum())
            controls[:, 1] += sign * by_expiry[expiry][0]
            control_mean += sign * np.array(
                [straddle_price(fwd, fwd, frozen.sum()), expected_v(p, step_weights, dt, expiry)]
            )
    if use_controls:
        beta, *_ = np.linalg.lstsq(controls - controls.mean(axis=0), values - values.mean(), rcond=None)
        values = values - (controls - control_mean) @ beta
    if source == "antithetic":
        half = n_paths // 2
        values = 0.5 * (values[:half] + values[half:])
    return values


def estimate(
    method: str,
    legs: tuple[tuple[int, float], ...],
    p: HestonParams,
    step_weights: np.ndarray,
    dt: float,
    fwd: float,
    n_paths: int,
    seed: int,
) -> float:
    """Price of ``sum sign * straddle(expiry)`` over ``legs``, ATM; see ``samples``."""
    return float(samples(method, legs, p, step_weights, dt, fwd, n_paths, seed).mean())

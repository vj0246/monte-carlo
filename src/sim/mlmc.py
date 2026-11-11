"""Multilevel Monte Carlo (Giles 2008, Operations Research 56(3), 607-617).

The hedging questions in D-27 are limits as the rebalancing count goes to infinity. A single-level
estimate on the finest grid pays for every step of that grid on every path, although almost all of
the variance is already present on the coarsest one. MLMC telescopes instead,

    E[P_L] = E[P_0] + sum_{l=1..L} E[P_l - P_(l-1)],

and estimates each term with its own sample. When level ``l`` and level ``l - 1`` are built from the
same Brownian path, ``Y_l = P_l - P_(l-1)`` is small and needs few samples. Three rates decide the
cost: ``|E[Y_l]| ~ 2^(-alpha l)`` (bias), ``Var[Y_l] ~ 2^(-beta l)`` (variance),
``C_l ~ 2^(gamma l)`` (cost). For a target RMSE ``eps`` MLMC costs ``eps^-2`` when beta > gamma,
``eps^-2 log(eps)^2`` when beta == gamma, ``eps^-(2 + (gamma - beta)/alpha)`` otherwise;
single-level MC costs ``eps^-(2 + gamma/alpha)``.

A sampler is ``sampler(level, n, rng) -> (fine, coarse)``: ``n`` iid samples of each quantity on
``level`` and on ``level - 1``, from the same randomness, each shaped ``(n, q)``. At level 0 the
coarse array must be zeros. Samples are allocated for column 0; the other columns ride along on the
same paths and carry their own standard errors.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

Sampler = Callable[[int, int, np.random.Generator], tuple[np.ndarray, np.ndarray]]


@dataclass
class Level:
    """Running sums for one level, so a level can be topped up without keeping its samples."""

    level: int
    cost: float  # model cost of one sample
    n: int = 0
    seconds: float = 0.0
    sums: dict[str, np.ndarray] = field(default_factory=dict)

    def add(self, fine: np.ndarray, coarse: np.ndarray, seconds: float) -> None:
        for key, x in (("fine", fine), ("coarse", coarse), ("y", fine - coarse)):
            self.sums[key] = self.sums.get(key, 0.0) + x.sum(axis=0)
            self.sums[key + "2"] = self.sums.get(key + "2", 0.0) + (x**2).sum(axis=0)
        self.n += len(fine)
        self.seconds += seconds

    def mean(self, key: str) -> np.ndarray:
        return self.sums[key] / self.n

    def var(self, key: str) -> np.ndarray:
        return (self.sums[key + "2"] / self.n - self.mean(key) ** 2) * self.n / (self.n - 1)


@dataclass(frozen=True)
class Estimate:
    value: float  # sum of level means: an estimate of E[P_L]
    se: float  # its standard error
    tail: float  # Richardson estimate of E[P_inf] - E[P_L]
    limit_se: float  # standard error of value + tail (the tail reuses level L's sample)
    alpha: float
    beta: float

    @property
    def limit(self) -> float:
        return self.value + self.tail


def decay_rate(values: np.ndarray, first: int = 2) -> float:
    """Fitted ``r`` in ``|values[l]| ~ 2^(-r l)``, least squares on log2 over levels ``first..L``.

    Level 0 is a different object (``P_0``, not a difference) and level 1 is usually
    pre-asymptotic, so both are left out of the fit by default.
    """
    levels = np.arange(first, len(values))
    return float(-np.polyfit(levels, np.log2(np.abs(values[first:])), 1)[0])


def optimal_samples(variances: np.ndarray, costs: np.ndarray, eps: float) -> np.ndarray:
    """Giles' allocation: minimise ``sum N_l C_l`` subject to ``sum V_l / N_l = eps^2 / 2``.

    The other half of the MSE budget belongs to the bias, which the caller checks with the tail.
    """
    return np.ceil(
        2.0 / eps**2 * np.sqrt(variances / costs) * np.sum(np.sqrt(variances * costs))
    ).astype(int)


def _sample(sampler: Sampler, lev: Level, n: int, rng: np.random.Generator) -> None:
    start = time.perf_counter()
    fine, coarse = sampler(lev.level, n, rng)
    lev.add(fine, coarse, time.perf_counter() - start)


def run(
    sampler: Sampler,
    costs: np.ndarray,
    eps: float,
    n_pilot: int,
    seed: int,
) -> list[Level]:
    """Pilot every level, then top each up to the optimal count for RMSE ``eps`` on column 0.

    Each level owns a child of one ``SeedSequence``, so levels are independent of each other and a
    top-up continues its level's stream: the whole run is reproducible from ``seed``.
    """
    seeds = np.random.SeedSequence(seed).spawn(len(costs))
    rngs = [np.random.default_rng(s) for s in seeds]
    levels = [Level(level, float(cost)) for level, cost in enumerate(costs)]
    for lev, rng in zip(levels, rngs):
        _sample(sampler, lev, n_pilot, rng)

    var_y = np.array([lev.var("y")[0] for lev in levels])
    for lev, rng, n in zip(levels, rngs, optimal_samples(var_y, np.asarray(costs), eps)):
        if n > lev.n:
            _sample(sampler, lev, int(n - lev.n), rng)
    return levels


def estimate(levels: list[Level], column: int = 0, alpha: float | None = None) -> Estimate:
    """Telescoped estimate of ``E[P_L]`` and its Richardson limit.

    Pass ``alpha`` when the weak rate is known from theory: a fitted rate is only as good as the
    finest ``E[Y_l]``, which is the least resolved of them.
    """
    mean_y = np.array([lev.mean("y")[column] for lev in levels])
    var_y = np.array([lev.var("y")[column] for lev in levels])
    n = np.array([lev.n for lev in levels])
    alpha = decay_rate(mean_y) if alpha is None else alpha
    # sum_{l > L} E[Y_l] for a geometric decay at rate alpha: E[Y_L] / (2^alpha - 1). The limit
    # therefore counts level L's mean 2^alpha / (2^alpha - 1) times, and its variance with it.
    inflate = np.ones(len(levels))
    inflate[-1] = (2.0**alpha / (2.0**alpha - 1.0)) ** 2
    return Estimate(
        value=float(mean_y.sum()),
        se=float(np.sqrt(np.sum(var_y / n))),
        tail=float(mean_y[-1] / (2.0**alpha - 1.0)),
        limit_se=float(np.sqrt(np.sum(inflate * var_y / n))),
        alpha=alpha,
        beta=decay_rate(var_y),
    )


def mlmc_cost(levels: list[Level], eps: float, column: int = 0) -> float:
    """Model cost of MLMC at RMSE ``eps`` with the optimal allocation, unrounded:
    ``2 eps^-2 (sum_l sqrt(V_l C_l))^2``."""
    var_y = np.array([lev.var("y")[column] for lev in levels])
    costs = np.array([lev.cost for lev in levels])
    return float(2.0 / eps**2 * np.sum(np.sqrt(var_y * costs)) ** 2)


def single_level_cost(levels: list[Level], eps: float, fine_cost: float, column: int = 0) -> float:
    """Model cost of plain MC on the finest level at the same RMSE and the same bias budget:
    ``2 eps^-2 Var[P_L]`` samples at ``fine_cost`` each (no coarse path to pay for)."""
    return float(2.0 / eps**2 * levels[-1].var("fine")[column] * fine_cost)

"""Acceptance gates A5 to A8 from DECISIONS.md D-18, with the A6 wording corrected in D-23.

Every gate here is seeded, so a failure is a real regression rather than an unlucky draw.
"""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest

from src.config import settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT, TUE_REGIME
from src.provenance import config_hash, manifest
from src.sim.analytic import believed_paths, ekjs_short_straddle_mean
from src.sim.engine import hedge_short_straddle, simulate, straddle_price
from src.sim.heston import HestonParams, call_price, mc_call_price

pytestmark = [pytest.mark.acceptance, pytest.mark.slow]

A5_SETS = 20
A5_PATHS = 60_000
A5_STEPS = 128
#: Allowance for QE discretisation on top of 3 standard errors. Measured over 3 parameter sets at
#: 64, 256 and 1024 steps the deviation oscillates inside +/-0.4% with no drift, so this absorbs
#: discretisation without hiding a wrong characteristic function, which would be wrong by percent.
A5_BIAS_ALLOWANCE = 0.003

SESSION_ORDER = ("Wed", "Thu", "Fri", "Mon", "Tue")


def _clock_weights() -> np.ndarray:
    weights = np.array([TUE_REGIME[d] for d in SESSION_ORDER])
    return weights / weights.mean()


def _remaining(per_step: np.ndarray) -> np.ndarray:
    return per_step.sum() - np.concatenate([[0.0], np.cumsum(per_step)[:-1]])


def test_a5_heston_mc_matches_fourier() -> None:
    """A5. The QE simulator must reproduce the semi-analytic Fourier price."""
    rng = np.random.default_rng(20_260_101)
    worst = 0.0
    for i in range(A5_SETS):
        p = HestonParams(
            v0=float(rng.uniform(0.01, 0.09)),
            kappa=float(rng.uniform(0.5, 3.0)),
            theta=float(rng.uniform(0.01, 0.09)),
            xi=float(rng.uniform(0.2, 0.8)),
            rho=float(rng.uniform(-0.9, 0.0)),
        )
        ttm = float(rng.uniform(0.1, 1.0))
        strike = 100.0 * float(np.exp(rng.uniform(-0.15, 0.15)))
        exact = call_price(100.0, strike, ttm, p)
        got, se = mc_call_price(p, 100.0, strike, ttm, A5_STEPS, A5_PATHS, seed=1_000 + i)
        tolerance = 3 * se + A5_BIAS_ALLOWANCE * exact
        worst = max(worst, abs(got - exact) / tolerance)
        assert abs(got - exact) < tolerance, (
            f"set {i}: mc={got:.4f} fourier={exact:.4f} se={se:.4f} params={p} ttm={ttm:.3f}"
        )
    assert worst < 1.0


def test_a6_hedging_matches_the_ekjs_benchmark() -> None:
    """A6, corrected by D-23.

    The original wording said the wrong-clock mean converges to a non-zero EKJS value. It does not:
    both traders enter at the same price, so their P&L difference is a stochastic integral of a delta
    difference against the spot, a martingale, and its mean goes to zero as rebalancing refines. The
    benchmark itself shrinks with the grid (-0.699, -0.174, -0.043, -0.011 at 1, 4, 16, 64 hedges per
    session). What is testable, and tested here, is that the simulator equals the closed form on
    whatever grid it is run, and that a correct-clock hedger has mean zero.
    """
    sigma = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
    weights = _clock_weights()
    entry = straddle_price(SPOT, SPOT, float(sigma**2 * weights.sum()))
    benchmarks = []

    for per_session in (1, 4, 16, 64):
        truth, flat = believed_paths(weights, per_session)
        paths = simulate(truth, sigma, SPOT, 200_000, np.random.default_rng(5))
        clock = hedge_short_straddle(paths, SPOT, sigma**2 * _remaining(truth), entry, 0.0)
        calendar = hedge_short_straddle(paths, SPOT, sigma**2 * _remaining(flat), entry, 0.0)
        expected = ekjs_short_straddle_mean(SPOT, SPOT, sigma**2 * truth, sigma**2 * flat)
        benchmarks.append(expected)

        se = calendar.std() / np.sqrt(calendar.size)
        assert abs(calendar.mean() - expected) < 3 * se, (
            f"{per_session}/session: mc={calendar.mean():+.4f} ekjs={expected:+.4f} se={se:.4f}"
        )
        if per_session >= 16:
            se_clock = clock.std() / np.sqrt(clock.size)
            assert abs(clock.mean()) < 3 * se_clock, "correct-clock mean should vanish"

    assert abs(benchmarks[-1]) < abs(benchmarks[0]), "benchmark must shrink as rebalancing refines"


def test_a7_headline_is_a_surface_not_a_number() -> None:
    """A7. The result is published across rebalance frequency and cost, with provenance."""
    path = settings.tables_dir / "sensitivity.parquet"
    if not path.exists():
        pytest.skip("run `uv run python -m src.experiment.sensitivity` first")
    table = pl.read_parquet(path)
    assert table["rebalances_per_session"].n_unique() >= 3
    assert table["cost_bp"].n_unique() >= 3
    assert table.height == table["rebalances_per_session"].n_unique() * table["cost_bp"].n_unique()
    assert table["share_of_sd"].is_finite().all()
    assert path.with_suffix(".manifest.json").exists(), "A8: a published table needs provenance"


def test_a8_provenance_is_complete_and_runs_are_reproducible() -> None:
    """A8, scoped by D-23: provenance plus determinism. The clean-machine run stays manual."""
    assert config_hash() == config_hash(), "config hash must be stable within a run"

    payload = manifest("test", seed=1)
    for key in ("git_commit", "config_hash", "seed", "python", "packages"):
        assert key in payload, key
    assert payload["packages"]["numpy"] != "absent"

    path = settings.tables_dir / "sensitivity.parquet"
    if path.exists():
        stored = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        assert stored["config_hash"] == config_hash(), "table was built under a different config"
        assert stored["seed"] is not None

    weights = _clock_weights()
    first = simulate(weights, 0.008, SPOT, 2_000, np.random.default_rng(11))
    second = simulate(weights, 0.008, SPOT, 2_000, np.random.default_rng(11))
    assert np.array_equal(first, second), "same seed must give identical paths"

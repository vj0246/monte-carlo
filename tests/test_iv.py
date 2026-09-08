"""Unit tests for the Black-76 layer. Fast, no data dependency."""

from __future__ import annotations

import numpy as np
import pytest

from src.iv.invert import black76_undiscounted, black76_vega, implied_vol


def test_round_trip_recovers_input_vol() -> None:
    """Price at a known vol, invert, get the vol back.

    Deliberately includes contracts down to two calendar days, because that is where vega collapses
    and where a Newton-only solver would silently return whatever its last iterate happened to be.
    """
    rng = np.random.default_rng(0)
    n = 20_000
    fwd = rng.uniform(15_000, 30_000, n)
    strike = fwd * np.exp(rng.uniform(-0.15, 0.15, n))
    ttm = rng.uniform(2 / 365, 0.25, n)
    sigma = rng.uniform(0.05, 0.9, n)
    is_call = rng.random(n) < 0.5
    discount = np.exp(-0.065 * ttm)

    price = black76_undiscounted(sigma, fwd, strike, ttm, is_call) * discount
    back = implied_vol(price, fwd, strike, ttm, discount, is_call)

    solved = ~np.isnan(back)
    assert solved.mean() > 0.95, "too many contracts left unsolved"
    assert np.abs(back[solved] - sigma[solved]).max() < 1e-6

    short = solved & (ttm < 7 / 365)
    assert short.sum() > 100
    assert np.abs(back[short] - sigma[short]).max() < 1e-6


def test_unsolvable_prices_return_nan_not_a_clipped_guess() -> None:
    """Outside the no-arbitrage bounds there is no implied volatility. 3.1% of real quotes land
    there (D-05 found settlements violating monotonicity in strike), so this path is live, not
    hypothetical. Returning a boundary value instead of NaN would push a fake number downstream."""
    fwd = np.array([25_000.0, 25_000.0, 25_000.0])
    strike = np.array([24_000.0, 24_000.0, 24_000.0])
    ttm = np.array([0.05, 0.05, 0.05])
    discount = np.exp(-0.065 * ttm)
    is_call = np.array([True, True, True])
    # below intrinsic, above the forward, and exactly at intrinsic
    price = np.array([500.0, 26_000.0, 1_000.0]) * discount

    assert np.isnan(implied_vol(price, fwd, strike, ttm, discount, is_call)).all()


def test_put_call_parity_holds_in_the_pricer() -> None:
    fwd, strike, ttm, sigma = 25_000.0, 24_500.0, 0.08, 0.14
    call = black76_undiscounted(sigma, fwd, strike, ttm, True)
    put = black76_undiscounted(sigma, fwd, strike, ttm, False)
    assert call - put == pytest.approx(fwd - strike, abs=1e-9)


def test_vega_matches_a_numerical_derivative() -> None:
    fwd, strike, ttm, sigma = 25_000.0, 25_500.0, 0.06, 0.18
    h = 1e-6
    numeric = (
        black76_undiscounted(sigma + h, fwd, strike, ttm, True)
        - black76_undiscounted(sigma - h, fwd, strike, ttm, True)
    ) / (2 * h)
    assert black76_vega(sigma, fwd, strike, ttm) == pytest.approx(numeric, rel=1e-6)

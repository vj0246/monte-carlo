"""STT on index options (src.experiment.stt): closed forms against quadrature, and the rate file."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest
from scipy.integrate import quad

from src.config import settings
from src.experiment import stt
from src.sim.engine import straddle_price

F = K = 25_000.0
VAR = 4.1e-5  # about one Tuesday-weighted session at 13% vol


def _expect(payoff, var: float = VAR) -> float:
    """E[payoff(S_T)] for a lognormal martingale with total variance ``var``, by quadrature."""
    sd = np.sqrt(var)

    def integrand(z: float) -> float:
        s = F * np.exp(-0.5 * var + sd * z)
        return payoff(s) * np.exp(-0.5 * z * z) / np.sqrt(2 * np.pi)

    # Split at the kinks so quad sees smooth pieces.
    kinks = sorted((np.log(k / F) + 0.5 * var) / sd for k in (K / 1.0015, K, K / 0.9985))
    edges = [-12.0, *kinks, 12.0]
    return sum(quad(integrand, a, b, epsabs=1e-10, epsrel=1e-12)[0] for a, b in zip(edges, edges[1:]))


def test_exercise_stt_is_exactly_proportional() -> None:
    e = 0.00125
    taxed = _expect(lambda s: abs(s - K) - e * abs(s - K))
    assert taxed == pytest.approx((1 - e) * straddle_price(F, K, VAR), rel=1e-9)


def test_counterfactual_forced_exercise_is_price_minus_e_forward() -> None:
    e = 0.0015
    forced = _expect(lambda s: abs(s - K) - e * s)  # one leg is always in the money
    assert forced == pytest.approx(straddle_price(F, K, VAR) - e * F, rel=1e-9)


def test_counterfactual_optimal_exercise_matches_quadrature() -> None:
    e = 0.0015
    optimal = _expect(lambda s: max(s - K - e * s, 0.0) + max(K - s - e * s, 0.0))
    assert stt.notional_base_value(F, K, VAR, e) == pytest.approx(optimal, rel=1e-9)
    assert stt.notional_base_value(F, K, VAR, 0.0) == pytest.approx(straddle_price(F, K, VAR))


def test_trap_probability_matches_quadrature_and_band() -> None:
    e = 0.0015
    lo, hi = K / (1 + e), K / (1 - e)
    in_band = _expect(lambda s: float(lo < s < hi))
    assert stt.trap_probability(F, K, VAR, e) == pytest.approx(in_band, rel=1e-8)
    assert hi - lo == pytest.approx(2 * e * K, rel=1e-5)


def test_no_trap_on_the_intrinsic_base() -> None:
    # An identity, kept as documentation: (1 - e) I >= 0 for any e < 1. The substance is the
    # contrast with the notional base below, which does lose money just in the money.
    e = 0.0015
    settle = np.linspace(K - 200, K + 200, 40_001)
    intrinsic = np.maximum(settle - K, 0.0)
    assert np.all(intrinsic - e * intrinsic >= 0.0)  # current law: never costs more than it pays
    notional_loss = (settle > K) & (settle - K < e * settle)
    assert notional_loss.any()  # the counterfactual base does have a losing band


def test_taxed_error_by_quadrature_matches_the_frictionless_error() -> None:
    # Independent of stt._prices: integrate the taxed long payoff at the real clock and flat
    # variances, take the ratio, and compare with the closed-form frictionless error.
    sigma2 = stt.ANNUAL_VOL**2 / stt.SESSIONS_PER_YEAR
    mean_w = float(np.mean(list(stt.TUE_REGIME.values())))
    e = 0.0015
    for day, expected in (("Mon", -0.2183), ("Thu", -0.0058)):
        span = stt.SPANS[day]
        var_clock = sigma2 * sum(stt.TUE_REGIME[d] for d in span) / mean_w
        var_flat = sigma2 * len(span)
        taxed = [_expect(lambda s: (1 - e) * abs(s - K), v) for v in (var_clock, var_flat)]
        frictionless = straddle_price(F, K, var_clock) / straddle_price(F, K, var_flat) - 1
        assert taxed[0] / taxed[1] - 1 == pytest.approx(frictionless, abs=1e-9)
        assert frictionless == pytest.approx(expected, abs=5e-5)


def test_entry_price_error_is_invariant_and_counterfactual_is_not() -> None:
    table = stt.run(settings)
    assert table.height == 15
    # err_shift_max < 1e-12 is an identity of _prices (both prices carry the same constant);
    # the quadrature test above is the independent check.
    assert float(table["err_shift_max"].max()) < 1e-12
    monday = table.filter(table["opened"] == "Mon")
    assert np.allclose(monday["err_frictionless"].to_numpy(), -0.2183, atol=5e-5)
    assert np.all(monday["cf_err_forced"].to_numpy() < -0.26)
    full_week = table.filter(table["opened"] == "Tue")
    assert np.allclose(full_week["err_frictionless"].to_numpy(), 0.0, atol=1e-12)
    assert np.allclose(full_week["price_clock"].to_numpy(), 365.26, atol=0.01)


def test_rate_file_covers_the_sample_contiguously() -> None:
    raw = json.loads((settings.reference_dir / "stt_rates.json").read_text(encoding="utf-8"))
    periods = raw["periods"]
    assert dt.date.fromisoformat(periods[0]["from"]) <= dt.date(2024, 1, 1)
    assert periods[-1]["to"] is None
    for prev, nxt in zip(periods, periods[1:]):
        gap = dt.date.fromisoformat(nxt["from"]) - dt.date.fromisoformat(prev["to"])
        assert gap == dt.timedelta(days=1)
    for period in periods:
        assert period["sources"] and all(s in raw["sources"] for s in period["sources"])
    for source in raw["sources"].values():
        assert source["url"].startswith("https://")
    # The cf_ notional base is the pre-2019-09-01 statute, so it needs its own source.
    assert "memo2019" in raw["base"]["option_exercised_before_2019_09_01"]
    assert "memo2019" in raw["sources"]
    by_start = {p["from"]: (p["option_sale_pct"], p["option_exercised_pct"]) for p in periods}
    assert by_start == {
        "2023-04-01": (0.0625, 0.125),
        "2024-10-01": (0.1, 0.125),
        "2026-04-01": (0.15, 0.15),
    }

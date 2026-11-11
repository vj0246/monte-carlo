"""Known-answer tests for propagating clock uncertainty into the price and the hedge."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import norm

from src.experiment import clock_uncertainty as cu
from src.experiment.mispricing import SPANS

TUE_POINT = np.array([1.459, 0.607, 1.093, 0.928, 0.880])  # Mon..Fri, D-08b
TUE_POINT = TUE_POINT / TUE_POINT.mean()


def _random_clocks(n: int, seed: int = 3) -> np.ndarray:
    u = np.random.default_rng(seed).lognormal(0.0, 0.3, size=(n, 5))
    return u / u.mean(axis=1, keepdims=True)


def test_spans_match_the_published_tuesday_table() -> None:
    names = {f"open_{day}": (span, []) for day, span in SPANS.items()}
    names["spread_Mon"] = (SPANS["Mon"] + SPANS["Tue"], SPANS["Mon"])
    assert cu.products("Tue") == names
    assert cu.week_order("Thu") == ["Fri", "Mon", "Tue", "Wed", "Thu"]


def test_entry_error_against_the_atm_closed_form() -> None:
    """ATM straddle on total variance V is ``2 S (2 Phi(sqrt(V)/2) - 1)``: independent of Black-76
    as coded, and the flat clock must price every product with zero error."""
    errors = cu.entry_errors(np.vstack([np.ones(5), TUE_POINT]), "Tue")
    assert all(abs(v[0]) < 1e-14 for v in errors.values())

    def atm(sessions: float) -> float:
        return 2 * (2 * norm.cdf(cu.SIGMA * np.sqrt(sessions) / 2) - 1)

    tue = TUE_POINT[1]
    assert errors["open_Mon"][1] == pytest.approx(atm(tue) / atm(1) - 1, rel=1e-10)
    spread = (atm(tue + 5) - atm(tue)) / (atm(6) - atm(1)) - 1
    assert errors["spread_Mon"][1] == pytest.approx(spread, rel=1e-10)
    assert errors["open_Mon"][1] == pytest.approx(-0.218, abs=5e-4)  # D-08c


def test_one_weight_products_are_monotone_and_the_week_is_exact() -> None:
    """With the week pinned at 5, the Monday straddle and spread depend on u_Tue alone, the
    Wednesday straddle on u_Wed alone, and the full week on nothing."""
    u = _random_clocks(500)
    errors = cu.entry_errors(u, "Tue")
    assert np.max(np.abs(errors["open_Tue"])) < 1e-12
    by_tue = np.argsort(u[:, 1])
    assert np.all(np.diff(errors["open_Mon"][by_tue]) > 0)
    assert np.all(np.diff(errors["spread_Mon"][by_tue]) < 0)
    assert np.all(np.diff(errors["open_Wed"][np.argsort(u[:, 2])]) < 0)


def test_naive_band_is_exact_for_one_session_and_too_wide_for_sums() -> None:
    """The Monday straddle spans one session, so its marginal interval maps through. Every
    product that sums sessions, the spread's week included, ignores that the week totals 5."""
    u = _random_clocks(2001)
    errors = cu.entry_errors(u, "Tue")
    naive = cu.naive_band(u, "Tue")
    band = {k: np.percentile(v, [cu.P_LO, cu.P_HI]) for k, v in errors.items()}
    assert naive["open_Mon"] == pytest.approx(tuple(band["open_Mon"]), rel=2e-3)
    assert naive["open_Tue"][1] - naive["open_Tue"][0] > 0.05  # a band where there is none
    for name in ("open_Fri", "open_Wed", "spread_Mon"):
        assert naive[name][1] - naive[name][0] > band[name][1] - band[name][0], name


def test_common_random_numbers_make_equal_clocks_bit_identical() -> None:
    order = [cu.WEEK.index(d) for d in cu.week_order("Tue")]
    a = cu.hedging(TUE_POINT[order], n_paths=cu.CHUNK)
    b = cu.hedging(TUE_POINT[order].copy(), n_paths=cu.CHUNK)
    assert a == b


@pytest.mark.slow
def test_hedging_means_are_zero_and_ekjs_matches_its_exact_moment() -> None:
    """Every mean P&L is exactly zero (D-25); ``E[I_64^2]`` is known exactly per clock."""
    order = [cu.WEEK.index(d) for d in cu.week_order("Tue")]
    h = cu.hedging(TUE_POINT[order], n_paths=2 * cu.CHUNK)
    for name in ("diff", "clock", "ekjs"):
        assert abs(h[f"{name}_mean"]) < 4 * np.sqrt(h[f"{name}_mean_mcvar"])
    assert abs(h["ekjs_sd"] - h["ekjs_exact"]) < 4 * np.sqrt(h["ekjs_sd_mcvar"])
    assert h["diff_sd"] == pytest.approx(11.5, abs=0.3)  # D-25, one hedge per session
    assert h["floor"] == pytest.approx(12.17, abs=0.02)  # D-27 closed form


@pytest.mark.slow
def test_degenerate_bootstrap_collapses_to_the_point() -> None:
    draws = np.tile(TUE_POINT, (3, 1))
    _, summary = cu.regime_tables("Tue regime", TUE_POINT, draws, n_paths=cu.CHUNK)
    for row in summary.iter_rows(named=True):
        assert row["lo"] == row["hi"] == row["median"] == row["point"], row["quantity"]
        assert row["var_draws"] <= 1e-24 * max(row["point"] ** 2, 1.0)
        if row["naive_lo"] is not None:
            assert row["naive_lo"] == pytest.approx(row["point"], abs=1e-12)
            assert row["naive_hi"] == pytest.approx(row["point"], abs=1e-12)
        if row["predictive_sd"] is not None:
            assert row["predictive_sd"] == pytest.approx(row["point"], rel=1e-12)

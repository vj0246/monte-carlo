"""Unit tests for expiry-regime segmentation. Synthetic calendars, no data dependency."""

from __future__ import annotations

import datetime as dt

from src.ingest.regime_scan import _segments

MONDAY = dt.date(2024, 1, 1)


def _weekly(weekday: int, n: int, start_week: int = 0) -> list[dt.date]:
    """``n`` consecutive weekly expiries on ``weekday`` (0 = Monday)."""
    return [MONDAY + dt.timedelta(weeks=start_week + k, days=weekday) for k in range(n)]


def _weekdays(regimes: list[dict]) -> list[str]:
    return [r["weekday"] for r in regimes]


def test_single_change_is_found() -> None:
    expiries = _weekly(3, 20) + _weekly(1, 20, start_week=20)
    regimes, residual = _segments(expiries)
    assert _weekdays(regimes) == ["Thu", "Tue"]
    assert residual == 0


def test_two_changes_are_found() -> None:
    """The case the old single-split scanner could not represent: Sensex went Fri, Tue, Thu."""
    expiries = _weekly(4, 15) + _weekly(1, 15, start_week=15) + _weekly(3, 15, start_week=30)
    regimes, _ = _segments(expiries)
    assert _weekdays(regimes) == ["Fri", "Tue", "Thu"]


def test_lone_holiday_rolls_do_not_create_regimes() -> None:
    expiries = _weekly(3, 30)
    for k in (7, 19):  # holiday: expiry moves to the previous session
        expiries[k] -= dt.timedelta(days=1)
    regimes, residual = _segments(expiries)
    assert _weekdays(regimes) == ["Thu"]
    assert residual == 2


def test_no_adjacent_regimes_share_a_weekday() -> None:
    """Old-schedule contracts running off inside a new regime (Sensex, Jan-Mar 2025) can pull the
    greedy top-level cut inside one regime. Neighbouring same-weekday segments must be merged."""
    fri = _weekly(4, 20)
    tue = _weekly(1, 20, start_week=20)
    for k in (1, 2, 4, 5, 11):  # leftover Friday contracts inside the Tuesday regime
        tue[k] += dt.timedelta(days=3)
    thu = _weekly(3, 20, start_week=40)
    regimes, _ = _segments(fri + sorted(tue) + thu)
    days = _weekdays(regimes)
    assert all(a != b for a, b in zip(days, days[1:])), days
    assert days == ["Fri", "Tue", "Thu"]

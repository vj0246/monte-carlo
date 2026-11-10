"""BANKEX as a second BSE unit (TO_DO 3.1): regime timeline, panel, and the A2a forward gate.

The timeline and gate tests read the built artefacts, as the acceptance gates do; they skip if the
pipeline has not been run. ``_days_early`` is checked against hand-counted calendar answers.

Run:  uv run pytest tests/test_bankex.py
"""

from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from src.config import settings
from src.ingest.regime_scan import _days_early
from tests.test_acceptance_data import A2A_MAX_ABS_MEDIAN_BP, A2A_MAX_MAD_BP, A2A_MIN_FUTURE_TRADES


def _timeline() -> dict:
    path = settings.reference_dir / "regime_timeline_bankex.json"
    if not path.exists():
        pytest.skip("no Bankex timeline; run `uv run python -m src.ingest.regime_scan`")
    return json.loads(path.read_text(encoding="utf-8"))


def _bankex_panel() -> pl.DataFrame:
    files = sorted(settings.panel_dir.glob("fo_bse_*.parquet"))
    if not files:
        pytest.skip("no BSE panel; run `uv run python -m src.ingest.build_panel`")
    panel = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
    return panel.filter(pl.col("symbol") == "BANKEX")


def test_every_session_has_both_exchange_files() -> None:
    """``regime_scan`` counts an expiry only if it is quoted on its own session, so a missing
    daily file would drop that expiry without a trace. Every NSE session needs both files."""
    cal = settings.raw_dir / "calendar.json"
    if not cal.exists() or not (settings.bse_raw_dir / "fo").exists():
        pytest.skip("raw archives not downloaded")
    days = json.loads(cal.read_text(encoding="utf-8"))["trading_days"]
    missing = [
        d
        for d in days
        if not (settings.raw_dir / "fo" / f"fo_{d.replace('-', '')}.csv.zip").exists()
        or not (settings.bse_raw_dir / "fo" / f"fo_{d.replace('-', '')}.csv").exists()
    ]
    assert len(days) > 600 and not missing, f"sessions without an F&O file: {missing[:10]}"


@pytest.mark.parametrize(
    ("expiry", "regime", "expected"),
    [
        (dt.date(2024, 1, 20), "Mon", 2),  # Saturday session before a Monday holiday
        (dt.date(2024, 3, 22), "Mon", 3),  # Friday: pulled back across the weekend
        (dt.date(2025, 12, 24), "Thu", 1),  # Wednesday before a Thursday holiday
        (dt.date(2025, 9, 25), "Thu", 0),  # on-regime
        (dt.date(2025, 9, 24), "Tue", 6),  # one day *after* Tuesday is six days early, not -1
    ],
)
def test_days_early_counts_across_the_week_boundary(
    expiry: dt.date, regime: str, expected: int
) -> None:
    assert _days_early(expiry, regime) == expected


def test_bankex_timeline_is_ordered_and_rolls_are_backward() -> None:
    tl = _timeline()
    regimes = tl["expiry_regimes"]
    assert len(regimes) >= 1
    for r in regimes:
        assert r["start"] <= r["end"]
    for a, b in zip(regimes, regimes[1:]):
        assert a["end"] < b["start"], "regimes overlap"
        assert a["weekday"] != b["weekday"], "adjacent regimes share a weekday; should be merged"
    firsts = [c["first_seen"] for c in tl["lot_size_changes"]]
    assert firsts == sorted(firsts) and len(set(firsts)) == len(firsts)
    assert all(r["kind"] == "backward_roll" for r in tl["holiday_rolls"]), tl["holiday_rolls"]
    assert sum(r["n_expiries"] for r in regimes) == sum(r["n"] for r in tl["expiry_weekday_runs"])


@pytest.mark.slow
def test_every_timeline_expiry_was_quoted_on_its_own_session() -> None:
    """A contract re-dated by the exchange (Bankex Mon 2025-01-27 -> Tue 2025-01-28) never expires
    on its listed date. The scan must count only dates where an expiring series was quoted."""
    tl = _timeline()
    panel = _bankex_panel().filter(pl.col("instr") == "IDO")
    expiring = set(panel.filter(pl.col("dte_cal") == 0)["trade_date"].to_list())
    # The JSON keeps run boundaries and rolls, not every expiry; those are where a phantom would sit.
    dates = {dt.date.fromisoformat(r[k]) for r in tl["expiry_weekday_runs"] for k in ("start", "end")}
    dates |= {dt.date.fromisoformat(r["expiry"]) for r in tl["holiday_rolls"]}
    missing = sorted(d for d in dates if d not in expiring)
    assert not missing, f"timeline expiries with no expiring series quoted: {missing}"
    listed_never_expired = set(panel["expiry"].to_list()) - expiring
    assert dt.date(2025, 1, 27) in listed_never_expired  # the re-dated Monday contract


@pytest.mark.slow
def test_no_weekly_after_last_weekly_expiry() -> None:
    """Consistency, not an independent measurement: the scan's ``last_weekly_expiry`` and the
    panel's tenor split use the same rule (a month's last expiry is its monthly)."""
    last_weekly = dt.date.fromisoformat(_timeline()["last_weekly_expiry"])
    weeklies = _bankex_panel().filter(pl.col("tenor") == "weekly")
    assert weeklies.height > 0
    late = weeklies.filter(pl.col("expiry") > last_weekly)["expiry"].unique().sort().to_list()
    assert not late, f"weekly-tenor expiries after {last_weekly}: {late}"


@pytest.mark.slow
def test_weekly_removal_shows_in_expiry_spacing() -> None:
    """The independent check: spacing between actual expiries, with no month rule. Weekly listing
    keeps consecutive expiries at most 10 days apart (7 plus a holiday roll); a monthly-only
    schedule keeps them at least 27. Bankex switches once, after the 2024-11-25 monthly."""
    panel = _bankex_panel().filter((pl.col("instr") == "IDO") & (pl.col("dte_cal") == 0))
    expiries = panel["trade_date"].unique().sort().to_list()
    gaps = [(b - a).days for a, b in zip(expiries, expiries[1:])]
    first_monthly_only = expiries.index(dt.date(2024, 11, 25))
    assert max(gaps[:first_monthly_only]) <= 10
    assert min(gaps[first_monthly_only:]) >= 20
    assert expiries[first_monthly_only - 1] == dt.date.fromisoformat(_timeline()["last_weekly_expiry"])
    assert expiries[first_monthly_only + 1] == dt.date(2024, 12, 30)  # the only December expiry


@pytest.mark.slow
@pytest.mark.parametrize("min_trades", [1, A2A_MIN_FUTURE_TRADES, 10])
def test_a2a_bankex_parity_forward_matches_traded_futures(min_trades: int) -> None:
    """A2a on Bankex alone, so a thin unit cannot hide inside the pooled gate.

    Bankex is why the gate has a futures-liquidity floor, adopted after the unfiltered gate failed
    (MAD 5.22bp). Residual MAD against the futures settle is 8.2 and 8.9bp on futures with 0 and
    1-4 trades, 4.7bp at 5-49 and 2.7bp at 50+, while the parity forward's own standard error is
    0.7-1.7bp. Floors 1 and 10 are checked too, so the pass does not rest on the choice of 5.
    """
    path = settings.tables_dir / "forwards.parquet"
    if not path.exists():
        pytest.skip("no forwards built; run `uv run python -m src.iv.forward`")
    forwards = pl.read_parquet(path).filter(pl.col("symbol") == "BANKEX")
    futures = (
        _bankex_panel()
        .filter(
            (pl.col("instr") == "IDF")
            & pl.col("settle").is_not_null()
            & (pl.col("n_trades") >= min_trades)
        )
        .select("trade_date", "expiry", pl.col("settle").alias("fut_settle"))
    )
    bp = forwards.join(futures, on=["trade_date", "expiry"], how="inner").select(
        ((pl.col("forward") - pl.col("fut_settle")) / pl.col("fut_settle") * 1e4).alias("bp")
    )["bp"]
    assert bp.len() > 250, f"only {bp.len()} matched expiry-days"
    median = bp.median()
    mad = (bp - median).abs().median()
    assert abs(median) < A2A_MAX_ABS_MEDIAN_BP, f"BANKEX forward biased {median:+.2f}bp vs futures"
    assert mad < A2A_MAX_MAD_BP, f"BANKEX forward dispersion {mad:.2f}bp vs futures"

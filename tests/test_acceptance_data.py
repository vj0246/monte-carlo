"""Acceptance gates A1, A2a and A2b from DECISIONS.md D-18.

These run against the built panel rather than a fixture. A gate that passes on synthetic data
proves nothing about NSE's archives, which is where every problem in this project has come from.

Run:  uv run pytest -m acceptance
"""

from __future__ import annotations

import pathlib

import polars as pl
import pytest

from src.config import settings
from src.iv.forward import forward_for_expiry

pytestmark = [pytest.mark.acceptance, pytest.mark.slow]

#: A1: fraction of contract-day transitions where reported OI change disagrees with the realised
#: change. Measured 0.020% over 1.8M transitions, confined to two dates that carry exchange
#: restatements. The threshold leaves headroom for a few more such dates without hiding a
#: parser-level misalignment, which would break thousands of rows rather than hundreds.
A1_MAX_MISMATCH_RATE = 0.001
#: A2a: the parity forward is validated against listed futures, which the estimator never sees.
#: Measured medians: +1.51bp (NIFTY), +0.40bp (BANKNIFTY).
A2A_MAX_ABS_MEDIAN_BP = 5.0
A2A_MAX_MAD_BP = 5.0
#: A2b: dispersion of the per-strike forward within one expiry, in index points. Measured medians:
#: 3.93 (weekly), 9.00 (monthly).
A2B_MAX_MEDIAN_MAD = {"weekly": 8.0, "monthly": 15.0}


def _panel_files() -> list[pathlib.Path]:
    return sorted(settings.panel_dir.glob("fo_*.parquet"))


@pytest.fixture(scope="module")
def panel() -> pl.DataFrame:
    files = _panel_files()
    if not files:
        pytest.skip("no panel built; run `uv run python -m src.ingest.build_panel`")
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


@pytest.fixture(scope="module")
def forwards() -> pl.DataFrame:
    path = settings.tables_dir / "forwards.parquet"
    if not path.exists():
        pytest.skip("no forwards built; run `uv run python -m src.iv.forward`")
    return pl.read_parquet(path)


def test_a1_open_interest_chain_reconciles(panel: pl.DataFrame) -> None:
    """A1. The exchange reports OI and the change in OI as separate columns. They must agree.

    This replaces the originally planned reconciliation against exchange-published per-expiry OI
    totals, which NSE does not publish in the daily archive. Two independently reported columns
    agreeing across every contract is a comparable check and a stricter one: a parser that
    misaligned contracts across days would fail it on thousands of rows.
    """
    opts = panel.filter(pl.col("instr") == "IDO").sort(
        ["symbol", "expiry", "strike", "opt_type", "trade_date"]
    )
    key = ["symbol", "expiry", "strike", "opt_type"]
    chained = opts.with_columns(
        pl.col("open_int").shift(1).over(key).alias("oi_prev")
    ).filter(pl.col("oi_prev").is_not_null())
    gap = chained.select(
        (pl.col("open_int") - pl.col("oi_prev") - pl.col("chg_oi")).alias("gap")
    )
    n_bad = gap.filter(pl.col("gap") != 0).height
    rate = n_bad / gap.height
    assert rate < A1_MAX_MISMATCH_RATE, (
        f"OI chain mismatch on {n_bad}/{gap.height} transitions ({rate:.5%}); "
        "a rate this high means contracts are misaligned across days, not that the exchange "
        "restated a few figures"
    )


def test_a2a_parity_forward_matches_listed_futures(
    panel: pl.DataFrame, forwards: pl.DataFrame
) -> None:
    """A2a. Out-of-sample: nothing in the parity estimator sees a futures price."""
    futures = (
        panel.filter((pl.col("instr") == "IDF") & pl.col("settle").is_not_null())
        .select(["trade_date", "symbol", "expiry", "settle"])
        .rename({"settle": "fut_settle"})
    )
    joined = forwards.join(futures, on=["trade_date", "symbol", "expiry"], how="inner")
    assert joined.height > 1000, "too few matched expiry-days for A2a to mean anything"

    for symbol in joined["symbol"].unique():
        diff_bp = joined.filter(pl.col("symbol") == symbol).select(
            ((pl.col("forward") - pl.col("fut_settle")) / pl.col("fut_settle") * 1e4).alias("bp")
        )["bp"]
        median = diff_bp.median()
        mad = (diff_bp - median).abs().median()
        assert abs(median) < A2A_MAX_ABS_MEDIAN_BP, (
            f"{symbol}: parity forward is biased {median:+.2f}bp against listed futures; "
            "the inversion is systematically wrong"
        )
        assert mad < A2A_MAX_MAD_BP, f"{symbol}: forward dispersion {mad:.2f}bp vs futures"


def test_a2b_parity_dispersion_within_expiry(forwards: pl.DataFrame) -> None:
    """A2b. Strikes on one expiry must imply one forward, within the observed spread."""
    for tenor, limit in A2B_MAX_MEDIAN_MAD.items():
        sub = forwards.filter(pl.col("tenor") == tenor)
        if sub.is_empty():
            continue
        median_mad = sub["fwd_mad"].median()
        assert median_mad < limit, (
            f"{tenor}: median per-strike forward MAD is {median_mad:.2f} index points "
            f"(limit {limit}); strikes on one expiry disagree about the forward"
        )


def test_expiry_day_series_is_refused(panel: pl.DataFrame) -> None:
    """D-02a guard. On expiry day the settlement column holds the underlying's final settlement,
    not option prices. The forward estimator must refuse it rather than trust its caller."""
    expiring = panel.filter(
        (pl.col("instr") == "IDO") & (pl.col("dte_cal") == 0) & (pl.col("symbol") == "NIFTY")
    )
    assert expiring.height > 0, "sample should contain expiry-day rows"
    with pytest.raises(ValueError, match="D-02a"):
        forward_for_expiry(expiring, dte_cal=0)

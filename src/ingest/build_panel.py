"""Parse the raw NSE archives into tidy parquet panels.

Outputs
    data/panel/fo_<year>.parquet   index options and index futures, one row per contract per day
    data/panel/index_daily.parquet index closing levels for the treated and control indices

Two things a naive parse would miss.

Tenor classification. D-08 assumes the maturities quoted on one date share a clock; they do not. NSE
lists weeklies, three serial monthlies, quarterlies and semi-annual contracts side by side -- 18
simultaneous expiries on 2025-09-03, running out to 2030. The nearest weekly carries ~170 strikes,
the 2030 contract 24. Rule: a month's last expiry is its monthly, anything earlier that month is a
weekly, a monthly more than two calendar months out is long-dated.

Liquidity columns stay separate. ``settle`` is an exchange computation and ``close`` is the last
trade; D-02a measured them disagreeing on 663 of 663 untraded rows and 80 of 559 traded ones.
Reading a price without reading ``volume`` and ``n_trades`` alongside it is a bug.

Run:  uv run python -m src.ingest.build_panel
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import zipfile
from collections import defaultdict
from pathlib import Path

import polars as pl

from src.config import Settings, settings

log = logging.getLogger(__name__)

_SRC_COLS = [
    "FinInstrmTp",
    "TckrSymb",
    "FininstrmActlXpryDt",
    "StrkPric",
    "OptnTp",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "PrvsClsgPric",
    "UndrlygPric",
    "SttlmPric",
    "OpnIntrst",
    "ChngInOpnIntrst",
    "TtlTradgVol",
    "TtlTrfVal",
    "TtlNbOfTxsExctd",
    "NewBrdLotQty",
]
_FLOATS = {
    "StrkPric": "strike",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "PrvsClsgPric": "prev_close",
    "UndrlygPric": "spot",
    "SttlmPric": "settle",
    "TtlTrfVal": "turnover",
}
_INTS = {
    "OpnIntrst": "open_int",
    "ChngInOpnIntrst": "chg_oi",
    "TtlTradgVol": "volume",
    "TtlNbOfTxsExctd": "n_trades",
    "NewBrdLotQty": "lot_size",
}
_INDEX_OPTION, _INDEX_FUTURE = "IDO", "IDF"


def _trading_days(cfg: Settings) -> list[dt.date]:
    cal = json.loads((cfg.raw_dir / "calendar.json").read_text(encoding="utf-8"))
    return [dt.date.fromisoformat(d) for d in cal["trading_days"]]


def _classify_tenor(opts: pl.DataFrame, day: dt.date) -> pl.DataFrame:
    """Label each option expiry weekly / monthly / long_dated. See the module docstring."""
    months_out = (pl.col("expiry").dt.year() - day.year) * 12 + (
        pl.col("expiry").dt.month() - day.month
    )
    return opts.with_columns(
        pl.col("expiry").max().over(["symbol", pl.col("expiry").dt.year(), pl.col("expiry").dt.month()])
        .alias("_month_last"),
        months_out.alias("_months_out"),
    ).with_columns(
        pl.when(pl.col("expiry") != pl.col("_month_last"))
        .then(pl.lit("weekly"))
        .when(pl.col("_months_out") <= 2)
        .then(pl.lit("monthly"))
        .otherwise(pl.lit("long_dated"))
        .alias("tenor")
    ).drop("_month_last", "_months_out")


def _parse_day(cfg: Settings, day: dt.date) -> pl.DataFrame | None:
    path = cfg.raw_dir / "fo" / f"fo_{day:%Y%m%d}.csv.zip"
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    df = pl.read_csv(
        io.BytesIO(raw), columns=_SRC_COLS, schema_overrides={c: pl.Utf8 for c in _SRC_COLS}
    ).filter(
        pl.col("TckrSymb").is_in(list(cfg.option_symbols))
        & pl.col("FinInstrmTp").is_in([_INDEX_OPTION, _INDEX_FUTURE])
    )
    if df.is_empty():
        return None

    df = df.select(
        pl.lit(day).cast(pl.Date).alias("trade_date"),
        pl.col("TckrSymb").alias("symbol"),
        pl.col("FinInstrmTp").alias("instr"),
        pl.col("FininstrmActlXpryDt").str.slice(0, 10).str.to_date().alias("expiry"),
        # OptnTp is empty on futures rows; keep it null rather than an empty string.
        pl.when(pl.col("OptnTp").is_in(["CE", "PE"]))
        .then(pl.col("OptnTp"))
        .otherwise(None)
        .alias("opt_type"),
        *[pl.col(src).cast(pl.Float64, strict=False).alias(dst) for src, dst in _FLOATS.items()],
        *[
            pl.col(src).cast(pl.Float64, strict=False).cast(pl.Int64).alias(dst)
            for src, dst in _INTS.items()
        ],
    ).with_columns((pl.col("expiry") - pl.col("trade_date")).dt.total_days().alias("dte_cal"))

    opts = _classify_tenor(df.filter(pl.col("instr") == _INDEX_OPTION), day)
    futs = df.filter(pl.col("instr") == _INDEX_FUTURE).with_columns(pl.lit("future").alias("tenor"))
    return pl.concat([opts, futs], how="vertical_relaxed")


def build_options_panel(cfg: Settings = settings) -> dict[int, int]:
    days = _trading_days(cfg)
    by_year: dict[int, list[pl.DataFrame]] = defaultdict(list)
    for day in days:
        frame = _parse_day(cfg, day)
        if frame is not None:
            by_year[day.year].append(frame)

    cfg.panel_dir.mkdir(parents=True, exist_ok=True)
    written: dict[int, int] = {}
    for year, frames in sorted(by_year.items()):
        panel = pl.concat(frames, how="vertical_relaxed").sort(
            ["trade_date", "symbol", "expiry", "strike", "opt_type"]
        )
        panel.write_parquet(cfg.panel_dir / f"fo_{year}.parquet")
        written[year] = panel.height
    return written


def build_index_panel(cfg: Settings = settings) -> int:
    wanted = set(cfg.treated_indices) | set(cfg.control_indices)
    rows: list[dict] = []
    for session_idx, day in enumerate(_trading_days(cfg)):
        path = cfg.raw_dir / "idx" / f"idx_{day:%Y%m%d}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = row["Index Name"].strip()
                if name not in wanted:
                    continue
                rows.append(
                    {
                        "trade_date": day,
                        # The returns channel must walk this, never a calendar date range. Five
                        # weekend sessions exist and one moved Nifty -1.98% (D-02c).
                        "session_idx": session_idx,
                        "index_name": name,
                        "open": float(row["Open Index Value"]),
                        "high": float(row["High Index Value"]),
                        "low": float(row["Low Index Value"]),
                        "close": float(row["Closing Index Value"]),
                    }
                )
    panel = pl.DataFrame(rows).sort(["index_name", "trade_date"])
    cfg.panel_dir.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(cfg.panel_dir / "index_daily.parquet")
    return panel.height


def build_stock_futures_panel(cfg: Settings = settings) -> int:
    """Single-stock futures settlement prices.

    The index returns channel has 87 pre-regime expiry sessions and very fat tails, which is not
    enough to estimate a mean squared return -- the bootstrap interval on the expiry-day ratio
    spans 0.45 to 1.14. These ~280 stocks trade on the same sessions and give roughly 128,000
    stock-days, which is where a test of the expiry-day effect has power.
    """
    cols = ["FinInstrmTp", "TckrSymb", "FininstrmActlXpryDt", "SttlmPric", "TtlTradgVol", "OpnIntrst"]
    frames = []
    for day in _trading_days(cfg):
        path = cfg.raw_dir / "fo" / f"fo_{day:%Y%m%d}.csv.zip"
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0])
        df = pl.read_csv(
            io.BytesIO(raw), columns=cols, schema_overrides={c: pl.Utf8 for c in cols}
        ).filter(pl.col("FinInstrmTp") == "STF")
        if df.is_empty():
            continue
        frames.append(
            df.select(
                pl.lit(day).cast(pl.Date).alias("trade_date"),
                pl.col("TckrSymb").alias("sym"),
                pl.col("FininstrmActlXpryDt").str.slice(0, 10).str.to_date().alias("expiry"),
                pl.col("SttlmPric").cast(pl.Float64).alias("settle"),
                pl.col("TtlTradgVol").cast(pl.Float64).alias("vol"),
                pl.col("OpnIntrst").cast(pl.Float64).alias("oi"),
            )
        )
    panel = pl.concat(frames, how="vertical_relaxed")
    cfg.panel_dir.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(cfg.panel_dir / "stock_futures.parquet")
    return panel.height


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n_index = build_index_panel(cfg)
    log.info("index_daily.parquet: %d rows", n_index)
    written = build_options_panel(cfg)
    for year, n in written.items():
        log.info("fo_%d.parquet: %d rows", year, n)
    log.info("total contract-days: %d", sum(written.values()))
    log.info("stock_futures.parquet: %d rows", build_stock_futures_panel(cfg))


if __name__ == "__main__":
    main()

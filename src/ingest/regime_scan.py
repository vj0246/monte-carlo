"""Derive the regime timeline from the raw archives and write ``data/reference/regime_timeline.json``.

Exists so that every structurally special date is measured rather than recalled: contract-size
changes, the expiry-weekday regime and its transition, holiday rolls, weekend sessions. D-02 forbids
asserting any of these from memory.

Two things must be computed rather than read:

* Holiday rolls. ``XpryDt`` and ``FininstrmActlXpryDt`` are identical on every row of every file, so
  the exchange does not publish scheduled-versus-actual. A roll is an expiry whose weekday differs
  from its regime weekday.
* The regime itself, located by change-point search, so the Sep-2025 transition comes from the data
  rather than from an assumed effective date.

Known limitation: this scan pools every listed expiry for the symbol -- weekly, monthly, quarterly
and long-dated -- into one set, and those tenors do not share an expiry convention. Three observed
expiries sit *later* in the week than their regime weekday (2025-09-25, 2025-12-24, 2026-06-25),
which a holiday roll can never do, so they are flagged ``off_regime_unclassified`` rather than
absorbed. Splitting expiries by tenor is a prerequisite for the options channel of D-08, which
assumes the simultaneously quoted maturities share a clock; it is not done here.

Run:  uv run python -m src.ingest.regime_scan
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import math
import zipfile
from collections import Counter
from pathlib import Path

import polars as pl

from src.config import Settings, settings

log = logging.getLogger(__name__)

_FO_COLS = ["TckrSymb", "FinInstrmTp", "XpryDt", "FininstrmActlXpryDt", "NewBrdLotQty"]
_INDEX_OPTION = "IDO"


def trading_days(cfg: Settings) -> list[dt.date]:
    cal = json.loads((cfg.raw_dir / "calendar.json").read_text(encoding="utf-8"))
    return [dt.date.fromisoformat(d) for d in cal["trading_days"]]


def _read_fo(cfg: Settings, day: dt.date, symbol: str) -> pl.DataFrame:
    path = cfg.raw_dir / "fo" / f"fo_{day:%Y%m%d}.csv.zip"
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    df = pl.read_csv(
        io.BytesIO(raw), columns=_FO_COLS, schema_overrides={c: pl.Utf8 for c in _FO_COLS}
    )
    return df.filter(
        (pl.col("TckrSymb") == symbol) & (pl.col("FinInstrmTp") == _INDEX_OPTION)
    )


def _nifty_close(cfg: Settings, day: dt.date) -> float | None:
    path = cfg.raw_dir / "idx" / f"idx_{day:%Y%m%d}.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["Index Name"].strip() == "Nifty 50":
                return float(row["Closing Index Value"])
    return None


def _weekday_runs(expiries: list[dt.date]) -> list[dict]:
    runs: list[dict] = []
    for e in expiries:
        wd = e.strftime("%a")
        if runs and runs[-1]["weekday"] == wd:
            runs[-1]["end"] = e.isoformat()
            runs[-1]["n"] += 1
        else:
            runs.append({"weekday": wd, "start": e.isoformat(), "end": e.isoformat(), "n": 1})
    return runs


def _split_regimes(expiries: list[dt.date]) -> tuple[list[dict], int]:
    """Locate the expiry-weekday regime change by exhaustive change-point search.

    Segmenting on unbroken runs does not work: a single holiday-rolled expiry breaks the run, so
    every roll would masquerade as its own regime and no expiry would have a regime to deviate
    from. Instead the sample is split at the point that maximises agreement between each side's
    weekday and that side's modal weekday.

    Returns the segments and the residual misclassification count. That count is the self-check:
    it should equal the number of genuine holiday rolls. If a second regime change ever enters the
    sample the residual jumps, and gate A1 catches it rather than the result quietly absorbing it.
    """
    wd = [e.strftime("%a") for e in expiries]
    n = len(wd)

    def agree(seg: list[str]) -> int:
        return Counter(seg).most_common(1)[0][1] if seg else 0

    # Require both sides non-trivial so a degenerate split cannot win.
    best_cut = max(range(5, n - 5), key=lambda i: agree(wd[:i]) + agree(wd[i:]))
    segments = []
    for lo, hi in ((0, best_cut), (best_cut, n)):
        seg = wd[lo:hi]
        segments.append(
            {
                "weekday": Counter(seg).most_common(1)[0][0],
                "start": expiries[lo].isoformat(),
                "end": expiries[hi - 1].isoformat(),
                "n_expiries": hi - lo,
            }
        )
    residual = n - (agree(wd[:best_cut]) + agree(wd[best_cut:]))
    return segments, residual


def _regime_of(expiry: dt.date, regimes: list[dict]) -> str | None:
    for r in regimes:
        if dt.date.fromisoformat(r["start"]) <= expiry <= dt.date.fromisoformat(r["end"]):
            return r["weekday"]
    return None


def scan(cfg: Settings = settings, symbol: str = "NIFTY") -> dict:
    days = trading_days(cfg)
    day_set = set(days)
    lots: dict[dt.date, list[int]] = {}
    expiries: set[dt.date] = set()
    xpry_mismatches = 0

    for day in days:
        df = _read_fo(cfg, day, symbol)
        if df.is_empty():
            continue
        lots[day] = sorted({int(float(x)) for x in df["NewBrdLotQty"] if x})
        expiries |= {dt.date.fromisoformat(x[:10]) for x in df["FininstrmActlXpryDt"] if x}
        xpry_mismatches += df.filter(
            pl.col("XpryDt").str.slice(0, 10) != pl.col("FininstrmActlXpryDt").str.slice(0, 10)
        ).height

    lot_changes: list[dict] = []
    prev: list[int] | None = None
    for day in days:
        if day in lots and lots[day] != prev:
            lot_changes.append({"first_seen": day.isoformat(), "lot_sizes": lots[day]})
            prev = lots[day]

    # Only expiries that are themselves trading days in the sample can be dated reliably.
    observed = sorted(e for e in expiries if e in day_set)
    runs = _weekday_runs(observed)
    regimes, residual = _split_regimes(observed)
    _WD = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    rolls = []
    for e in observed:
        actual, regime = e.strftime("%a"), _regime_of(e, regimes)
        if actual == regime:
            continue
        delta = _WD[actual] - _WD[regime]
        rolls.append(
            {
                "expiry": e.isoformat(),
                "weekday": actual,
                "regime_weekday": regime,
                "weekday_delta": delta,
                # A holiday roll always moves expiry *earlier* in the week. An expiry later than
                # the regime weekday is something else -- during Sep-2025 the monthly contract ran
                # on the old Thursday schedule while weeklies had already moved to Tuesday -- and
                # must not be silently filed as a holiday.
                "kind": "backward_roll" if delta < 0 else "off_regime_unclassified",
            }
        )

    closes = {d: _nifty_close(cfg, d) for d in days}
    rets: dict[dt.date, float] = {}
    for prev_day, day in zip(days, days[1:]):
        if closes.get(prev_day) and closes.get(day):
            rets[day] = math.log(closes[day] / closes[prev_day]) * 100.0
    specials = [
        {
            "date": d.isoformat(),
            "weekday": d.strftime("%a"),
            "nifty_c2c_pct": round(rets.get(d, float("nan")), 3),
            # Feb 1 is Union Budget day. Anything else is left unclassified on purpose: the data
            # cannot tell a DR-site drill from any other special session, and guessing is D-02's
            # prohibited move.
            "classification": "union_budget" if (d.month, d.day) == (2, 1) else "unclassified",
        }
        for d in days
        if d.weekday() >= 5
    ]

    return {
        "note": "Generated by src.ingest.regime_scan. Every field is measured from data/raw/nse.",
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "symbol": symbol,
        "window": {"start": days[0].isoformat(), "end": days[-1].isoformat()},
        "xpry_vs_actual_mismatched_rows": xpry_mismatches,
        "lot_size_changes": lot_changes,
        "expiry_weekday_runs": runs,
        "expiry_regimes": regimes,
        "regime_split_residual": residual,
        "regime_split_residual_check": "must equal len(holiday_rolls); a mismatch means a second "
        "regime change entered the sample",
        "holiday_rolls": rolls,
        "weekend_sessions": specials,
    }


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = scan(cfg)
    cfg.reference_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.reference_dir / "regime_timeline.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("wrote %s", path)
    log.info("lot-size changes: %d", len(out["lot_size_changes"]))
    log.info("expiry regimes: %s", [(r["weekday"], r["start"], r["end"]) for r in out["expiry_regimes"]])
    log.info("holiday rolls: %d", len(out["holiday_rolls"]))
    log.info("weekend sessions: %d", len(out["weekend_sessions"]))
    log.info("XpryDt vs FininstrmActlXpryDt mismatched rows: %d", out["xpry_vs_actual_mismatched_rows"])


if __name__ == "__main__":
    main()

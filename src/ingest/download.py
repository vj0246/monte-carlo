"""Download NSE public archives into ``data/raw/nse/``. Idempotent, safe to re-run.

Two files per candidate date:

    fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip   F&O daily bhavcopy (UDiFF)
    idx/ind_close_all_<DDMMYYYY>.csv                     all-index daily close

A 404 on the F&O bhavcopy is not an error -- it means the exchange published nothing that day, which
is how the trading calendar is derived (D-02). No separate holiday file to fall out of sync.

Every calendar day is probed, weekends included. NSE runs occasional weekend sessions, and Budget
Sunday 2026-02-01 moved Nifty -1.98%. Filtering to weekdays to save requests would silently drop
exactly the observations the model most needs.

Run:  uv run python -m src.ingest.download
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from src.config import Settings, settings

log = logging.getLogger(__name__)

FO_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
IDX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{dmy}.csv"

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Job:
    date: dt.date
    kind: str  # "fo" | "idx"
    url: str
    dest: Path


@dataclass(frozen=True)
class Result:
    job: Job
    status: str  # "ok" | "absent" | "failed" | "cached"
    sha256: str | None = None
    nbytes: int = 0


def _jobs_for(day: dt.date, raw_dir: Path) -> list[Job]:
    ymd = day.strftime("%Y%m%d")
    dmy = day.strftime("%d%m%Y")
    return [
        Job(day, "fo", FO_URL.format(ymd=ymd), raw_dir / "fo" / f"fo_{ymd}.csv.zip"),
        Job(day, "idx", IDX_URL.format(dmy=dmy), raw_dir / "idx" / f"idx_{ymd}.csv"),
    ]


def _payload_is_real(kind: str, body: bytes) -> bool:
    """Reject a 200 that is actually a block page or an error stub.

    nsearchives can answer with HTML and a 200 status. Writing that to disk under a .zip name
    produces a corrupt archive that only surfaces much later, in the parser, as an unrelated
    exception.
    """
    if not body:
        return False
    if kind == "fo":
        return body[:2] == b"PK"
    if kind == "bse_fo":
        return body.lstrip(b"\xef\xbb\xbf")[:6] == b"TradDt"
    return body.lstrip()[:10].lower().startswith(b"index name")


def _fetch(session: requests.Session, job: Job, cfg: Settings) -> Result:
    if job.dest.exists():
        body = job.dest.read_bytes()
        return Result(job, "cached", hashlib.sha256(body).hexdigest(), len(body))

    for attempt in range(cfg.max_retries):
        try:
            resp = session.get(
                job.url,
                timeout=cfg.request_timeout_s,
                headers=cfg.bse_http_headers if job.kind == "bse_fo" else None,
            )
        except requests.RequestException as exc:
            log.warning("%s %s: %s", job.kind, job.date, exc)
            time.sleep(2**attempt)
            continue

        if resp.status_code == 404:
            return Result(job, "absent")
        if resp.status_code in RETRY_STATUS:
            time.sleep(2**attempt)
            continue
        if resp.status_code != 200:
            log.warning("%s %s: HTTP %s", job.kind, job.date, resp.status_code)
            return Result(job, "failed")

        body = resp.content
        if job.kind == "bse_fo" and body.lstrip()[:15].lower() == b"<!doctype html>":
            # BSE answers a non-session with HTTP 200 and a 14 KB HTML page, never a 404. A block
            # page would look the same, which is why download_bse cross-checks the NSE calendar.
            return Result(job, "absent")
        if not _payload_is_real(job.kind, body):
            log.warning("%s %s: HTTP 200 but payload is not a %s file", job.kind, job.date, job.kind)
            time.sleep(2**attempt)
            continue

        job.dest.parent.mkdir(parents=True, exist_ok=True)
        job.dest.write_bytes(body)
        return Result(job, "ok", hashlib.sha256(body).hexdigest(), len(body))

    return Result(job, "failed")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def download_nse(cfg: Settings = settings) -> None:
    raw_dir = cfg.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = raw_dir / "MANIFEST.json"
    calendar_path = raw_dir / "calendar.json"
    manifest: dict = _load_json(manifest_path)
    calendar: dict = _load_json(calendar_path)
    fo_status: dict[str, str] = dict(calendar.get("fo_status", {}))

    start, end = cfg.sample_start, cfg.effective_end
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    # A day already resolved as absent stays absent; the exchange does not backfill holidays.
    # Both files are skipped for such a day: no bhavcopy means no session, so no index close either.
    jobs = [
        job
        for day in days
        for job in _jobs_for(day, raw_dir)
        if fo_status.get(day.isoformat()) != "absent"
    ]
    log.info("window %s..%s: %d days, %d jobs queued", start, end, len(days), len(jobs))

    session = requests.Session()
    session.headers.update(cfg.http_headers)

    counts: dict[str, int] = {}
    with cf.ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        for result in pool.map(lambda j: _fetch(session, j, cfg), jobs):
            counts[result.status] = counts.get(result.status, 0) + 1
            # An absence is only final once the exchange has had time to publish. Probing a day
            # before its file is out and caching "no session" is how 2026-09-07 dropped out of the
            # NSE calendar; the BSE cross-check (D-22) caught it.
            unsettled = result.job.date >= dt.date.today() - dt.timedelta(days=3)
            if result.status == "absent" and unsettled:
                continue
            if result.job.kind == "fo" and result.status in {"ok", "cached", "absent"}:
                fo_status[result.job.date.isoformat()] = (
                    "absent" if result.status == "absent" else "ok"
                )
            if result.status in {"ok", "cached"}:
                manifest[result.job.dest.relative_to(raw_dir).as_posix()] = {
                    "url": result.job.url,
                    "trade_date": result.job.date.isoformat(),
                    "sha256": result.sha256,
                    "bytes": result.nbytes,
                    "downloaded_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                }

    trading_days = sorted(d for d, s in fo_status.items() if s == "ok")
    calendar_path.write_text(
        json.dumps(
            {
                "note": "A trading day is a day NSE published an F&O bhavcopy. See DECISIONS.md D-02.",
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "n_trading_days": len(trading_days),
                "trading_days": trading_days,
                "fo_status": dict(sorted(fo_status.items())),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    log.info("results: %s", dict(sorted(counts.items())))
    log.info("trading days resolved: %d", len(trading_days))
    if counts.get("failed"):
        log.error("%d jobs failed after retries; re-run to fill gaps", counts["failed"])


BSE_FO_URL = (
    "https://www.bseindia.com/download/Bhavcopy/Derivative/BhavCopy_BSE_FO_0_0_0_{ymd}_F_0000.CSV"
)
SENSEX_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/ProduceCSVForDate/w"
    "?strIndex=SENSEX&dtFromDate={start}&dtToDate={end}"
)


def download_bse(cfg: Settings = settings) -> None:
    """Sensex F&O bhavcopies and Sensex OHLC over the NSE calendar window (D-22).

    The window comes from the NSE calendar, not today's date, so both exchanges cover the same
    sessions. Every calendar day in it is probed, so the BSE calendar is derived independently and
    can be checked against NSE's. They share one holiday list; a mismatch is a download fault.
    """
    nse_cal = _load_json(cfg.raw_dir / "calendar.json")
    nse_days = set(nse_cal["trading_days"])
    start = dt.date.fromisoformat(nse_cal["window"]["start"])
    end = dt.date.fromisoformat(nse_cal["window"]["end"])
    raw_dir = cfg.bse_raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    jobs = [
        Job(d, "bse_fo", BSE_FO_URL.format(ymd=f"{d:%Y%m%d}"), raw_dir / "fo" / f"fo_{d:%Y%m%d}.csv")
        for d in days
    ]
    session = requests.Session()
    session.headers.update(cfg.bse_http_headers)
    manifest: dict = _load_json(raw_dir / "MANIFEST.json")
    status: dict[str, str] = {}
    with cf.ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        for result in pool.map(lambda j: _fetch(session, j, cfg), jobs):
            status[result.job.date.isoformat()] = result.status
            if result.status in {"ok", "cached"}:
                manifest[result.job.dest.relative_to(raw_dir).as_posix()] = {
                    "url": result.job.url,
                    "trade_date": result.job.date.isoformat(),
                    "sha256": result.sha256,
                    "bytes": result.nbytes,
                    "downloaded_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                }

    bse_days = {d for d, s in status.items() if s in {"ok", "cached"}}
    failed = sorted(d for d, s in status.items() if s == "failed")
    only_nse = sorted(nse_days - bse_days - set(failed))
    only_bse = sorted(bse_days - nse_days)

    resp = session.get(
        SENSEX_URL.format(start=f"{start:%d/%m/%Y}", end=f"{end:%d/%m/%Y}"),
        timeout=cfg.request_timeout_s,
    )
    resp.raise_for_status()
    if not resp.content.lstrip(b"\xef\xbb\xbf").startswith(b"Date,Open"):
        raise RuntimeError("Sensex OHLC request returned something other than the expected CSV")
    (raw_dir / "sensex_daily.csv").write_bytes(resp.content)
    manifest["sensex_daily.csv"] = {
        "url": resp.url,
        "sha256": hashlib.sha256(resp.content).hexdigest(),
        "bytes": len(resp.content),
        "downloaded_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }

    (raw_dir / "calendar.json").write_text(
        json.dumps(
            {
                "note": "BSE sessions, derived independently and cross-checked against NSE (D-22).",
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "n_trading_days": len(bse_days),
                "trading_days": sorted(bse_days),
                "failed": failed,
                "nse_session_missing_on_bse": only_nse,
                "bse_session_missing_on_nse": only_bse,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (raw_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    log.info("BSE sessions: %d  (NSE: %d)  failed: %d", len(bse_days), len(nse_days), len(failed))
    if only_nse or only_bse:
        log.error("calendar mismatch: NSE-only %s  BSE-only %s", only_nse, only_bse)


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    download_nse(cfg)
    download_bse(cfg)


if __name__ == "__main__":
    main()

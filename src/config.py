"""Project settings, validated at import. Failures are fatal and immediate.

Every field is overridable by environment variable with the ``VCLOCK_`` prefix, e.g.
``VCLOCK_SAMPLE_START=2024-06-01``. There is no CLI flag layer on top of this.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: First date the UDiFF F&O bhavcopy exists. Probed 2026-09-07: 2023-12-29 and earlier are 404.
UDIFF_FIRST_DATE = dt.date(2024, 1, 1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VCLOCK_", frozen=True)

    data_root: Path = PROJECT_ROOT / "data"
    results_root: Path = PROJECT_ROOT / "results"

    # Sample window. UDiFF reader only; the legacy 2023 reader is deliberately not built (D-02).
    sample_start: dt.date = UDIFF_FIRST_DATE
    sample_end: dt.date | None = None  # None means "up to today"

    # Instruments. Treated and lower-dose-treated option symbols (D-03, D-04).
    option_symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY")
    # Returns-channel-only control indices: no listed weekly options in the sample (D-04a).
    control_indices: tuple[str, ...] = (
        "Nifty Midcap 150",
        "Nifty Smallcap 250",
        "Nifty 500",
    )
    # Index levels needed for the returns channel of the treated symbols (D-09).
    treated_indices: tuple[str, ...] = ("Nifty 50", "Nifty Bank")

    # BSE (D-22): same UDiFF schema as NSE, served as plain CSV.
    bse_option_symbols: tuple[str, ...] = ("SENSEX",)
    bse_referer: str = "https://www.bseindia.com/"

    # NSE archive access. The UA and Referer are mandatory, not defensive: without them
    # nsearchives returns a block page rather than the file (D-02).
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    referer: str = "https://www.nseindia.com/"
    request_timeout_s: float = 30.0
    max_retries: int = 4
    max_workers: int = 4

    @field_validator("sample_start")
    @classmethod
    def _start_within_udiff_coverage(cls, v: dt.date) -> dt.date:
        if v < UDIFF_FIRST_DATE:
            raise ValueError(
                f"sample_start={v} predates UDiFF coverage ({UDIFF_FIRST_DATE}). "
                "2023 needs the legacy bhavcopy reader, which v1 does not build. See DECISIONS.md "
                "D-02 before widening the sample."
            )
        return v

    @property
    def effective_end(self) -> dt.date:
        return self.sample_end or dt.date.today()

    @property
    def raw_dir(self) -> Path:
        return self.data_root / "raw" / "nse"

    @property
    def bse_raw_dir(self) -> Path:
        return self.data_root / "raw" / "bse"

    @property
    def panel_dir(self) -> Path:
        return self.data_root / "panel"

    @property
    def reference_dir(self) -> Path:
        return self.data_root / "reference"

    @property
    def tables_dir(self) -> Path:
        return self.results_root / "tables"

    @property
    def figures_dir(self) -> Path:
        return self.results_root / "figures"

    @property
    def http_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": self.referer,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

    @property
    def bse_http_headers(self) -> dict[str, str]:
        return {
            **self.http_headers,
            "Referer": self.bse_referer,
            "Origin": self.bse_referer.rstrip("/"),
        }


settings = Settings()

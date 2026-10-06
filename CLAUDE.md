# CLAUDE.md - Variance Clock (Indian Index Options)

## Purpose
Estimate the weekday/expiry-day shape of variance accumulation (the "variance clock") in NSE and
BSE index options across the Nov-2024 and Sep-2025 SEBI expiry-regime changes, then price the cost
of assuming variance accrues uniformly in calendar time.

## Stack
Python 3.11+ (uv venv; system python is 3.10, do not use it). numpy, scipy, pandas, polars, pyarrow,
statsmodels, numba, pytest, matplotlib. Pydantic v2 for config. Notebooks exploration only.

## Commands
    uv sync                                   # create/refresh env
    uv run pytest -q                          # full suite
    uv run pytest -m acceptance               # the acceptance gates only (A1..A8)
    uv run python -m src.ingest.download      # NSE + BSE archives -> data/raw/{nse,bse}/
    uv run python -m src.ingest.regime_scan   # -> data/reference/regime_timeline.json
    uv run python -m src.ingest.build_panel   # data/raw -> data/panel/*.parquet
    uv run python -m src.iv.forward           # parity forwards (then src.iv.invert -> IV surface)
    uv run python -m src.clock.estimate          # weekday weights + bootstrap CIs
    uv run python -m src.analysis.expiry_effect  # mechanism tests; .permutation for D-11
    uv run python -m src.experiment.mispricing   # entry-price error; .sensitivity for the A7 surface

## Layout
    data/raw/{nse,bse}/     immutable archives + MANIFEST.json + calendar.json (BSE cross-checked)
    data/reference/         regime_timeline.json (generated), RBI policy + Budget dates
    data/panel/             parsed parquet, partitioned by year
    src/config.py           Pydantic settings, validated at import
    src/ingest/ src/iv/ src/clock/ src/analysis/ src/sim/ src/experiment/
    tests/                  test_acceptance_*.py mirror the A1..A8 gates in DECISIONS.md
    results/{tables,figures}/    the deliverable - there is no paper (D-21)
    DECISIONS.md            every modelling choice + why. Update BEFORE writing the code.

## Env
None. Public NSE archives, no credentials. Any future vendor key goes through src/config.py as a
NAME only: NSE_DATA_KEY. Never inline, never logged.

## Gotchas
- Archives need browser UA + exchange Referer. BSE answers a non-session with HTTP 200 + HTML.
- v1 sample starts 2024-01-02, UDiFF reader only. Legacy 2023 reader deliberately NOT built (D-02).
- Expiry day: settle for the *expiring* series is the underlying's final settlement, not an option
  price (146/146 rows share one value). Never invert it. D-02a.
- Settlement price != traded price. 663/663 untraded rows had settle != close; so did 80/559
  *traded* rows. Filter on `TtlNbOfTxsExctd`, not volume alone. D-06 is correctness, not hygiene.
- NO matching future for weeklies (3 futures vs ~18 option expiries). Forward = put-call parity
  *median* across strikes; not futures/spot+div/`UndrlygPric`. Mean breaks on bad settles. D-05.
- `E[IV^2]=E[IV]^2+Var(IV)`, so IV noise fakes an expiry-day weight. Measured via CE-vs-PE smile
  split: only 0.011% of variance at 2 DTE, because the vega-weighted fit averages ~50 quotes. Feed
  the clock fitted ATM values, never single quotes - one quote per expiry makes it ~5%. D-08a.
- `XpryDt` == `FininstrmActlXpryDt` on all 666 days. The exchange does NOT publish scheduled-vs-
  actual expiry; derive rolls (src/ingest/regime_scan.py). Calendar = dates a bhavcopy exists.
- Iterate the returns channel over the trading-day sequence, never calendar dates. 5 weekend
  sessions exist; Budget Sun 2026-02-01 moved Nifty -1.98%. Dropping it reassigns that to Monday.
- Lot size is a staircase (50->25->25&75->75->65&75->65); two changes land in event windows. D-10.
- Stock-days are not independent: one session shares the market factor. Bootstrap by session, never
  by stock-day; iid resampling gave CIs ~4x too narrow. D-08b.
- Sep-2025: Nifty Thu->Tue, Sensex Tue->Thu (measured). Bank Nifty moved with Nifty: treated, not
  a control. Causal language only if the Nifty-minus-Sensex DiD passes D-11. D-03, D-04, D-22.
- Sensex clock is thin: 39/48 three-month fits degenerate. Never read Sensex weights below a regime.
- Dates: naive IST, never localised. Any date from memory is wrong until measured (D-02).

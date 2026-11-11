# What is left

Status (2026-11-11): the planned work is done. Seven of eight acceptance gates pass, A4 is a
reported null, and A8 is covered by the clean-clone run below. What remains is two decisions only
you can take, and optional work in order of value.

---

## 1. Decisions only you can take

### 1.1 Buy intraday data? (budget)

The only route to the mechanism behind the Nifty expiry dip. Daily data has now ruled out
everything it can see: settlement averaging has no support, max-pain pinning is contradicted, and
the Bank Nifty dose-response is null (D-32). The remaining hypotheses (dealer gamma, expiry-day
positioning) are intraday phenomena. One-minute index and option data from 2024-01 would also
make the returns channel roughly an order of magnitude more precise (D-09).

### 1.2 Build the legacy 2023 reader?

D-02 pre-committed to it if D-11 came out thin. It did, and D-32 found only 3 disjoint pseudo
designs. About 250 more sessions, more pseudo windows for D-11, and a pre-Sep-2023 Bank Nifty
weekly event. Probably does not overturn the null on its own. About two days.

---

## 2. Reproducibility (A8)

`git clone` into a clean directory, `uv sync`, and the pipeline in README "Run it". The fresh
`results/tables/*.manifest.json` must carry the cloned commit and the same `config_hash`.
Attempted 2026-11-11 (script: clone, copy `data/raw`, verify, rerun, compare): the clone at
`cb6f3cf` built its environment, all 2,000 raw archives matched their MANIFEST sha256, and
`regime_scan` ran; the run was then stopped for low system memory during `build_panel`.
Not completed. Rerun on an otherwise idle machine (close Chrome; the surface
build needs several GB).

---

## 3. Optional work, by value

1. **Extend the sample past 2026-09-04.** Rerun `src.ingest.download` and the pipeline. Marks the
   2026-10-07 RBI date and adds about two months to the Tuesday regime. An hour, mostly download.
2. **Futures STT on the delta hedge** (D-30). Path-dependent, so it does not cancel the way option
   STT does. A small addition to the hedging experiment. Half a day.
3. **Bankex and Bank Nifty returns channels** (D-31, D-32). Needs Bankex index closes from BSE.
   Half a day.
4. **Options-channel interval on the dose ratio** (D-32). `fit` now returns bootstrap draws, so the
   Sep-2025 R = 0.75 can get an interval. An hour.

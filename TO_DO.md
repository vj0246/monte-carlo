# What is left, and who does it

Status: seven of eight acceptance gates pass, A4 is a reported null, A8 is partial by construction.
The code is complete for v1. What remains is three things only you can do, four decisions I need from
you, and a list of optional work I can pick up on a word.

Nothing here is blocked on me.

---

## 1. Only you can do these

### 1.1 RBI policy dates (30 minutes, blocks D-07)

The macro-event dummy currently covers Union Budget days only. RBI monetary policy announcement
dates are not derivable from exchange archives, and D-02 forbids me asserting dates from memory.

Open `data/reference/macro_events.json` and fill the `rbi_mpc` array with ISO dates from RBI press
releases, for every announcement between **2024-01-01 and 2026-09-04**:

```json
"rbi_mpc": ["2024-02-08", "2024-04-05", "..."]
```

Then tell me, and I re-run the clock. Why it matters: RBI days carry large scheduled variance, and
with the dummy empty that variance is silently absorbed into whichever weekday the announcements
tend to fall on. It is the one known contaminant of the weekday weights still in the pipeline.

### 1.2 Clean-clone reproducibility run (about 90 minutes, mostly waiting; completes A8)

A8 asks that a clean machine reproduce the committed results. I cannot test that from inside this
process, so the automated gate covers provenance and seeded determinism and this step covers the
rest. Run it once:

```bash
git clone "C:\Users\vivaa\OneDrive\Desktop\Personal Projects\Monte Carlo" /tmp/ghadi-check
cd /tmp/ghadi-check
uv sync
uv run python -m src.ingest.download        # ~880 MB, the slow part
uv run python -m src.ingest.regime_scan
uv run python -m src.ingest.build_panel
uv run python -m src.iv.forward
uv run python -m src.iv.invert
uv run python -m src.iv.smile
uv run python -m src.experiment.sensitivity
uv run pytest -q
```

Then compare the fresh `results/tables/sensitivity.manifest.json` against the committed one: the
`config_hash` must match, and `git_commit` must be the commit you cloned. Send me any mismatch.

Close Chrome first. The machine has 15.7 GB and Chrome was holding about 3 GB; the surface build was
killed twice for low memory before I made it lazy.

### 1.3 Decide whether to buy intraday data (budget decision)

This is the only route to settling the central open question. Nifty expiry sessions are measurably
quieter, and neither candidate explanation survived: settlement averaging has no support, max-pain
pinning is contradicted. Both remaining hypotheses (dealer gamma, expiry-day positioning) are
intraday phenomena that daily close-to-close data cannot see.

Decide yes or no on one-minute index and option data covering 2024-01 to date. If yes, give me
access and I will re-run the mechanism tests. If no, say so and the limitation gets written as
permanent rather than pending.

---

## 2. Decisions I need from you

| # | Decision | Default if you say nothing |
|---|---|---|
| 2.1 | Build the legacy 2023 reader? D-02 pre-committed to it if D-11 came out thin, and it did. It buys ~250 sessions and takes independent pseudo-windows from about 5 to 7, which probably will not change the null. | Not built |
| 2.2 | Push to a remote? There is no git remote. If you want this on GitHub, create an empty repo and give me the URL. Five commits are ready. | Stays local |
| 2.3 | Keep results as parquet only (D-21), or add a short write-up and figures? You said project folder only, before the gates existed. Worth reconfirming now. | Parquet only |
| 2.4 | Sign off on the researcher-chosen numbers, or change them: D-06 liquidity floors (volume > 0, 5 trades, 500 open interest, 15% moneyness), the A5 allowance (0.3% of price), and the D-11 window lengths (63 and 42 sessions). Each already carries a sensitivity panel or a documented measurement. | Keep as is |

---

## 3. Optional work I can do next

Ordered by value for effort. Say the word on any of them.

1. **Heston robustness sweep (half a day).** D-12 planned the clock conclusion to be re-tested under
   stochastic volatility. The engine now exists and is validated against Fourier prices (A5), so this
   is a sweep over `(rho, xi)` and a table. It answers "does the 2% hedging number survive leverage
   and vol-of-vol?" and it is the last unbuilt piece of the original plan.
2. **BANKEX as a second BSE unit (half a day).** Already in the downloaded BSE files. Gives a
   lower-dose treated unit on the BSE side, the mirror of Bank Nifty on NSE.
3. **Bank Nifty dose-response at Nov-2024 (half a day).** D-04 noted the treated/control roles swap
   at that event and the code supports it, but the estimate was never run.
4. **Securities transaction tax on exercise (a day).** D-17 excluded it. It is large in Indian index
   options and interacts with expiry-day pinning, so it plausibly changes the entry-price number.
5. **Figures (two hours).** Everything is parquet right now. Weekday weights with intervals, the
   sensitivity surface, the permutation distribution.

---

## 4. What is already done

Gates: A1 open-interest chain (99.980% over 1.8M transitions), A2a forward vs listed futures
(+1.51 bp Nifty, -1.22 bp Sensex), A2b per-strike dispersion, A3 conditioning, A5 Heston MC vs
Fourier over 20 parameter sets, A6 hedging vs the EKJS closed form, A7 the 16-cell sensitivity
surface. A4 ran and reports a null, which is a result and not a gap. 22 tests pass.

Findings, in one line each. Nifty expiry sessions carry significantly less variance and that is
stable across half-samples. Sensex does not replicate it. Whether the Sep-2025 rule caused Nifty's
shift is untested and, with Sensex too thin to use as a control, stays that way. The clock costs a
calendar-time trader about 22% on a one-session option and almost nothing over a full week, and it
moves hedging P&L dispersion by about 2% at every rebalance frequency and cost in the grid.

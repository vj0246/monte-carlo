# Closing out the project

Everything that Claude can do without your input is done. Four things remain, and only you can do
them. Do them in this order. When all boxes are ticked, the project is finished and can be archived.

Decisions D-25 already settled the optional work: no legacy 2023 reader, parquet-only output, current
thresholds kept. Do not reopen those to finish.

---

## Step 1. RBI policy dates (30 minutes)

Why: the macro-event dummy covers Budget days only. RBI announcement days are the one known
contaminant of the weekday weights still in the pipeline (D-07).

- [ ] Open the RBI press releases for every Monetary Policy Committee announcement from
      **2024-01-01 to 2026-09-04**. Use the RBI website, not memory (D-02).
- [ ] Fill the `rbi_mpc` array in `data/reference/macro_events.json` with ISO dates:
      `"rbi_mpc": ["YYYY-MM-DD", "..."]`
- [ ] Re-run the clock and the headline numbers:

```bash
uv run python -m src.clock.estimate
uv run python -m src.analysis.expiry_effect
uv run python -m src.experiment.mispricing
uv run pytest -q
```

- [ ] Compare the new weekday weights with the table in `README.md`. If they moved, update the
      README numbers and add a line to `DECISIONS.md` under D-07 with the before and after.
      If they did not move, add one line to D-07 saying so. Delete the RBI entry under "Open".

## Step 2. Clean-clone reproducibility run (about 90 minutes, mostly waiting)

Why: gate A8 needs a clean machine to reproduce the results. This cannot be tested from inside
a Claude session.

- [ ] Close Chrome first (the surface build was killed twice for low memory).
- [ ] Run:

```bash
git clone https://github.com/vj0246/monte-carlo /tmp/ghadi-check
cd /tmp/ghadi-check
git checkout claude/task-lfj1dg      # or main, once merged
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

- [ ] Compare `results/tables/sensitivity.manifest.json` from the clone against the one on your
      original machine. `results/` is not tracked in git, so the original lives only on that
      machine. `config_hash` must match, and `git_commit` must be the commit you cloned.
- [ ] Match: write "A8 passed, clean clone, <date>" in `DECISIONS.md`.
      Mismatch: do not close. Send the two manifests and the `pytest` output for diagnosis.

## Step 3. Intraday data: yes or no (5 minutes, a budget decision)

Why: it is the only route to explaining why Nifty expiry sessions are quieter. Daily data cannot
separate the two remaining hypotheses (dealer gamma, expiry-day positioning).

- [ ] **No** (the way to end the project): add this under D-25 in `DECISIONS.md`:
      "Intraday data not purchased. The mechanism behind the Nifty expiry-session effect is an
      open limitation, permanent for this project." Nothing else is needed.
- [ ] **Yes**: the project is not closed. Buy one-minute index and option data for 2024-01 to date
      and start a new phase.

## Step 4. Wrap up (10 minutes)

- [ ] Run `uv run pytest -q`. All tests must pass.
- [ ] Run `git status`. It should be clean.
- [ ] Merge `claude/task-lfj1dg` into `main` (open a PR on GitHub, or merge locally).
- [ ] Tag the result: `git tag v1.0 && git push origin v1.0`
- [ ] Delete `TO_DO.md` and this file, or change their first line to "Project closed <date>".
- [ ] Optional: archive the GitHub repo (Settings, Danger Zone, Archive).

---

## Definition of done

The project is done when Steps 1, 2 and 3 (answer "No") are ticked and Step 4 is complete.
Findings stand as written in `README.md`: Nifty expiry sessions carry less variance, Sensex does not
replicate it, and whether the Sep-2025 rule caused the shift is untested and stays that way.

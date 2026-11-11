# Ghadi

*घड़ी - clock.*

Market variance does not accumulate evenly across the days of a week. This measures the shape of
that accumulation in Indian index options (the **variance clock**) across two SEBI rule changes that
moved expiry days by regulation rather than by market choice, and then prices what it costs to
ignore it.

- **Nov 2024**: one weekly index expiry per exchange. Bank Nifty, Fin Nifty and Midcap weeklies
  ceased to exist.
- **Sep 2025**: expiry days separated. Nifty moved Thursday → Tuesday; Sensex moved Tuesday →
  Thursday on the same date.

## The result

Open **`results/report.html`** (one file, works offline) for every figure with its numbers.
Rebuild it with `uv run python -m src.report.build`.

**1. On Nifty, the expiry session is the lowest-variance session of the week, and that holds up.**
Weekday weights from the option surface, normalised to average one, 300 bootstrap draws over dates:

| Regime | Expiry weekday | Weight | 95% CI | With RBI/Budget dummy |
|---|---|---|---|---|
| Thursday, to 2025-08-28 | Thu | **0.699** | [0.483, 0.889] | 0.771 [0.620, 0.932] |
| Tuesday, from 2025-09-02 | Tue | **0.607** | [0.298, 0.844] | 0.645 [0.351, 0.878] |

Half-sample refits point the same way in all four halves and three of four intervals exclude 1.
Scheduled macro days (17 RBI policy announcements sourced from rbi.org.in, 3 Budgets) carry about
twice an average session's variance; controlling for them leaves the expiry dip in place and removes
the Thursday-regime Friday premium, which was macro variance (D-26).

**2. It does not generalise.** Sensex's own expiry session is flat or high where its clock is
identified. Bank Nifty's own expiry is never significantly low, and removing its weekly in Nov-2024
did not measurably change it (pre-registered DiD +0.19 [-0.71, +1.09], D-32). Bankex cannot be
estimated at all: it quotes one liquid maturity per day (D-31).

**3. Whether the Sep-2025 rule caused Nifty's shift is untested.** The pre-registered
randomization test is null (p = 0.12 at both window lengths, D-11), and the design has only about
five independent windows.

**4. In the price, the clock is a one-session phenomenon.** A trader who assumes variance accrues
evenly misprices a Tuesday-expiry straddle opened on Monday by **-21.8%**, and almost nothing over a
full week. Carried through 300 bootstrap clocks, the one-session error stays negative in every draw (band -45% to -8%), while every span of two or more sessions is indistinguishable from zero (D-29). The number does not move under stochastic volatility (at most 0.006 percentage
points across the Heston grid, D-28) or securities transaction tax (it cancels exactly, D-30).

**5. In the hedge, the clock is a floor that rebalancing cannot remove.** Hedging a weekly
straddle on the wrong clock leaves a risk that converges to **12.17 index points** (3.3% of the
premium) as rebalancing becomes continuous, while discretisation error falls as 145/sqrt(m). Beyond
about **140 hedges per session** the wrong clock is a calendar-time hedger's largest risk. The
floor is a closed form, confirmed by multilevel Monte Carlo (12.15 +/- 0.02) and unchanged under
stochastic volatility (11.6 to 11.8 across the Heston grid). The mean effect is exactly zero on
every grid: the cost is dispersion, not drift (D-25, D-27).

An earlier version of this README reported the hedging effect as 2.3% of hedging risk, "swamped by
rebalancing". That came from a delta hedge with the wrong sign (D-25).

**Mechanisms.** Max-pain pinning does not hold: spot diverges from the max-pain strike at roughly
the random-walk rate. Settlement averaging has no support (0.852 vs 0.820, intervals overlap), but
the test is too weak to reject it. What remains (dealer gamma, expiry-day positioning) needs
intraday data.

## The Monte Carlo layer

Every simulator is checked against something it cannot fake before its numbers are used:

| Method | Checked against |
|---|---|
| Time-changed GBM delta hedging | pathwise L2 convergence to the EKJS integral at rate m^-1/2 (gate A6); exact mean zero on every grid |
| Multilevel MC (Giles), exact Brownian coupling | closed-form floor from Gaussian-kernel integrals of dollar gamma |
| Heston QE (Andersen) | Lewis/Fourier prices over 20 random parameter sets (gate A5) |
| Piecewise-clock Heston characteristic function (Riccati recursion) | plain Heston at w = 1, a complex ODE solver, Black-76 as vol-of-vol goes to 0, Gil-Pelaez inversion |
| Conditional MC (Willard), control variates, scrambled Sobol with PCA ordering | exact Fourier prices; RMSE slope -1.09 against -0.5, about 8,000x the work-normalised efficiency of plain MC |
| Nested MC over 300 bootstrap clocks, common random numbers | exact per-clock second moments; law-of-total-variance split |

Antithetic sampling is measured, and it loses (0.5x): a straddle payoff is nearly even in the
driving normal, so a path and its mirror pay almost the same.

## Status

| Stage | State |
|---|---|
| NSE + BSE ingest (Nifty, Bank Nifty, Sensex, Bankex), panels, regime scan | done |
| Forwards, IV surface, ATM variance | done |
| Clock fits persisted with bootstrap draws, RBI/Budget macro control | done (D-26) |
| Mechanism tests, Bank Nifty dose-response | done; dose-response null (D-32) |
| Randomization inference (D-11) | run: **kill condition fires** |
| Nifty-minus-Sensex DiD (D-22) | **not evaluable**: Sensex too thin |
| Hedging experiment, sensitivity surface, Heston sweep | done, corrected after D-25 |
| Continuous-hedging limit (MLMC + closed form) | done (D-27) |
| Entry price under stochastic vol, terminal MC benchmark | done (D-28) |
| Clock uncertainty propagated (nested MC) | done (D-29) |
| STT | done; cancels from the price error (D-30) |
| Figures and report | done (D-33) |
| Legacy 2023 reader, intraday data | not built; see TO_DO.md |

Gates A1, A2a, A2b, A3, A5, A6, A7 and A8 pass; A8 by a clean-clone rerun whose tables are
bit-identical to the local ones. A4 ran and reports a null (D-11).
See [DECISIONS.md](DECISIONS.md) D-18 for the gates.

## Run it

```bash
uv sync
uv run python -m src.ingest.download          # NSE + BSE archives, idempotent (~880 MB)
uv run python -m src.ingest.regime_scan       # regime timelines, Nifty, Sensex, Bankex
uv run python -m src.ingest.build_panel       # raw -> parquet panels
uv run python -m src.iv.forward               # put-call parity forwards
uv run python -m src.iv.invert                # Black-76 implied vol surface
uv run python -m src.iv.smile                 # vega-weighted ATM total variance
uv run python -m src.clock.estimate           # the clock
uv run python -m src.analysis.expiry_effect   # mechanism tests
uv run python -m src.analysis.permutation     # randomization inference, incl. DiD
uv run python -m src.experiment.mispricing    # what the clock is worth
uv run python -m src.experiment.sensitivity   # A7 surface, with a provenance manifest
uv run python -m src.experiment.heston_robustness   # does the result survive stochastic vol
uv run python -m src.clock.regimes            # every regime fit + bootstrap draws (feeds the rest)
uv run python -m src.analysis.dose_response   # Bank Nifty dose-response
uv run python -m src.experiment.hedging_limit # MLMC floor, about 20 minutes
uv run python -m src.experiment.heston_entry  # entry price under Heston, MC efficiency
uv run python -m src.experiment.clock_uncertainty  # nested MC over bootstrap clocks
uv run python -m src.experiment.stt           # securities transaction tax
uv run python -m src.report.build             # results/figures/*.png and results/report.html

uv run pytest -q                              # all tests
uv run pytest -m acceptance                   # the gates only
```

No credentials. All data is public exchange archives. Everything under `data/` and `results/` is
regenerated by the commands above and is not committed, except `data/reference/`.

## Layout

```
src/ingest/     download (NSE, BSE), regime scan, panel build
src/iv/         forward, Black-76 inversion, smile fit
src/clock/      weight estimation, every regime fit with its bootstrap draws
src/analysis/   mechanism tests, randomization inference, dose-response
src/sim/        time-changed GBM, Heston QE and Fourier (plain and clocked), EKJS, MLMC, terminal MC
src/experiment/ mispricing, hedging, the continuous limit, Heston, uncertainty propagation, STT
src/report/     figures and the HTML report
tests/          acceptance gates and unit tests
DECISIONS.md    every modelling choice and why
```

## Things that will bite you

- **On expiry day, the settlement column holds the underlying's final settlement, not an option
  price.** All 146 expiring NIFTY rows on one sample date share a single value. Never invert it.
- **Settlement price is not a traded price.** For untraded strikes it is an exchange model output:
  663 of 663 disagreed with the close. Even 80 of 559 *traded* rows disagreed.
- **Weeklies have no matching future.** Three monthly futures against ~18 option expiries. The
  forward comes from the put-call parity *median* across strikes; the mean swallows bad settlements.
- **BSE never 404s a holiday.** It returns HTTP 200 with an HTML page, which looks exactly like a
  block page. The BSE calendar is cross-checked against NSE's for that reason, and on its first run
  the check caught an NSE download that had cached a real session as a holiday.
- **Stock-days are not independent observations.** Resampling them as if they were gave intervals
  about four times too narrow. Bootstrap by session.
- **A delta hedge must lower risk.** Check that a hedged P&L sd falls like m^-1/2 before trusting
  any hedging number; a sign error once survived 23 passing tests because every check compared means.
- **Sensex clock fits on short windows are mostly degenerate.** Do not read Sensex weights from
  anything shorter than a full regime.

## Known gaps

- The causal question needs either more pseudo windows (the legacy 2023 reader), a thicker control
  than Sensex, or intraday data.
- Futures STT on the delta hedge is path-dependent and unmeasured (D-30).
- The NSE calendar in `data/raw` ends 2026-09-04; the 2026-10-07 RBI date is unmarked until it is
  extended.

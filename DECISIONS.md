# Decisions

Every modelling choice and why. Written before the code that depends on it. If code and this file
disagree, this file is the bug report.

`SET` decided · `OPEN` blocking · `PROV` defaulted, revisit if evidence contradicts

---

## D-01 · Estimand — SET

Daily variance weights `w(d)`, normalised to mean one, indexed by (weekday, is-expiry-day,
has-macro-event). Business time is `V(t) = sigma_bar^2 * sum_{d<=t} w(d)`. Null is `w == 1`.

Mean-one normalisation makes the clock a pure reshaping and leaves the level to a separate scale
parameter. Without that split the headline number would confound "wrong shape" with "wrong level",
and level errors are a different problem.

**Falsifier:** bootstrap intervals that all straddle 1.0. That is a publishable null.

## D-02 · Data — SET

Public NSE archives only, no credentials. Files land immutable under `data/raw/nse/` with a
`MANIFEST.json` (url, timestamp, sha256). Probed live 2026-09-07; nothing below is from memory.

| Source | URL pattern | Coverage |
|---|---|---|
| F&O bhavcopy, UDiFF | `nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip` | 2024-01-02 on |
| F&O bhavcopy, legacy | `.../content/historical/DERIVATIVES/<YYYY>/<MON>/fo<DDMMMYYYY>bhav.csv.zip` | to 2024-07-03 |
| All-index close | `.../content/indices/ind_close_all_<DDMMYYYY>.csv` | both eras |

Needs a browser `User-Agent` **and** `Referer: https://www.nseindia.com/`. No cookie handshake.

**Sample: 2024-01-02 onward, UDiFF reader only.** Covering 2023 costs a second parser plus a
reconciliation gate to buy ~250 days. We already get 10 months before Nov-2024, 20 before Sep-2025,
and ~400 candidate pseudo-event dates for D-11. Build the legacy reader only if D-11 comes out thin.

**Trading calendar** = the set of dates a bhavcopy exists. Self-consistent, no holiday file to drift.

**Retracted 2026-09-07:** an earlier note claimed `FininstrmActlXpryDt` vs `XpryDt` gives the
holiday roll. They are identical on every row of all 666 files — 0 mismatches. The exchange
publishes the actual expiry twice. Rolls are derived instead (D-02b). This was assumed from a
column name before checking, which is the failure this decision exists to prevent.

## D-02a · What the settlement column actually is — SET

Measured on `fo15JUN2023bhav.csv` (50,591 rows) and `BhavCopy_..._20250903_...csv` (28,551 rows).

1. **On expiry day the settlement column for the expiring series is the underlying's final
   settlement, not an option price.** All 146 expiring NIFTY rows on 15-Jun-2023 carry the same
   value, 18688.1, across every strike and both types. Inverting it yields garbage, once a week,
   on the day the effect under study is largest.
2. **For untraded strikes, settlement is an exchange model output.** 663 of 663 untraded rows have
   `close != settle`. Strike 8500 CE against an 18745.45 future settles at 10211.05 — discounted
   intrinsic.
3. **Traded rows are not automatically safe.** 80 of 559 traded rows also have `close != settle`,
   because settlement comes from the closing window, not the last trade.

Panel keeps `close`, `settle`, `volume`, `n_trades`, `open_int` as separate columns and never
collapses them.

## D-02b · Measured regime timeline — SET

From `src.ingest.regime_scan` over 666 trading days → `data/reference/regime_timeline.json`.

**Expiry weekday**, located by change-point search, not an assumed effective date:

| Regime | Span | Expiries |
|---|---|---|
| Thursday | 2024-01-04 → 2025-08-28 | 87 |
| Tuesday | 2025-09-02 → 2026-09-01 | 56 |

Sep-2025 pins to the gap between 2025-08-28 and 2025-09-02. Split residual equals the off-weekday
expiry count exactly (11 = 11) — the self-check. A second regime change would make them diverge.

**Off-weekday expiries: 8 backward rolls, 3 not rolls.** A holiday roll only moves expiry *earlier*.
2025-09-25 (+2), 2025-12-24 (+1) and 2026-06-25 (+2) sit later, so they are non-weekly tenors, and
are flagged `off_regime_unclassified` rather than filed as holidays.

**Contract size is a staircase:** 50 → 25 (2024-04-26) → 25 *and* 75 quoted together
(2024-11-22 → 2025-01-31) → 75 → 65 and 75 (2025-10-29) → 65.

**Weekend sessions: 5.** 2024-01-20, 2024-03-02, 2024-05-18 (Nifty moved 0.16–0.23%);
2025-02-01 Budget Sat (−0.11%); **2026-02-01 Budget Sun (−1.98%, |z| = 2.3)**. Volumes are
comparable to weekdays, so these are real sessions. The three 2024 Saturdays stay `unclassified` —
the data cannot tell a DR drill from any other special session.

**Caveat:** the scan pools weekly, monthly, quarterly and long-dated expiries, which share no
schedule. Splitting by tenor is a prerequisite for D-08 and is done in `build_panel`.

## D-02c · Weekend sessions — SET

Kept in the panel and the return chain. Excluded from the weekday vector, given their own
indicator.

They cannot carry a weekday weight: four Saturdays and one Sunday is not an estimate. They also
cannot be dropped — deleting 2026-02-01 does not delete its −1.98% move, it transfers it to the
next Monday's close-to-close return. Dropping a session is a reassignment, not a removal.

The Budget dummy attaches to the session the Budget was presented in. In 2026 that was a Sunday.

## D-03 · Scope — SET

NSE only. Treated: Nifty 50. **BSE Sensex deferred to v2.**

Sensex was the only unit with opposite-signed treatment at Sep-2025, so v1 has no cross-sectional
control for the headline event. **The Sep-2025 result is an interrupted time series, not a
difference in differences.** It supports "the clock moved when the rule moved" and not "the rule
caused it". The writeup must say exactly that.

What carries identification instead: the D-11 permutation test (now load-bearing, not a formality)
and the D-04a control indices.

## D-04 · Bank Nifty is treated, not a control — SET

Sep-2025 moved the expiry day for all NSE index derivatives, monthlies included, so Bank Nifty
moved with everything else. A unit treated in the same direction at smaller magnitude is not a
control; using it as one biases toward zero and yields a defensible-looking wrong null. It enters
as a lower-dose treated unit with a dose-response prediction.

At Nov-2024 the roles swap: Bank Nifty is treated (weekly removed), Nifty is the control (weekly
survived). Treated/control is a property of the event, not the symbol — code must not hardcode it.

Confirmed in the panel: **zero BANKNIFTY weekly rows after 2024-11-13.**

## D-04a · Within-NSE control — SET

Returns-channel-only panel from NSE cash indices with no listed weekly options across the sample:
Nifty Midcap 150, Nifty Smallcap 250, Nifty 500. Never inverted for IV.

Answers "did Indian equity indices in general shift weekday variance around Sep-2025 for unrelated
reasons?" Costs one small parser. Imperfect — these are correlated with Nifty and touched by
index-level hedging flow — so they bound the confound rather than remove it, and do not substitute
for Sensex.

## D-05 · Forward from put-call parity — SET (revised 2026-09-07)

**The original decision was unimplementable.** It required the matching-expiry future. NSE lists
only three monthly index futures against ~18 quoted option expiries: 15 have no future, including
*every weekly*, which is the object of the project.

For each (date, expiry), every liquid strike gives an independent forward estimate
`F_k = K + (C-P)/D`. **Take the median.**

Not the mean. A fixed-slope least-squares fit computes the mean, and settlement prices on thin
strikes violate no-arbitrage outright: on 2025-07-17 the 25050 call settled at 548.35 while the
25000 call settled at 414.20, on 6 and 22 trades. Two such strikes pulled the OLS forward 21 points
off a tight cluster formed by the other six.

| | OLS | median |
|---|---|---|
| weekly dispersion (index pts) | 16.90 | **3.93** |
| monthly dispersion | 41.16 | **9.00** |

**`D` is fixed at `exp(-r*T)`, `r = 6.5%`, declared an assumption not an estimate.** Free-slope fits
returned discount factors above 1.0 (negative rates) on 3 of 12 expiries; a shared-rate fit gave
11.98%, −1.03%, 2.90%, 8.86% on four dates whose true rate barely moved. The rate is not in this
data. It also does not matter: `F` moved 0.04 index points between `r = 0` and `r = 11.98%`.

Rejected: interpolating the forward between monthly futures. That imposes a smooth term structure
on the very quantity whose short-horizon shape is under investigation.

Never spot-plus-dividend, never UDiFF's `UndrlygPric` (that is spot).

## D-06 · Liquidity filters — PROV

Keep a quote only if `volume > 0`, `n_trades >= 5`, `open_int >= 500`, `|log(K/F)| <= 0.15`, and at
least 2 trading sessions to expiry. The expiry session is excluded and held out.

This is correctness, not hygiene — see D-02a. `n_trades` rather than volume alone because one lot
traded at 10:15 does not make the 15:30 settlement a market price. Thresholds are chosen, not
derived, so every headline carries a sensitivity panel across them. If the sign of a result depends
on them, the result does not exist.

## D-07 · Clock parameterisation — SET

`log w(d) = a[weekday] + e*1[weekend session] + b*1[expiry day] + c*1[macro event]`, mean-one over
the window, positive by construction.

Log-linear gives positivity without a constrained solver and makes the expiry effect multiplicative,
which is how a desk quotes it. The macro dummy is not optional: RBI and Budget days carry large
scheduled variance, and omitting them dumps that variance onto whichever weekday they fall on.

Rejected: a free weight per calendar day. Fits everything, identifies nothing.

**OPEN:** `data/reference/macro_events.json` has Union Budget days only. RBI MPC dates are not
derivable from NSE archives and D-02 forbids asserting them. Until supplied, RBI variance leaks
into weekday weights.

## D-08 · Options-channel identification — SET

Pooled panel with one nuisance scale per (date, symbol), profiled out by within-group demeaning
rather than carried as thousands of free parameters:

    log(IV^2 * tau) = log s_t + log( sum_{d in (t,T]} w(d) )

A single date supplies 3–6 maturities against 7 parameters — underdetermined. The original plan's
claim that one date *overdetermines* the system is wrong. Identification comes from variation
across dates in which weekdays remain: from a Monday, a Tuesday-expiry week has a different weekday
mix than from a Wednesday. Pooling is what makes it full rank.

Gate A3 reports the condition number and VIFs, because weights off an ill-conditioned design are
noise wearing a confidence interval.

## D-08a · IV noise inflates variance — SET

`E[IV^2] = E[IV]^2 + Var(IV)`, so measurement noise inflates total variance, and noise is largest at
the shortest maturities — exactly where the expiry day dominates the remaining-day set. Noise alone
would produce an apparent expiry-day weight above one.

**First estimate was wrong by three orders of magnitude.** Taking `Var/mean^2` from the per-quote
smile residual gave 5.4% at two sessions vs 0.5% at thirteen. That is the error on a *single quote*.
The clock consumes one fitted ATM value per expiry-day, across ~50 quotes.

Measured properly by fitting calls and puts separately — under parity they must agree, so their
difference is measurement error. Across 3,772 NIFTY expiry-days:

| dte | n | empirical SE(ATM) | iid formula | ratio | noise share of variance |
|---|---|---|---|---|---|
| 2–3 | 280 | 0.141% | 0.115% | 1.2x | **0.011%** |
| 4–6 | 417 | 0.091% | 0.069% | 1.3x | 0.006% |
| 7–12 | 830 | 0.085% | 0.062% | 1.4x | 0.005% |
| 13+ | 2245 | 0.130% | 0.091% | 1.4x | 0.010% |

Errors are close to independent across strikes. Differential bias ≈ 0.006 percentage points of
variance, against a plausible effect measured in tens of percent. Negligible.

The threat was real; the vega-weighted smile fit is what defeats it. Feed the clock fitted ATM
values, never single quotes. The closed-form correction stays in the code because it is free.

**Requirements:** vega weighting (error scales as price-error/vega, so weight by `vega^2`);
subtract `Var(atm_iv_hat)`; report the clock with and without the correction.

## D-08b · First estimates — FINDING, not yet a result

**The plan assumed variance accumulates faster on expiry days. It accumulates slower.** Two channels
sharing no data agree.

Options channel, NIFTY, weights vs a plain Monday:

| Window | Mon | Tue | Wed | Thu | Fri | cond |
|---|---|---|---|---|---|---|
| Thu regime, to 2025-08-28 | 1.00 | 1.06 | 0.61 | **0.40** | 1.31 | 10.8 |
| Tue regime, from 2025-09-02 | 1.00 | **0.42** | 0.79 | 0.68 | 0.61 | 4.4 |

Returns channel, mean squared close-to-close Nifty return, no option data:

| Window | expiry | non-expiry | ratio |
|---|---|---|---|
| Pre 2025-09-01 | 0.583 (n=87) | 0.800 (n=326) | **0.73** |
| Post | 0.607 (n=56) | 0.708 (n=196) | **0.86** |

In each regime the lowest-weight weekday is the expiry weekday, and it moved Thursday → Tuesday when
the rule moved. The design worked; the sign is opposite to the hypothesis.

**Leading explanation, mechanical not behavioural.** An option's remaining variance runs to its
*settlement price*, and NSE settles on a time-average of the final window, not a point. The variance
of an average of a Brownian path is about one third of its endpoint variance — close to what the
Thursday fit reports. If that is the mechanism it is real, and belongs in the hedging model, because
a hedger faces it.

**Competing explanation daily data cannot exclude:** expiry-day variance may be mostly intraday and
invisible to close-to-close returns. Both channels share this blind spot, so their agreement is
weaker evidence than it looks.

**Not yet a result** — no bootstrap intervals, no randomization inference. Recorded so the direction
cannot later be quietly reversed to match the original hypothesis.

**Specification consequence.** Within one regime, expiry day is nearly a deterministic function of
weekday (83 of 87 pre-regime expiries are Thursdays), so `a[Thu]` and the expiry dummy are not
separately identified even where the rank check passes. Drop the expiry dummy within regime and let
the expiry weekday carry the effect; keep it only across regimes, where the rule change identifies
it. Reported `combined` cells with zero observations (Friday-plus-expiry never occurs pre-regime)
are extrapolation, not estimate.

## D-09 · Returns channel — SET

Independent second estimate from the underlying alone: realized variance by weekday and expiry
proximity from squared close-to-close log returns, with an EWMA level control so a drifting
volatility regime is not read as a weekday pattern. Touches no option data.

Agreement between an option-based and a returns-based channel is a finding. Agreement between two
option-based specifications restates the input. This is also the only usable channel for Bank Nifty
after Nov-2024, when monthly-only quoting leaves the options channel weakly identified — an
asymmetry that goes in the writeup, not under it.

**Weakness:** one observation per day makes squared returns a noisy variance proxy. Expect wide
intervals. Intraday data would make this ~an order of magnitude more precise; it is the highest-value
future upgrade.

## D-10 · Event windows — SET

Six months either side, minus a blackout. **Sep-2025 primary. Nov-2024 secondary and confounded.**

**Nov-2024** removed the weeklies *and* raised contract size, and the measured transition is not a
step: lots of 25 and 75 trade side by side from 2024-11-22 to 2025-01-31. Any weekday-variance change
here is jointly caused by both and cannot be separated with daily data. Blackout is that measured
overlap, not an arbitrary two weeks. A further change (50→25) sits at 2024-04-26, just outside a
six-month pre-window.

**Sep-2025 is cleaner, not clean.** An earlier version of this decision claimed it had no
simultaneous contract-size change. False — lot size goes 75→65 from 2025-10-29, eight weeks after
the event, inside the post-window. So the headline is reported **twice**: full post-window, and the
contamination-free sub-window 2025-09-02 → 2025-10-28. If they disagree, the lot change is doing
the work. Reporting only the better-looking one is what this clause blocks.

Windows: pre 2025-03-02 → 2025-08-28; post 2025-09-02 → 2026-02-28.

**The transitional September monthly is excluded by contract, not by date.** Expiry 2025-09-25 sits
two weekdays after the Tuesday regime and belongs to the old schedule. Blacking out all of September
would cost the most valuable month in the post-window; instead those contracts are dropped and the
sessions kept.

## D-11 · Randomization inference — SET

Replaces the single placebo. Re-estimate the effect at every candidate pseudo-event date at least
three months from a real change, build the empirical distribution, report where the true date falls.

A single placebo is a test with one observation. The permutation distribution answers "how unusual
is this among all dates?", and gives a p-value that does not lean on asymptotics the small
post-event sample cannot support.

**Kill condition:** if the true event date falls inside the middle 90%, the effect is not
distinguishable from calendar drift and the project reports a null. Written down before any result
exists, so it cannot be renegotiated after.

## D-12 · Simulation baseline — SET

Time-changed geometric Brownian motion, where the only free object is the estimated clock. Heston
with the Andersen QE scheme is a robustness layer over a swept `(rho, xi)` grid, not the headline
generator.

Heston has five parameters this project never estimates, so a Heston headline is a function of
guessed inputs and a reviewer can move the answer by moving numbers the author admitted inventing.
Under a time-changed GBM every input is either estimated (the clock) or observed (the entry price).
The Heston layer answers a different, legitimate question: does the conclusion survive stochastic
vol and the leverage effect?

QE, not Euler, regardless. Euler's bias on the variance process near zero is well known.

## D-13 · Clock applied to variance, not the grid — SET

Simulate on a uniform calendar-time grid and rescale the variance increment per step. Rescaling the
grid instead entangles the clock effect with discretisation error, which itself depends on step
size. Rescaling variance keeps discretisation error identical between the two traders, so their
difference is attributable to the clock alone.

## D-14 · Variance reduction, and where it is banned — SET

Terminal pricing: antithetic + Black-76 control variate + scrambled Sobol. Hedging simulation:
antithetic only. **No QMC on hedging paths** — the P&L is a path functional whose value depends on
increment ordering, and low-discrepancy sequences distort the joint dependence across time steps,
which is the structure being measured.

## D-15 · Hedging experiment — SET

Short one weekly ATM Nifty straddle, delta-hedged once per session at settlement to expiry. Trader A
hedges on the estimated clock, trader B on calendar time. Both forced to enter at the identical
price. Report the full P&L distribution — variance, skew, 1/5/95/99 quantiles — never the mean alone.

Forced-equal entry is the design, not a simplification: under a mean-one clock both agree on total
variance and therefore on the entry price, so the entire measured difference lands in the risk
outcome.

## D-16 · The simulation must beat EKJS, not Black-Scholes — SET

Continuous-time hedging at the wrong vol has a closed form (El Karoui–Jeanblanc-Picqué–Shreve;
Ahmad–Wilmott):

    P&L = integral_0^T  0.5 * S_t^2 * Gamma_t^(h) * ( dV_h(t) - dV_r(t) )

up to sign and discounting, pinned in `src/sim/analytic.py` and asserted by A6. A reviewer will
raise this in about thirty seconds.

**Why the simulation still earns its place.** Both traders enter at the same price, so
`V_h(0,T) = V_r(0,T)` and `(dV_h - dV_r)` is a signed measure integrating to **zero** over the
option's life. A naive reading says the effect vanishes. It does not, because gamma is not constant
— small far from expiry, large near it. The clock matters exactly to the extent it moves variance
into or out of the high-gamma window. That is the central claim, and it is about a weighting, not a
level. It is also why Sep-2025 is the right test: moving the expiry day moves the high-gamma window.

The closed form gives the mean. It gives nothing about the distribution once rebalancing is discrete
and costs are paid, and the distribution is what a desk sizes risk against. EKJS is the validation
target, not a competitor.

## D-17 · Costs — PROV

Half-spread on the hedging future plus exchange charges, on traded delta notional, proportional. No
market impact in v1 — impact needs a depth model daily data cannot support, and a proportional cost
is transparently wrong in a known direction (understates large rebalances), which beats an invented
impact model.

Out of v1: securities transaction tax on exercise. Large in Indian index options and interacting
with expiry-day pinning, so it is a project of its own.

## D-18 · Acceptance gates — SET

`uv run pytest -m acceptance`

| ID | Gate | Fails if | Status |
|---|---|---|---|
| A1 | `ChngInOpnIntrst` reconciles with the realised day-over-day `OpnIntrst` change, per contract | mismatch rate > 0.1% | **pass** (99.980% over 1,799,623 transitions) |
| A2a | Parity forward vs listed futures settlement | median bias > 5bp | **pass** (+1.51bp NIFTY, +0.40bp BANKNIFTY, 3670 expiry-days) |
| A2b | Per-strike forward dispersion within an expiry | median MAD > 8 pts weekly | **pass** (3.93) |
| A3 | Clock design condition number and VIFs | ill-conditioned | **pass** (10.8 / 4.4 per regime) |
| A4 | Randomization-inference p-value for Sep-2025 | true event inside middle 90% → report null | not run |
| A5 | Heston MC vs semi-analytic Fourier, 20 parameter sets | any outside 3 MC standard errors | not built |
| A6 | Costs=0, dt→0: correct-clock mean → 0, wrong-clock mean → the EKJS integral | either limit missed | not built |
| A7 | Headline is a surface over (rebalance frequency × cost) | a single figure is published | not built |
| A8 | Clean-environment run reproduces committed results from pinned seeds and config hash | any figure differs | not built |

**A1 was rewritten.** The original reconciled against exchange-published per-expiry OI totals. NSE
publishes no such total in the daily archive, so the gate was unrunnable. The replacement compares
two independently reported columns and is stricter: a parser misaligning contracts across days would
fail it on thousands of rows, not hundreds. All 355 mismatches fall on two dates (2026-01-12,
2026-08-03) and are not lot-size related — 2,136 rows spanning a lot change reconcile exactly.

**A6 corrects the original plan**, which required mean hedging error → 0. True for the correct-clock
trader, **wrong** for the wrong-clock trader, whose mean converges to the non-zero EKJS value.
Requiring zero would force the engine to be broken to pass.

## D-19 · Reproducibility — SET

Every run writes a JSON sidecar: git commit, config hash, RNG seed, library versions, input SHA-256s.
`numpy.random.Generator` with explicit `SeedSequence` spawning per path block — never the legacy
global `np.random`, never seeding inside a loop.

## D-20 · Out of scope for v1 — SET

BSE Sensex (v2, D-03). Intraday data. Single-stock options. Stochastic time change. STT on exercise.
Early exercise. Market impact.

Listed so that "we did not do it" is visibly a decision rather than an omission.

## D-21 · Deliverable — SET

The project folder. No paper. Results are parquet under `results/tables/` and figures under
`results/figures/`; the narrative lives here and in docstrings.

Since no prose document carries the caveats, code must. The D-03 labelling (interrupted time series,
not diff-in-diff) is emitted in result metadata and printed by `src.experiment.hedge`, so a result
cannot be read out of the folder without its limitation attached.

---

## Open

- **RBI MPC dates** (D-07). Not derivable from NSE archives, not assertable from memory. Needed
  before the macro dummy means anything.
- **Within-regime specification** (D-08b). Plan: drop the expiry dummy within regime, keep it across.

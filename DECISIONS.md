# Decisions

Every modelling choice and why. Written before the code that depends on it. If code and this file
disagree, this file is the bug report.

`SET` decided · `OPEN` blocking · `PROV` defaulted, revisit if evidence contradicts

---

## D-01 · Estimand - SET

Daily variance weights `w(d)`, normalised to mean one, indexed by (weekday, is-expiry-day,
has-macro-event). Business time is `V(t) = sigma_bar^2 * sum_{d<=t} w(d)`. Null is `w == 1`.

Mean-one normalisation makes the clock a pure reshaping and leaves the level to a separate scale
parameter. Without that split the headline number would confound "wrong shape" with "wrong level",
and level errors are a different problem.

**Falsifier:** bootstrap intervals that all straddle 1.0. That is a publishable null.

## D-02 · Data - SET

Public NSE archives only, no credentials. Files land immutable under `data/raw/nse/` with a
`MANIFEST.json` (url, timestamp, sha256). Probed live 2026-09-07; nothing below is from memory.

| Source | URL pattern | Coverage |
|---|---|---|
| F&O bhavcopy, UDiFF | `nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip` | 2024-01-02 on |
| F&O bhavcopy, legacy | `.../content/historical/DERIVATIVES/<YYYY>/<MON>/fo<DDMMMYYYY>bhav.csv.zip` | to 2024-07-03 |
| All-index close | `.../content/indices/ind_close_all_<DDMMYYYY>.csv` | both eras |
| BSE F&O bhavcopy, UDiFF | see D-22 | from 2024-01-01 |
| Sensex OHLC | see D-22 | from 2024-01-01 |

Needs a browser `User-Agent` **and** `Referer: https://www.nseindia.com/`. No cookie handshake.

**Sample: 2024-01-02 onward, UDiFF reader only.** Covering 2023 costs a second parser plus a
reconciliation gate to buy ~250 days. We already get 10 months before Nov-2024, 20 before Sep-2025,
and what was expected to be ~400 pseudo-event dates for D-11. Measured, it is 48 eligible dates
and about five independent windows. Build the legacy reader only if D-11 comes out thin: it did.

**Trading calendar** = the set of dates a bhavcopy exists. Self-consistent, no holiday file to drift.

**Retracted 2026-09-07:** an earlier note claimed `FininstrmActlXpryDt` vs `XpryDt` gives the
holiday roll. They are identical on every row of all 666 files - 0 mismatches. The exchange
publishes the actual expiry twice. Rolls are derived instead (D-02b). This was assumed from a
column name before checking, which is the failure this decision exists to prevent.

## D-02a · What the settlement column actually is - SET

Measured on `fo15JUN2023bhav.csv` (50,591 rows) and `BhavCopy_..._20250903_...csv` (28,551 rows).

1. **On expiry day the settlement column for the expiring series is the underlying's final
   settlement, not an option price.** All 146 expiring NIFTY rows on 15-Jun-2023 carry the same
   value, 18688.1, across every strike and both types. Inverting it yields garbage, once a week,
   on the day the effect under study is largest.
2. **For untraded strikes, settlement is an exchange model output.** 663 of 663 untraded rows have
   `close != settle`. Strike 8500 CE against an 18745.45 future settles at 10211.05 - discounted
   intrinsic.
3. **Traded rows are not automatically safe.** 80 of 559 traded rows also have `close != settle`,
   because settlement comes from the closing window, not the last trade.

Panel keeps `close`, `settle`, `volume`, `n_trades`, `open_int` as separate columns and never
collapses them.

## D-02b · Measured regime timeline - SET

From `src.ingest.regime_scan` over 666 trading days → `data/reference/regime_timeline.json`.

**Expiry weekday**, located by change-point search, not an assumed effective date:

| Regime | Span | Expiries |
|---|---|---|
| Thursday | 2024-01-04 → 2025-08-28 | 87 |
| Tuesday | 2025-09-02 → 2026-09-01 | 56 |

Sep-2025 pins to the gap between 2025-08-28 and 2025-09-02. **Corrected 2026-09-11:** an earlier version called "split residual equals the off-weekday
expiry count (11 = 11)" a self-check. It is a tautology: rolls are defined as expiries off their
regime's weekday, and the residual counts exactly those. The real check is binary segmentation
with a minimum gain: a further split is taken only if it fixes at least three expiries, which a
lone holiday roll never does and a real regime change always does. On Nifty it finds one change.

**Off-weekday expiries: 8 backward rolls, 3 not rolls.** A holiday roll only moves expiry *earlier*.
2025-09-25 (+2), 2025-12-24 (+1) and 2026-06-25 (+2) sit later, so they are non-weekly tenors, and
are flagged `off_regime_unclassified` rather than filed as holidays.

**Contract size is a staircase:** 50 → 25 (2024-04-26) → 25 *and* 75 quoted together
(2024-11-22 → 2025-01-31) → 75 → 65 and 75 (2025-10-29) → 65.

**Weekend sessions: 5.** 2024-01-20, 2024-03-02, 2024-05-18 (Nifty moved 0.16-0.23%);
2025-02-01 Budget Sat (−0.11%); **2026-02-01 Budget Sun (−1.98%, |z| = 2.3)**. Volumes are
comparable to weekdays, so these are real sessions. The three 2024 Saturdays stay `unclassified` - 
the data cannot tell a DR drill from any other special session.

**Caveat:** the scan pools weekly, monthly, quarterly and long-dated expiries, which share no
schedule. Splitting by tenor is a prerequisite for D-08 and is done in `build_panel`.

## D-02c · Weekend sessions - SET

Kept in the panel and the return chain. Excluded from the weekday vector, given their own
indicator.

They cannot carry a weekday weight: four Saturdays and one Sunday is not an estimate. They also
cannot be dropped - deleting 2026-02-01 does not delete its −1.98% move, it transfers it to the
next Monday's close-to-close return. Dropping a session is a reassignment, not a removal.

The Budget dummy attaches to the session the Budget was presented in. In 2026 that was a Sunday.

## D-03 · Scope - SET (revised 2026-09-11)

NSE and BSE. At Sep-2025 two units are treated in opposite directions on the same date: Nifty 50
(Thursday to Tuesday) and Sensex (D-22). Sensex was deferred to v2 until D-11 came back null on the
NSE-only design; it was pulled into v1 on 2026-09-11 because it is the only addition that can turn
the Sep-2025 interrupted time series into a difference in differences.

Until the Sensex estimates exist and pass their own gates, the Sep-2025 result stays labelled an
interrupted time series. It supports "the clock moved when the rule moved", not "the rule caused
it".

Identification was to rest on the Nifty-minus-Sensex difference (D-22), ranked by the D-11
permutation test. **It could not be estimated**: Sensex is too thin for the three-month windows the
test needs (D-22). The Sep-2025 result therefore stays an interrupted time series.

## D-04 · Bank Nifty is treated, not a control - SET

Sep-2025 moved the expiry day for all NSE index derivatives, monthlies included, so Bank Nifty
moved with everything else. A unit treated in the same direction at smaller magnitude is not a
control; using it as one biases toward zero and yields a defensible-looking wrong null. It enters
as a lower-dose treated unit with a dose-response prediction.

At Nov-2024 the roles swap: Bank Nifty is treated (weekly removed), Nifty is the control (weekly
survived). Treated/control is a property of the event, not the symbol - code must not hardcode it.

Confirmed in the panel: **zero BANKNIFTY weekly rows after 2024-11-13.**

## D-04a · Within-NSE control - SET

Returns-channel-only panel from NSE cash indices with no listed weekly options across the sample:
Nifty Midcap 150, Nifty Smallcap 250, Nifty 500. Never inverted for IV.

Answers "did Indian equity indices in general shift weekday variance around Sep-2025 for unrelated
reasons?" Costs one small parser. Imperfect - these are correlated with Nifty and touched by
index-level hedging flow - so they bound the confound rather than remove it, and do not substitute
for Sensex.

## D-05 · Forward from put-call parity - SET (revised 2026-09-07)

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

## D-06 · Liquidity filters - PROV

Keep a quote only if `volume > 0`, `n_trades >= 5`, `open_int >= 500`, `|log(K/F)| <= 0.15`, and at
least 2 trading sessions to expiry. The expiry session is excluded and held out.

This is correctness, not hygiene - see D-02a. `n_trades` rather than volume alone because one lot
traded at 10:15 does not make the 15:30 settlement a market price. Thresholds are chosen, not
derived, so every headline carries a sensitivity panel across them. If the sign of a result depends
on them, the result does not exist.

## D-07 · Clock parameterisation - SET

`log w(d) = a[weekday] + e*1[weekend session] + b*1[expiry day] + c*1[macro event]`, mean-one over
the window, positive by construction.

Log-linear gives positivity without a constrained solver and makes the expiry effect multiplicative,
which is how a desk quotes it. The macro dummy is not optional: RBI and Budget days carry large
scheduled variance, and omitting them dumps that variance onto whichever weekday they fall on.

Rejected: a free weight per calendar day. Fits everything, identifies nothing.

**Closed by D-07a and D-26:** RBI MPC dates are now sourced; the dummy is identified on Nifty (x2.2 / x2.0) and the expiry dip survives it. Original note: `data/reference/macro_events.json` had Union Budget days only. RBI MPC dates are not
derivable from NSE archives and D-02 forbids asserting them. Until supplied, RBI variance leaks
into weekday weights.

## D-08 · Options-channel identification - SET

Pooled panel with one nuisance scale per (date, symbol), profiled out by within-group demeaning
rather than carried as thousands of free parameters:

    log(IV^2 * tau) = log s_t + log( sum_{d in (t,T]} w(d) )

A single date supplies 3-6 maturities against 7 parameters - underdetermined. The original plan's
claim that one date *overdetermines* the system is wrong. Identification comes from variation
across dates in which weekdays remain: from a Monday, a Tuesday-expiry week has a different weekday
mix than from a Wednesday. Pooling is what makes it full rank.

Gate A3 reports the condition number and VIFs, because weights off an ill-conditioned design are
noise wearing a confidence interval.

## D-08a · IV noise inflates variance - SET

`E[IV^2] = E[IV]^2 + Var(IV)`, so measurement noise inflates total variance, and noise is largest at
the shortest maturities - exactly where the expiry day dominates the remaining-day set. Noise alone
would produce an apparent expiry-day weight above one.

**First estimate was wrong by three orders of magnitude.** Taking `Var/mean^2` from the per-quote
smile residual gave 5.4% at two sessions vs 0.5% at thirteen. That is the error on a *single quote*.
The clock consumes one fitted ATM value per expiry-day, across ~50 quotes.

Measured properly by fitting calls and puts separately - under parity they must agree, so their
difference is measurement error. Across 3,772 NIFTY expiry-days:

| dte | n | empirical SE(ATM) | iid formula | ratio | noise share of variance |
|---|---|---|---|---|---|
| 2-3 | 280 | 0.141% | 0.115% | 1.2x | **0.011%** |
| 4-6 | 417 | 0.091% | 0.069% | 1.3x | 0.006% |
| 7-12 | 830 | 0.085% | 0.062% | 1.4x | 0.005% |
| 13+ | 2245 | 0.130% | 0.091% | 1.4x | 0.010% |

Errors are close to independent across strikes. Differential bias ≈ 0.006 percentage points of
variance, against a plausible effect measured in tens of percent. Negligible.

The threat was real; the vega-weighted smile fit is what defeats it. Feed the clock fitted ATM
values, never single quotes. The closed-form correction stays in the code because it is free.

**Requirements:** vega weighting (error scales as price-error/vega, so weight by `vega^2`);
subtract `Var(atm_iv_hat)`; report the clock with and without the correction.

## D-08b · The expiry-day effect: real within each regime, not attributable to the rule

**Corrections, in order.** 2026-09-08: the returns channel does not confirm the options channel;
its bootstrap ratio spans [0.45, 1.14] under every estimator. 2026-09-11: the stock-future
intervals were computed by resampling stock-days as independent, but stocks on one session share
the market factor, so the session is the unit. Clustered by session the intervals widen about four
times and the post-regime effect loses significance. Same date: the pre-regime clock fit was using
quotes whose variance span crossed into the Tuesday regime (a quote on 2025-08-27 expiring
2025-09-02). Windows now require the expiry inside the window; 89 spans dropped, Thursday moved from
0.654 to 0.699.

**Options channel, within-regime specification, weights normalised to mean one, 300 bootstrap draws
resampling whole dates:**

| Window | Mon | Tue | Wed | Thu | Fri | cond |
|---|---|---|---|---|---|---|
| Thu regime, to 2025-08-28 | 1.019 | 1.082 | 0.926 | **0.699** [0.483, 0.889] | 1.299 [1.058, 1.477] | 9.4 |
| Tue regime, from 2025-09-02 | 1.459 [1.163, 1.780] | **0.607** [0.298, 0.844] | 1.093 | 0.928 | 0.880 | 4.0 |

In each regime the expiry weekday is significantly below an average session. That part holds.

**Stock-future cross-section, bootstrapped by session:** index-expiry sessions against others,
**0.808 [0.685, 0.949]** pre-regime, **0.904 [0.730, 1.121]** post. Pre-regime replicates; post
does not reach significance.

**Mechanism tests.** *Settlement averaging*: sessions where the stock future itself settles to a
VWAP, 0.820 [0.648, 0.995] (32 sessions), against sessions where only an index weekly expires,
0.852 [0.734, 0.993] (107 sessions). The intervals overlap, so there is no evidence for averaging;
with 32 sessions the test is also too weak to reject it. An earlier version said "rejected"; that
was an artefact of the stock-day bootstrap. *Max-pain pinning*: spot diverges from the max-pain
strike at roughly the random-walk rate (0.29% at 1-3 days to expiry grows to 0.83% by settlement).
A point comparison, but the direction is unambiguous.

**Stable across sub-samples (2026-09-12).** Splitting each Nifty regime in half: Thursday 0.675
[0.45, 0.87] and 0.484 [0.14, 0.91]; Tuesday 0.549 [0.12, 0.80] and 0.498 [0.06, 1.16]. Three of four
intervals exclude 1 and the direction never flips. The same check on Sensex does not reproduce the
pattern (D-22).

**Whether the rule moved the low-variance day is D-11, and D-11 is null.**

**Specification note.** Within one regime the expiry day is nearly a deterministic function of
weekday (83 of 87 pre-regime expiries are Thursdays), so `a[Thu]` and an expiry dummy are not
separately identified. `fit(..., dummies=("weekend",))` drops the dummy within regime.

**Normalisation bug, fixed.** Weights were once reported relative to the pinned Monday rather than
mean-one. Monday carries the weekend, so everything looked suppressed against it. `_normalise`
rescales to mean one over the window.

## D-08c · What the clock is actually worth

**Corrected 2026-11-10 (D-25).** The table that stood here (clock sd 497, share 2.3%, "discrete
rebalancing swamps the clock by a factor of forty") came from a hedge with the wrong sign. Corrected,
200,000 paths, truth following the fitted Tuesday-regime clock, one hedge per session:

| cost (bp) | clock sd | calendar sd | difference sd | share of hedging-error sd |
|---|---|---|---|---|
| 0.0 | 136.86 | 137.30 | 11.52 | 8.4% |
| 0.5 | 137.14 | 137.59 | 11.53 | 8.4% |
| 2.0 | 137.99 | 138.47 | 11.54 | 8.4% |

At daily hedging the clock term is nearly orthogonal to discretisation error, so the calendar
trader's total sd is only 0.3% higher. But the clock term does not shrink with rebalancing and the
discretisation term does: see D-25. The hedging result is a floor, not a null.

**The money is in the entry price, at one session to expiry.** A calendar-time trader misprices an
option by the ratio of the clock variance of the sessions it spans to the flat variance it assumes:

| opened | sessions to expiry | clock variance | flat | price error |
|---|---|---|---|---|
| Mon | 1 | 0.611 | 1.0 | **-21.8%** |
| Fri | 2 | 2.080 | 2.0 | +2.0% |
| Thu | 3 | 2.966 | 3.0 | -0.6% |
| Wed | 4 | 3.900 | 4.0 | -1.3% |
| Tue | 5 | 5.000 | 5.0 | 0.0% |

Over a full week the weekday mix averages to one by construction and the error vanishes. Over a
single session it does not. **In the price, the clock is a one-day-to-expiry phenomenon.** (The
earlier "price, not the hedge" was wrong; D-25.) The corresponding near/far calendar spread opened
on a Monday is mispriced +9.5%.

Caveat: -21.8% is a point estimate on a weight whose interval is [0.298, 0.844], which maps to a
price-error range of roughly -45% to -8%. Wide, but negative throughout.

---

## D-08d · Variance risk premium and skew - supporting measurements

**VRP.** Implied variance exceeds subsequent realized variance on 70% of weekly windows, median
ratio 1.46 pre-regime and 1.51 post (1,977 observations). That is where the global literature puts
it, and it is a useful end-to-end sanity check: a pipeline error in the forward, the inversion or
the smile would not land on a plausible number by accident. Weekday variation in the premium is
mild (1.31 to 1.57).

**Skew.** The vega-weighted smile slope is stable across weekdays (-0.30 to -0.33 pre, -0.24 to
-0.29 post) and shows no expiry-day pattern, so there is no "skew clock" to speak of. Curvature is
a different matter: it rises sharply into expiry, from +2.8 at thirteen or more sessions to **+44.7
at two to three**. Short-dated smiles are far more convex, which is consistent with the wings
pricing jump risk that the at-the-money level does not see.


## D-09 · Returns channel - SET

Independent second estimate from the underlying alone: realized variance by weekday and expiry
proximity from squared close-to-close log returns, with an EWMA level control so a drifting
volatility regime is not read as a weekday pattern. Touches no option data.

Agreement between an option-based and a returns-based channel is a finding. Agreement between two
option-based specifications restates the input. This is also the only usable channel for Bank Nifty
after Nov-2024, when monthly-only quoting leaves the options channel weakly identified - an
asymmetry that goes in the writeup, not under it.

**Weakness:** one observation per day makes squared returns a noisy variance proxy. Expect wide
intervals. Intraday data would make this ~an order of magnitude more precise; it is the highest-value
future upgrade.

## D-10 · Event windows - SET

Six months either side, minus a blackout. **Sep-2025 primary. Nov-2024 secondary and confounded.**

**Nov-2024** removed the weeklies *and* raised contract size, and the measured transition is not a
step: lots of 25 and 75 trade side by side from 2024-11-22 to 2025-01-31. Any weekday-variance change
here is jointly caused by both and cannot be separated with daily data. Blackout is that measured
overlap, not an arbitrary two weeks. A further change (50→25) sits at 2024-04-26, just outside a
six-month pre-window.

**Sep-2025 is cleaner, not clean.** An earlier version of this decision claimed it had no
simultaneous contract-size change. False - lot size goes 75→65 from 2025-10-29, eight weeks after
the event, inside the post-window. So the headline is reported **twice**: full post-window, and the
contamination-free sub-window 2025-09-02 → 2025-10-28. If they disagree, the lot change is doing
the work. Reporting only the better-looking one is what this clause blocks.

Windows: pre 2025-03-02 → 2025-08-28; post 2025-09-02 → 2026-02-28.

**The transitional September monthly is excluded by contract, not by date.** Expiry 2025-09-25 sits
two weekdays after the Tuesday regime and belongs to the old schedule. Blacking out all of September
would cost the most valuable month in the post-window; instead those contracts are dropped and the
sessions kept.

## D-11 · Randomization inference - SET, RUN: kill condition fires

Re-estimate the regime-change statistic at every eligible pseudo-event date and rank the true date
against that distribution. Pre-registered statistic:

    S = [ln w_Tue(post) - ln w_Tue(pre)] - [ln w_Thu(post) - ln w_Thu(pre)]

predicted negative at the true date. Windows of h sessions each side; a pseudo window may not
contain the real Sep-2025 boundary or overlap the Nov-2024 blackout (2024-11-13 to 2025-01-31).

**Kill condition, written before any result:** if the true date falls inside the middle 90% of the
permutation distribution, the effect is not distinguishable from calendar drift and the project
reports a null.

**Result, 2026-09-11 (`src.analysis.permutation`):**

| h | channel | pseudo dates | S* | percentile | one-sided p |
|---|---|---|---|---|---|
| 63 | options, all fits | 48 | -2.251 | 10.4 | 0.122 |
| 63 | options, degenerate fits excluded | 35 | -2.251 | 2.9 | 0.056 |
| 63 | stocks | 48 | -0.059 | 35.4 | 0.367 |
| 42 | options, all fits | 73 | -2.004 | 11.0 | 0.122 |
| 42 | options, degenerate fits excluded | - | true event fails the rule (cond 31) | - | - |
| 42 | stocks | 73 | -0.326 | 30.1 | 0.311 |

**The pre-registered test is null on both channels at both window lengths. The project reports a
null for the claim that the rule moved the low-variance day.**

The "degenerate excluded" row needs saying clearly. On three-month windows a quarter of the
options fits are numerically broken (weights outside [0.1, 10], condition numbers to 1e9), so
excluding them is defensible on its face. But the rule was written after the raw test came back
null, it moves the result from null to p = 0.056 (still above 0.05), and at h = 42 the same rule
excludes the true event itself. That is the forking-path pattern this decision exists to block, so
it is recorded and not claimed.

**The design is thin, not just this sample.** Adjacent pseudo dates share almost all their data;
there are about five independent pseudo windows. No p-value from this design can be much smaller
than one in six. The stock channel involves no model fit and is plainly null (percentile 30-35).

**Rerun 2026-11-11 after D-26's estimator changes.** Nifty rows unchanged (h = 63: S* -2.251, p 0.122; h = 42: p 0.122). The rank guard now refuses 19 Sensex h = 63 pseudo-windows that had condition numbers of 1e16 or worse: Sensex n 48 -> 29, DiD p 0.102 -> 0.100. The h = 42 DiD p = 0.021 is still the 1e-60 Sensex weight artefact (D-22) and is not a result.

**What could change the answer:** BSE Sensex (tried 2026-09-12: 39 of 48 Sensex window fits are
degenerate, so the DiD is not evaluable, D-22); the legacy 2023 reader (more pseudo windows; D-02 made
it conditional on D-11 coming out thin, which it did); intraday data.

## D-12 · Simulation baseline - SET

Time-changed geometric Brownian motion, where the only free object is the estimated clock. Heston
with the Andersen QE scheme is a robustness layer over a swept `(rho, xi)` grid, not the headline
generator.

Heston has five parameters this project never estimates, so a Heston headline is a function of
guessed inputs and a reviewer can move the answer by moving numbers the author admitted inventing.
Under a time-changed GBM every input is either estimated (the clock) or observed (the entry price).
The Heston layer answers a different, legitimate question: does the conclusion survive stochastic
vol and the leverage effect?

QE, not Euler, regardless. Euler's bias on the variance process near zero is well known.

## D-13 · Clock applied to variance, not the grid - SET

Simulate on a uniform calendar-time grid and rescale the variance increment per step. Rescaling the
grid instead entangles the clock effect with discretisation error, which itself depends on step
size. Rescaling variance keeps discretisation error identical between the two traders, so their
difference is attributable to the clock alone.

## D-14 · Variance reduction, and where it is banned - SET

Terminal pricing: antithetic + Black-76 control variate + scrambled Sobol. Hedging simulation:
antithetic only. **No QMC on hedging paths** - the P&L is a path functional whose value depends on
increment ordering, and low-discrepancy sequences distort the joint dependence across time steps,
which is the structure being measured.

## D-15 · Hedging experiment - SET

Short one weekly ATM Nifty straddle, delta-hedged once per session at settlement to expiry. Trader A
hedges on the estimated clock, trader B on calendar time. Both forced to enter at the identical
price. Report the full P&L distribution - variance, skew, 1/5/95/99 quantiles - never the mean alone.

Forced-equal entry is the design, not a simplification: under a mean-one clock both agree on total
variance and therefore on the entry price, so the entire measured difference lands in the risk
outcome.

## D-16 · The simulation must beat EKJS, not Black-Scholes - SET

Continuous-time hedging at the wrong vol has a closed form (El Karoui-Jeanblanc-Picqué-Shreve;
Ahmad-Wilmott):

    P&L = integral_0^T  0.5 * S_t^2 * Gamma_t^(h) * ( dV_h(t) - dV_r(t) )

up to sign and discounting, pinned in `src/sim/analytic.py` and asserted by A6. A reviewer will
raise this in about thirty seconds.

**Why the simulation still earns its place.** Both traders enter at the same price, so
`V_h(0,T) = V_r(0,T)` and `(dV_h - dV_r)` is a signed measure integrating to **zero** over the
option's life. A naive reading says the effect vanishes. It does not, because gamma is not constant
 -  small far from expiry, large near it. The clock matters exactly to the extent it moves variance
into or out of the high-gamma window. That is the central claim, and it is about a weighting, not a
level. It is also why Sep-2025 is the right test: moving the expiry day moves the high-gamma window.

The closed form gives the mean. It gives nothing about the distribution once rebalancing is discrete
and costs are paid, and the distribution is what a desk sizes risk against. EKJS is the validation
target, not a competitor.

## D-17 · Costs - PROV

Half-spread on the hedging future plus exchange charges, on traded delta notional, proportional. No
market impact in v1 - impact needs a depth model daily data cannot support, and a proportional cost
is transparently wrong in a known direction (understates large rebalances), which beats an invented
impact model.

Out of v1: securities transaction tax on exercise. Large in Indian index options and interacting
with expiry-day pinning, so it is a project of its own.

## D-18 · Acceptance gates - SET

`uv run pytest -m acceptance`

| ID | Gate | Fails if | Status |
|---|---|---|---|
| A1 | `ChngInOpnIntrst` reconciles with the realised day-over-day `OpnIntrst` change, per contract | mismatch rate > 0.1% | **pass** (99.980% over 1,799,623 transitions) |
| A2a | Parity forward vs listed futures settlement | median bias > 5bp | **pass** (+1.51bp NIFTY, +0.40bp BANKNIFTY, 3670 expiry-days) |
| A2b | Per-strike forward dispersion within an expiry | median MAD > 8 pts weekly | **pass** (3.93) |
| A3 | Clock design condition number and VIFs | ill-conditioned | **pass** (10.8 / 4.4 per regime) |
| A4 | Randomization-inference p-value for Sep-2025 | true event inside middle 90% → report null | **fails**: kill condition fires, D-11 |
| A5 | Heston MC vs the semi-analytic Fourier price, 20 random parameter sets, fixed seeds | any set outside 3 standard errors plus the D-23 allowance | **pass** |
| A6 | Exact mean zero on every grid; simulated P&L difference converges pathwise (L2) to the EKJS integral at rate m^-1/2 | either check missed | **pass**; wording corrected in D-23, again in D-25 |
| A7 | Headline published as a surface over rebalance frequency and cost, with provenance | a single figure is published instead | **pass** (16 cells, clock share 8.4% at 1 hedge/session to 23.8% at 8) |
| A8 | Provenance complete, config hash stable, seeded runs bit-for-bit reproducible | a manifest is missing or a seeded run differs | **partial**: automated part passes; a clean-clone run on 2026-11-11 verified all 2,000 raw sha256s and reran `regime_scan`, then was killed for low memory in `build_panel` (TO_DO.md) |

**A1 was rewritten.** The original reconciled against exchange-published per-expiry OI totals. NSE
publishes no such total in the daily archive, so the gate was unrunnable. The replacement compares
two independently reported columns and is stricter: a parser misaligning contracts across days would
fail it on thousands of rows, not hundreds. All 355 mismatches fall on two dates (2026-01-12,
2026-08-03) and are not lot-size related - 2,136 rows spanning a lot change reconcile exactly.

**A6 corrects the original plan**, which required mean hedging error → 0. True for the correct-clock
trader, **wrong** for the wrong-clock trader, whose mean converges to the non-zero EKJS value.
Requiring zero would force the engine to be broken to pass. **Corrected again in D-23:** measured, the wrong-clock mean also goes to zero as rebalancing refines, because equal entry prices make the difference a martingale. The gate now compares the simulator with the closed form on each grid.

## D-19 · Reproducibility - SET

Every run writes a JSON sidecar: git commit, config hash, RNG seed, library versions, input SHA-256s.
`numpy.random.Generator` with explicit `SeedSequence` spawning per path block - never the legacy
global `np.random`, never seeding inside a loop.

## D-20 · Out of scope for v1 - SET

BSE Sensex (v2, D-03). Intraday data. Single-stock options. Stochastic time change. STT on exercise.
Early exercise. Market impact.

Listed so that "we did not do it" is visibly a decision rather than an omission.

## D-21 · Deliverable - SET

The project folder. No paper. Results are parquet under `results/tables/` and figures under
`results/figures/`; the narrative lives here and in docstrings.

Since no prose document carries the caveats, code must. The D-03 labelling (interrupted time series,
not diff-in-diff) is emitted in result metadata and printed by `src.experiment.hedge`, so a result
cannot be read out of the folder without its limitation attached.

---

## D-22 · BSE Sensex, the opposite-signed unit - SET

Added 2026-09-11. D-11 came back null on NSE data alone, and a single-series design has about five
independent pseudo windows, which cannot produce a small p-value. A second series treated in the
opposite direction on the same date differences out every common calendar shock.

**Sources, probed live 2026-09-11.**

| Source | URL pattern | Notes |
|---|---|---|
| F&O bhavcopy, UDiFF | `www.bseindia.com/download/Bhavcopy/Derivative/BhavCopy_BSE_FO_0_0_0_<YYYYMMDD>_F_0000.CSV` | from 2024-01-01; same 34-column schema as NSE; plain CSV, not zip |
| Sensex OHLC | `api.bseindia.com/BseIndiaAPI/api/ProduceCSVForDate/w?strIndex=SENSEX&dtFromDate=DD/MM/YYYY&dtToDate=DD/MM/YYYY` | one request covers the sample: 671 rows, 2024-01-01 to 2026-09-10, all five weekend sessions present |

Needs `Referer: https://www.bseindia.com/`.

**BSE never 404s a non-session.** A holiday (2025-08-15) and a Saturday (2025-08-30) both return
HTTP 200 with the same 14,287-byte HTML page. The downloader reads that page as "no session", which
means a block page would be read the same way and would silently delete real sessions. Guard: the
BSE calendar is derived independently and cross-checked against the NSE calendar. The exchanges
share one holiday list, so any disagreement is a download fault until shown otherwise.

**Measured timeline** (`regime_scan`, 2026-09-11). Sensex weekly expiry: Friday 2024-01-05 to
2025-01-03 (54 expiries), Tuesday 2025-01-07 to 2025-08-26 (39), Thursday 2025-09-04 to 2026-09-03
(53). At Sep-2025 Sensex moved Tuesday to Thursday on the same date Nifty moved Thursday to Tuesday:
opposite-signed treatment, measured rather than assumed. Five Friday expiries sit inside the Tuesday
regime (2025-01-10 to 2025-03-28), contracts listed under the old schedule running off. Lot size 10,
then 10 and 20 together from 2024-12-02, then 20 from 2025-01-29 and unchanged after, so the Sensex
post-window has no counterpart to Nifty's 2025-10-29 lot change. The Jan-2025 Friday-to-Tuesday
change is a Sensex-only event, but it sits inside the Nov-2024 blackout and shares its confound.

**The calendar cross-check found a real fault on its first run.** All 666 NSE sessions are present
on BSE. BSE has one more, 2026-09-07: the NSE download ran that day before the file was published,
cached "no session", and the cache never expires. Absences within three days of a run are no longer
cached. The 666-session sample is unchanged.

**Thinner market.** On 2025-09-03: 614 Sensex option rows against ~1,500 for NIFTY, futures only for
the two monthlies, lot size 20. Same parity-forward method (D-05) and unchanged liquidity filters
(D-06), so fewer quotes survive. If the Sensex clock fails A3 conditioning, the DiD does not run.

**Pre-registered predictions, written before any Sensex estimate exists.** The Sensex expiry-weekday
history is to be measured by `regime_scan`, not assumed. Working expectation: Tuesday before
Sep-2025, Thursday after (the 2025-09-03 file already shows Thursday weeklies). With
`S = [ln w_Tue(post) - ln w_Tue(pre)] - [ln w_Thu(post) - ln w_Thu(pre)]`:

- **H_own**: each index's clock tracks its own exchange's expiry. `S_NIFTY < 0`, `S_SENSEX > 0`, and
  `DiD = S_NIFTY - S_SENSEX` is strongly negative.
- **H_market**: the suppression is market-wide and follows the dominant NSE expiry, which is what
  the stock-futures result suggests (stocks are quieter on NSE index-expiry sessions, D-08b). Then
  the Sensex clock is also low on NSE's expiry weekday: `S_SENSEX` close to `S_NIFTY`, DiD near 0.

Both outcomes are informative and they are distinguishable: under H_market the Sensex weight on its
*own* expiry weekday is not low. DiD inference reuses D-11 (true-date difference ranked against
pseudo dates) with the kill condition unchanged.

**Realized variance cannot do this job.** Nifty and Sensex are near-duplicate baskets (correlation
not yet measured here, expected above 0.95), so their realized weekday variances are close to
identical by construction. The DiD is an options-channel test only.

**Result, 2026-09-12. Sensex does not replicate the Nifty pattern, and the DiD cannot be run.**

Sensex forwards pass A2a on their own (median -1.22bp against listed Sensex futures, 1,108
expiry-days), so the inputs are sound. The clock is where it fails.

Sensex clock per regime, weekday-only specification, 300 bootstrap draws:

| Sensex regime | own expiry | NSE expiry | own-expiry weight | NSE-expiry weight | cond |
|---|---|---|---|---|---|
| 2024-01-02..2024-11-12 | Fri | Thu | **1.409** [1.077, 1.693] | 0.872 [0.560, 1.235] | 9.6 |
| 2025-02-03..2025-08-29 | Tue | Thu | 1.692 [1.170, 2.071] | 0.932 [0.595, 1.205] | 26.8 |
| 2025-09-02..2026-09-04 | Thu | Tue | 0.946 [0.758, 1.171] | 0.926 [0.707, 1.094] | 5.3 |

Half-split stability, own-expiry weight in each half of each regime:

| | first half | second half |
|---|---|---|
| NIFTY, Thu regime | 0.675 [0.45, 0.87] | 0.484 [0.14, 0.91] |
| NIFTY, Tue regime | 0.549 [0.12, 0.80] | 0.498 [0.06, 1.16] |
| SENSEX, Fri regime | 1.137 [0.75, 2.27] | **1.413** [1.05, 1.77] |
| SENSEX, Tue regime | 1.897 (cond 42.7) | 0.157 (cond 31.7) |
| SENSEX, Thu regime | 0.842 [0.58, 1.04] | 1.001 [0.80, 1.33] |

Reading, in order of confidence:

1. **Nifty's low expiry weight is stable.** Point estimates 0.48 to 0.68 in all four halves, three of
   four intervals excluding 1, direction never flips. This strengthens D-08b.
2. **Sensex's Tuesday regime is not identified.** Both halves fail the conditioning bar and the two
   estimates differ twelvefold. The full-window 1.692 is not a finding.
3. **Where Sensex is identified, its expiry session is not low.** Friday 2024 is high (1.409, and
   1.413 in the cleaner half); Thursday after Sep-2025 is flat. The NSE expiry weekday is not
   significantly low on Sensex either. **Neither pre-registered hypothesis is supported:** H_own
   predicted a low own-expiry weight, H_market a low NSE-expiry weight.
4. **The DiD does not run, as pre-committed.** On three-month windows 39 of 48 Sensex fits at h = 63
   and 57 of 73 at h = 42 are degenerate or unusable, against 13 and 27 for Nifty. At the true event
   the Sensex fit has condition number 32.4 at h = 63, and at h = 42 its Tuesday weight collapses to
   1e-60, which is what produced an apparent DiD p = 0.021. That number is an artefact and is not
   reported as a result.

**What this leaves.** The Nifty within-regime effect is real and stable. It does not generalise to
the Sensex option market, and whether the Sep-2025 rule caused Nifty's shift remains untested:
Sensex, the one control that could have tested it, is too thin to estimate on the windows the test
needs. Candidate reasons Sensex differs (a smaller, more speculative expiry-day market; fewer
simultaneous maturities for the D-08 identification) are hypotheses, not findings.

**Note on condition numbers in the 2024 halves** (Nifty 3e10, Sensex 9e8). Three near-motionless
Saturdays let the weekend weight run to zero, a boundary solution with a vanishing gradient. The
weekday weights and their intervals are unaffected. The D-11 windows never carry a weekend
parameter (too few weekend sessions), so this does not touch the permutation results.

---

## D-23 · Simulation layer, and two gate corrections - SET

Built 2026-10-06 to close A5 to A8.

**Heston pricing.** Lewis (2001) representation, Gauss-Legendre on [0, 400] with 512 nodes, and the
Albrecher "little trap" branch for `g` so the complex logarithm stays on the principal branch at long
maturities. A characteristic function is easy to get wrong in a way that still returns a plausible
number, so two independent checks guard it: the price must collapse to Black-76 as vol-of-vol goes to
zero with `v0 == theta`, and the QE simulator must match it at finite vol-of-vol. The first is
deliberately loose (relative 2e-3): the `kappa*theta/xi^2` prefactor makes that limit
ill-conditioned, which is the price of having a limit with a known answer at all.

**Simulation.** Andersen QE for the variance (Euler is biased near zero, D-12), central
discretisation for log-spot with the drift written as
`(rho/xi) dv - (rho kappa theta/xi) dt + (rho kappa/xi - 1/2) v dt + sqrt(1-rho^2) sqrt(v) dW`.
Antithetic pairs are built from mirrored seeds rather than negated normals, because the QE variance
step consumes a uniform as well as a normal and negating only the normals is not a valid pair.

**A5 tolerance.** `3 standard errors + 0.3% of price`. Measured over three parameter sets at 64, 256
and 1024 steps, the deviation oscillates inside +/-0.4% and +/-1.75 standard errors with no drift
with step count, so it is sampling noise rather than discretisation bias. The allowance absorbs that
without hiding a wrong characteristic function, which would be wrong by percent. Seeds are fixed, so
a failure is a regression and not an unlucky draw.

**A6 is corrected.** D-18 said the wrong-clock mean converges to the non-zero EKJS integral. It does
not. Both traders are forced to the same entry price, so their P&L difference is
`integral (Delta_realised - Delta_believed) dS`, a martingale, and **its mean goes to zero as
rebalancing refines**. The benchmark shrinks with the grid: -0.699, -0.174, -0.043, -0.011 at 1, 4,
16 and 64 hedges per session, and the simulator tracks it at every grid (z = +1.75, -1.11, -0.12,
-0.30). This is the same category of error as the original "mean converges to zero", which D-18
already corrected once in the other direction.

The gate is therefore: the simulator equals the closed form on whatever grid it runs, and a
correct-clock hedger has mean zero. **This strengthens D-16 rather than weakening it.** If the mean
effect vanishes in the limit, the mean was never the interesting quantity, and the distribution,
which has no closed form once rebalancing is discrete and costs are paid, is the only place the clock
can show up. ~~Measured dispersion stays near 2%~~ (wrong-sign hedge; D-25).

**Superseded in part by D-25.** The simulator did not "track" the closed form: its standard error
was inflated by the sign error, so -0.699 and 0 were indistinguishable. The exact mean is zero on
every grid; -0.699 is the bias of the gamma proxy.

**A8 scope.** A clean-machine end-to-end reproduction cannot be checked from inside the process. The
automated gate covers what can be: every published table carries a `.manifest.json` with git commit,
config hash, seed, interpreter and library versions and input digests; the config hash is stable; a
stored manifest must match the current config; and a seeded simulation is bit-for-bit reproducible.
The clean-clone run remains a manual step and is listed in TO_DO.md. Calling that gate "passed"
without the manual step would be a false claim, so the status reads partial.

---

## D-24 · Heston robustness sweep - SET

D-12 planned Heston as a robustness layer over a swept `(rho, xi)` grid rather than the headline
generator, because neither parameter is estimated anywhere in this project. The honest form of the
question is therefore not "what is the number under Heston" but "does the number move when the two
unestimated parameters do".

**Construction.** Time-changed Heston: the clock scales the variance the spot sees, never the grid
spacing (D-13).

    dX = -0.5 w(t) v dt + sqrt(w(t) v) dW1,  dv = kappa (theta - v) dt + xi sqrt(v) dW2,  corr = rho

Splitting `dW1` on `dW2` and substituting `sqrt(v) dW2 = (dv - kappa(theta - v) dt)/xi` gives the
discretisation actually used, with the variance stepped by QE:

    dx = -0.5 w vbar dt + (rho sqrt(w)/xi)(dv - kappa(theta - vbar) dt) + sqrt((1-rho^2) w vbar dt) Zperp

As `xi` goes to zero the variance becomes deterministic at `theta`, the middle term vanishes, and
this collapses exactly to the time-changed GBM of D-12. That is a hard cross-check rather than an
approximate one, and it is a unit test.

**`v0 = theta`, deliberately.** Then every session has expected variance `theta`, so a mean-one clock
and a flat clock give identical total expected variance and both traders enter at the same price by
construction (D-15). Any other `v0` would smuggle a level difference into a shape experiment.

**Beliefs.** Both traders observe `v` and know the parameters; they differ only in the clock. Each
hedges on its own expected remaining variance, which Heston gives in closed form:

    E[ integral w v ds | v_t ] = sum_{k>i} w_k [ theta dt + (v_t - theta)(e^{-kappa(t_k - t_i)} - e^{-kappa(t_{k+1} - t_i)})/kappa ]

so the hedge ratio is path-dependent through `v_t`, which is the point of the exercise.

**Grid.** `rho` in {0, -0.3, -0.6, -0.9}, `xi` in {0.2, 0.5, 0.8}, `kappa` fixed at 2.0, `theta` set
to a 13% annual volatility to match the A7 baseline, at 1 and 4 hedges per session. Feller
(`2 kappa theta >= xi^2`) fails for the upper two `xi` values; QE is built for exactly that case and
the variance is reported, so the violation is visible rather than hidden.

**What is reported.** Dispersion, not means. The entry price is taken from the Fourier Heston price
ignoring the time change, which is an approximation; it shifts both traders by the same constant and
so cannot affect any standard deviation or the share. Means are therefore not interpretable here and
are labelled as such. Under D-23 the mean was already shown to vanish in the continuous limit.

**Result, corrected 2026-11-10 (D-25; the published 2.13-2.37% was the wrong-sign hedge).** The
clock term is what is robust: difference sd 11.42 to 11.83 across all 24 stochastic-vol cells,
against 11.56 / 11.45 in the GBM-limit rows. The share moves (7.1% to 15.8%) only because vol-of-vol
adds unhedged vega risk to the denominator: clock-trader sd rises from 137 to 168 at one hedge per
session and from 70 to 117 at four as `xi` goes to 0.8. So the clock's absolute cost does not depend
on the two parameters this project never estimates.

---

## D-25 · The hedge had the wrong sign; the hedging result is a floor, not a null - SET

Found 2026-11-10 by reading the A7 table rather than the tests: the "hedged" clock trader's P&L sd
was 497 against 277 for the naked straddle payoff, and it *rose* with rebalancing (497, 524, 539,
545 at 1, 2, 4, 8 hedges per session). A delta hedge must lower risk, and discrete-hedging error must
fall like `m^-1/2` (Boyle-Emanuel 1980; Bertsimas-Kogan-Lo 2000). Cause: the short straddle has
delta `-Delta`, so the hedge holds `+Delta`; `hedge_short_straddle` and the Heston `hedge` held
`-Delta`, doubling the exposure. 23 tests passed because every check that touched it compared means
(sign-blind: `E[sum delta dS] = 0` for either sign) or compared two implementations that shared it.

**Corrected numbers** (Tue-regime clock, opened Wednesday, 13% vol, entry 365.26, zero cost):

| hedges/session | clock sd | calendar - clock sd | share |
|---|---|---|---|
| 1 | 136.9 | 11.52 | 8.4% |
| 2 | 99.0 | 11.13 | 11.2% |
| 4 | 70.8 | 11.39 | 16.1% |
| 8 | 50.4 | 11.72 | 23.3% |
| 32 | 25.4 | 12.18 | 47.9% (100k paths) |

`136.9 / sqrt(m)` predicts 96.8, 68.4, 48.4, 24.2: the discretisation term is textbook. The clock
term is flat. In the continuous limit the correct-clock trader replicates exactly and the calendar
trader is left with the pathwise EKJS integral `int 0.5 S^2 Gamma_flat (dV_flat - dV_true)`, whose
sd is a floor near 12 index points (about 3.3% of the premium) that no rebalancing frequency removes.
**The clock does show up in the hedge: as the one risk a faster hedger cannot diversify away.** The
precise floor and the crossover frequency are estimated by multilevel Monte Carlo (D-27).

**Under Heston the floor is invariant.** Difference sd 11.42 to 11.83 across all 24 `(rho, xi)`
cells (D-24, corrected).

**The mean, exactly.** Under zero rates the spot is a martingale, so `E[sum delta_i dS_i] = 0` for
any adapted hedge on any grid, and both traders' entry price equals `E[payoff]` because they agree on
total variance. The mean P&L of both traders, and of their difference, is therefore *exactly zero on
every grid*. D-23's "the mean goes to zero as rebalancing refines" was half right: the discrete EKJS
sum it used as a benchmark (-0.699, -0.174, -0.043, -0.011) is the Riemann-sum bias of the gamma
proxy, not the P&L mean. Measured: difference mean +0.012 +/- 0.036 at one hedge per session, 19
standard errors from -0.699. In continuous time the EKJS mean is exactly zero for a reason worth
stating: expected dollar gamma under a lognormal spot is a Gaussian convolution, so it depends only
on realised-elapsed plus believed-remaining variance, `g(V - D(t))` with `D = V_believed - V_realised`.
The EKJS mean is `int g(V - D) dD`, an exact differential, and `D(0) = D(T) = 0`.

**A6 rewritten.** (1) The difference mean is within 3 standard errors of zero on every grid, and the
gamma-proxy bias at one hedge per session is resolved from zero by more than 10 standard errors. (2)
Pathwise, the simulated difference converges in L2 to `ekjs_pathwise`: relative RMS error 1.41, 0.68,
0.32, 0.16 at 1, 4, 16, 64 hedges per session (correlation 0.988 at 64), the `m^-1/2` rate of the
hedge. A regression test now asserts a hedge lowers risk at that rate.

**What this changes.** D-08c's hedging paragraph and "price, not the hedge"; the A7 numbers; D-24's
2.13-2.37%. **What it does not change.** Every options-channel and returns-channel estimate, D-11,
D-22, and the entry-price table: none of them touch the hedge.

---

## D-07a · RBI MPC dates - SET (amends D-07 OPEN)

`data/reference/macro_events.json` `rbi_mpc` holds 17 policy announcements, 2024-02-08..2026-10-07, all from rbi.org.in only.
- Each date is the 'Date:' field of the 'Resolution of the Monetary Policy Committee' release.
- Each date maps to its prid URL in `rbi_mpc_sources`.
- Index: https://www.rbi.org.in/Scripts/Annualpolicy.aspx. All 17 re-fetched 2026-11-10.

Completeness (no off-cycle meeting in the window):
- **Meeting numbers 47..63, every one read, no gap.** The minutes give 46th (Dec-2023) through 59th (Feb-2026): prid 56982, 57366, 57735, 58136, 58545, 58958, 59347, 59818, 60279, 60686, 61056, 61433, 61856, 62261. The resolutions give 53rd through 63rd. That is 17 numbers for 17 dates.
- **Year views of the index page.** FY 2023-24, 2024-25, 2025-26 and 2026-27 list 1, 6, 6 and 4 resolutions in the window, and no other MPC decision.
- **Consistency only.** Each resolution's 'next meeting' line names the next date in the list, starting from Dec-2023 (prid=56888). This check cannot reveal an unscheduled meeting.

Count per FY: 6 / 6 for FY24-25 / FY25-26. FY26-27 has 4 to date; Dec 2-4 2026 and Feb 3-5 2027 are scheduled (PublicationsView id=23800). Six is practice; RBI Act s.45ZI(1) requires 'at least four' (prid=46626). One reschedule: Aug-2025 moved Aug 5-7 -> Aug 4-6 (prid=60612, s.45ZI(4)); announced 2025-08-06.

Where the dates fall:
- All weekdays: 8 Fri, 7 Wed, 2 Thu, 0 Mon/Tue.
- 16/17 are NSE sessions. 2026-10-07 is past the calendar window (ends 2026-09-07, last session 2026-09-04) and stays unmarked until the calendar is extended.

Overlap with actual expiry days. This is measured against the panel IDO expiry set, the `estimate._expiry_days` rule, not inferred from weekdays:

| Symbol | Overlap | Dates |
|---|---|---|
| NIFTY | 3/17 | 2024-02-08, 2024-08-08, 2025-04-09 (Wed holiday roll) |
| BANKNIFTY | 1/17 | 2024-10-09 |
| SENSEX | 5/17 | 2024-04-05, 2024-06-07, 2024-12-06, 2025-02-07 (Friday regime); 2025-10-01 (Wed holiday roll) |
| BANKEX | 0/17 | none |

So b (expiry) and c (macro) share sessions. With 17 macro days, Sensex c is weakly separated from b: 12 of its 17 days are not expiry days.

Expected effect, untested until refit: before this change, MPC variance would have loaded on a[Fri] and a[Wed] and, for Sensex, on b. Rerun `src.clock.estimate` with and without the dummy and report how a[Fri], a[Wed] and b move.

Rejected:
- Dates from memory or secondary sites (news, broker calendars). D-02 forbids them.
- The Governor's Statement or press-conference date. Same day here, but the resolution is the decision of record.
- Marking the first meeting day. Only the announcement day carries scheduled news.
- Proving completeness from the next-meeting chain. It is blind to off-cycle meetings.

Falsifiers:
- An rbi.org.in release showing an MPC decision in 2024-01..2026-11 that is not in the list.
- A minutes or resolution number that breaks 47..63.
- For Sensex, c is not robust to dropping the 5 MPC sessions that are also expiry days: a sign flip or a CI that more than doubles. If so, b and c are not separately identified there; report only their sum.

---

## D-26 · Clock fits persisted; the macro dummy, finally (D-07, D-08b, D-22) - SET

`src.clock.regimes` reruns every within-regime fit and writes three tables. Before this the D-08b and D-22 numbers existed only in logs.
- `clock_fits`: point, 95% CI, cond, VIF, `n_draws` of `n_boot`, the share of draws outside [0.1, 10], `identified`, `weekdays_in_range`, a `flag` naming the parameter that tripped it, and `split_rule`.
- `clock_boot`: every normalised draw.
- `clock_obs`: the NIFTY regimes, demeaned observed vs fitted.

**Reproduction is exact; the half conventions are reproduction conventions, not a rule.** Full regimes use 300 draws, halves 100, seed 0. How each half is split:
- NIFTY Thu: the "halves" are the two sides of the D-10 blackout (..2024-11-12, 2025-02-03..).
- NIFTY Tue and Sensex Thu: the end of the D-10 post-window, 2026-02-28.
- Sensex Tue: its calendar midpoint.
- Sensex Fri: 2024-06-18, recovered by scanning split dates, not derived. Its second half moves 1.398 to 1.426 over neighbouring dates.

Each row carries its `split_rule`. Because of this, the half table is not evidence of stability. `tests/test_clock_regimes.py` pins all 27 published values.

**Two published halves are degenerate.**
- NIFTY Thu h1 (0.675): cond 2.96e10, weekend multiplier 1.5e-10. Its weekday weights are in range, and dropping the weekend parameter gives 0.683 at cond 6.9. The Thursday number stands, but the fit should have been flagged.
- Sensex Fri h1 (1.137): cond 9.5e8, Monday 4e-8. Its weekdays are out of range. Not a finding.

D-22's half table should mark both. So should Sensex Tue h1/h2 (cond 42.7 / 31.7, 49% of h2 Tuesday draws at the boundary: an indicative interval only). No bootstrap draw failed on any fit (n_draws = n_boot on all 205 rows).

**Macro dummy (17 RBI MPC + 3 Budget sessions, D-07a).** Identified on NIFTY: x2.245 [1.835, 2.604] in the Thu regime and x2.032 [1.564, 2.678] in the Tue regime, VIF 1.1-1.2. Weekday weights move by at most 13.5%:

| | weekday-only | + macro |
|---|---|---|
| NIFTY Thu regime, Thu | 0.699 [0.483, 0.889] | 0.771 [0.620, 0.932] |
| NIFTY Thu regime, Fri | 1.299 [1.058, 1.477] | 1.134 [0.890, 1.335] |
| NIFTY Tue regime, Tue | 0.607 [0.298, 0.844] | 0.645 [0.351, 0.878] |
| NIFTY Tue regime, Mon | 1.459 [1.163, 1.780] | 1.442 [1.179, 1.733] |
| Sensex Fri regime, Fri | 1.409 [1.077, 1.693] | 1.338 [0.808, 1.661] |

The expiry-weekday suppression survives the contaminant check, and 3 of 4 NIFTY halves still exclude 1. Two premiums were partly scheduled macro variance:
- The Thu-regime Friday premium: 5 of the window's 12 macro sessions are Fridays, and with the dummy the premium is no longer significant.
- The Sensex Friday high: "Sensex own expiry is high" (D-22 reading 3) weakens to "not low".

Weekday-only remains the published specification. With-macro is the robustness row, and `clock_fits` carries both.

**Bank Nifty and Bankex.**
- Bankex is not estimable from the options channel: 507 quotes on 506 dates, and one maturity per date demeans to nothing. All six fits are refused.
- Bank Nifty's Wednesday-weekly regime runs the weekend multiplier to zero. It is flagged, with weekdays in range: Wed 1.16 [0.85, 1.42].
- Its two monthly-only regimes fit: Thu 1.08 [0.61, 1.67] at cond 15, and Tue 0.81 [0.49, 1.16] at cond 5.0.
- No Bank Nifty own-expiry weight is significantly low, which is consistent with a lower dose (D-04).

**Estimator changes, and what they touch.**
1. `_expiry_days` counts an expiry only if its series is quoted on that session, the same rule as `regime_scan`. Re-dated contracts (NIFTY 2025-09-25 -> 09-30) had been marking phantom expiry sessions. Within-regime fits carry no expiry dummy and are unaffected. The full-sample default fit moves: NIFTY expiry_day 1.043 -> 1.003 and Thu 0.741 -> 0.763; BANKNIFTY Tue 0.911 -> 0.571. Neither is quoted anywhere.
2. `fit` refuses when the demeaned Jacobian at the start is rank-deficient or residual df <= 0. This used to return start values and zero-width intervals.
   - D-11 at h = 63 has 19 Sensex pseudo-windows (2024-04-02..08-13) that had cond 2.5e16 to inf. They become NaN, and `summarise` drops them from the null.
   - Sensex all-fits: n 48 -> 29, p 0.020 -> 0.033. DiD all-fits: p 0.102 -> 0.100.
   - The NIFTY rows, the h = 42 rows and the "39 of 48 degenerate" count are unchanged. `permutation.parquet` needs a rerun.

Rejected:
- Dropping unidentified fits from the table. That would hide exactly the cases a reader needs to see.
- Choosing new half splits on a uniform rule now. That would quietly change published numbers, so the rule is recorded instead.
- Keeping the old `_expiry_days`. It was wrong whenever the exchange re-dates contracts.

Falsifiers:
- An independent RBI/Budget list that changes the macro sessions and moves NIFTY Thu outside [0.62, 0.93].
- A half-split rule under which a NIFTY half reverses direction.

---

## D-27 · The continuous-hedging limit: the floor, and where the clock overtakes rebalancing - SET
Question: as rebalancing m -> inf, what sd floor does calendar-minus-clock P&L keep, and at what m does it overtake discretisation? Setup: Tue-regime clock, opened Wed, 5 sessions, 13% vol, spot 25000, entry 365.26, zero cost. Code: src/sim/mlmc.py (Giles estimator), src/sim/continuous_hedge.py (closed forms; a needed new module outside the original file list), src/experiment/hedging_limit.py. Tables: results/tables/{mlmc_levels,hedging_limit}.parquet.

**Floor.** F = 12.17 index points = 3.33% of premium = 0.43 vol points (vega 28.10 points per vol point). This is the closed form, 12.169. A straddle's dollar gamma is a Gaussian kernel in log-moneyness, K n(x; R/2, R), so E[I^2] is a double time integral of Gaussian products. Confirmations:
- (a) The hedge itself: MLMC over m = 1..256 with exact subsample coupling and a Richardson tail at alpha = 1 gives 12.151 +/- 0.017 (z = -1.05).
- (b) The EKJS sum by MLMC: 12.170 +/- 0.006.
- (c) Control variate at m = 256 (EKJS sum, exact E[I_m^2]): 12.157 +/- 0.005.
- (d) A review-time pathwise MC on a sqrt(T - t) grid, sharing no code: 12.173 +/- 0.005.
- The exact discrete E[I_m^2] approaches F^2 like a/m plus an m^-3/2 corner term; m(E[I_m^2] - F^2) = -249 at m = 256.

**Rates.**
- Clock trader: sd(clock_m) = c m^-1/2 with c = 144.2 +/- 0.5 at m = 256, against 144.8 leading order (Bertsimas-Kogan-Lo form). Crossover m* = (c/F)^2 = 141 hedges per session, about one every 2.7 minutes of a 375-minute session. Below it rebalancing is the larger risk; above it the clock is.
- Calendar trader: c_cal = sqrt(m(E[cal^2] - F^2)) = 143.4 +/- 1.3, m*_cal = 139 +/- 3. Both are measured, not predicted. E[cal^2] - F^2 = E[e^2] + 2E[I_m e] + (E[I_m^2] - F^2) with e = cal - I_m. The noise term matches its closed form (sqrt(m E[e^2]) = 138.6 +/- 0.4 against 138.8). The cross term m E[2 I e] = +1451 +/- 235 is the same order 1/m and has no closed form here. Rejected: quoting the noise-only 138.8 (and m* = 130) as the calendar constant. It sat 3.5 se off the measurement.
- The difference around I: c_diff = 15.14 against 15.15 closed form, so diff - I_m = O(m^-1/2) pathwise. That is A6's L2 identity, now with a constant.
- That the m^-1/2 rate survives the straddle's kink is a measurement (flat sqrt(m E[clock^2]) over l = 3..8), not a cited theorem. Not cited: Gobet-Temam 2001, which has a 2014 correction note that was not read.

**MLMC does not pay here.**
- beta fits at 0.83 over l = 2..8 (0.96 locally at a review-time l = 9). That is the Giles boundary beta = gamma = 1, approached from below.
- c_diff > F (m*_diff = 1.6), so a coarse level predicts the fine one poorly and V_l C_l is flat.
- Cost ratio single/MLMC is 0.58. The control variate costs 42x less than single level.
- MLMC is kept for the sd-against-m curve and for lim E[diff^2 - I^2] = -0.47 +/- 0.44 points^2 (theory 0).
- Rejected: telescoping E[clock_m^2]. The level-0 variance (1.3e9) buries a level-8 mean of 81.

**Falsifiers.** Any of these would mean the hedge does not converge to the EKJS integral or the kernel algebra is wrong:
- lim E[diff^2 - I^2] not 0
- c_diff or c_cal_noise drifting with m (15.45 and 138.0 at m = 16; 15.14 and 138.6 at m = 256)
- F not linear in the clock deviation (F(eps)/|eps| = 11.63 at eps = +/-0.01)
- a flat clock not giving F = 0 (tested)

Stale after this entry: every '2.1-2.4% share' figure.

---

## D-28 · The entry-price headline under stochastic volatility, and terminal MC - SET
Both traders price in time-changed Heston (D-24 dynamics, v0 = theta, kappa 2, theta 0.13^2), exactly. The affine char fn is chained backward over constant-clock pieces using the Mikhailov-Noegel closed form with nonzero B0, and the Lewis integral is cut at 40/sd.
Checks:
- w=1 reproduces plain Heston to 1e-12.
- Splitting a piece changes nothing.
- A complex ODE integration agrees to 1e-15 over the whole integration range, compared on the exponent at u = 6000 where |phi| ~ 1e-19.
- xi=1e-3 gives Black-76 to 2e-7.
- An independent Gil-Pelaez inversion agrees to 2e-6 index points.
- Clocked QE MC agrees within 3 SE.

Result: the Monday 1-session error is -21.831% under GBM and moves at most 0.006 pp over rho {0,-0.3,-0.6,-0.9} x xi {0.2,0.5,0.8}.
- Why Monday barely moves: on one constant piece the variance law is the same for both clocks, and only the skew term k3/(12m) differs, by rho xi T (sqrt(w)-1)/8.
- Largest movers: the Mon calendar spread, +0.36 pp at (-0.9, 0.8), and Fri, +0.34 pp at (0, 0.8). Fri moves because a heavy session followed by the light expiry session lowers k4/m^2.
- The Edgeworth factor 1 + k3/(12m) + (k3^2/m^3 - k4/m^2)/24 predicts every single-straddle shift within 0.013 pp.
- Applied leg by leg to the spread, it gets the sign right in all 12 cells but misses by up to 0.096 pp (rho 0, xi 0.8: +0.148 predicted vs +0.052 exact). The truncation error on the 6-session leg, 1.3e-3 to 1.6e-3 relative, cancels in a ratio of straddles but not in a difference of them.
- An independent rho=0 mixing MC confirms Fri at +0.340 +/- 0.005 pp.
- The headline is a property of the clock's shape, not of constant vol.

Terminal MC (D-14 made good). Estimators:
- Conditional MC (Romano-Touzi mixing).
- Conditional MC plus a frozen-variance Black-76 control on the same normals.
- RQMC: scrambled Sobol with PCA ordering, 16 randomisations.

Efficiency is 1/(variance x seconds), relative to plain. Variance per path comes from 3x2^17 i.i.d. units (relative SE under 1%), seconds from the fastest of 3 runs. The first table used 16 replications and was wrong in direction: it showed conditional at 0.51x plain, which breaks Rao-Blackwell.
- Conditional: 7.0 to 8.6x at (-0.6, 0.5), 1.45 to 1.62x at (-0.9, 0.8). The gain falls with |rho| because only the (1-rho^2) share of spot noise is integrated out.
- With the control: 111 to 541x at (-0.6, 0.5), 7.4 to 36x at (-0.9, 0.8).
- RQMC-conditional: RMSE ~ n^-1 on 1-2 session straddles despite QE's branch switch (slope -0.94 to -1.09; -0.79 on the 6-session spread). At n=2^15 that is 2e3 to 7e5x plain.
- Rejected, antithetics: ATM straddles are even in the spot normal, giving efficiency 0.47 to 0.67 of plain (variance ratio 0.51 to 0.67).
- Rejected, linear controls in I: the conditional straddle is even in rho I.
- Timing noise reaches 27% between repeats on a shared machine, so efficiency ratios are good to about +/-30%. The variance part is good to about 2%.

Falsifiers:
- Any w=1 or split-piece mismatch above 1e-12.
- A Fourier-vs-QE gap above 3 SE.
- A single-straddle shift outside the expansion by more than 0.02 pp.
- Per-path var(conditional) > var(plain) on shared variance paths.

---

## D-29 · Clock uncertainty carried into the price and the hedge - SET

`src.experiment.clock_uncertainty` reruns the entry-price and hedging experiments once per bootstrap
draw of the Nifty within-regime clock (300 per regime, `clock_boot`), each draw rescaled to a
five-day mean of one so both traders still agree on the week. Prices are exact Black-76 per draw.
Hedging uses 50,000 paths per draw with **the same seed for every draw** (common random numbers), so
the spread across draws is parameter uncertainty, not path noise.

**Derived before running, and confirmed.** With the week pinned at 5, a one-session straddle and the
Monday calendar spread depend on a single weight, monotonically, so a percentile interval of that
weight maps through exactly: the old "-45% to -8%" mapping was already right, and the propagated
band reproduces it to the third decimal. Multi-session spans depend on the joint law, and the
mean-one normalisation makes weights negatively correlated, so reading them off marginal intervals
is wrong:

| Tue regime | point | propagated 95% band | P(< 0) | naive (marginal) band |
|---|---|---|---|---|
| straddle opened Mon (1 session) | -21.8% | [-45.2%, -8.0%] | 1.00 | [-45.2%, -8.0%] |
| opened Fri (2) | +2.0% | [-2.4%, +7.1%] | 0.20 | [-14.4%, +15.0%] |
| opened Thu (3) | -0.6% | [-5.1%, +3.2%] | 0.63 | [-16.6%, +12.2%] |
| opened Wed (4) | -1.3% | [-5.0%, +2.8%] | 0.74 | [-17.6%, +12.9%] |
| opened Tue (5, full week) | 0 | exactly 0 | - | [-16.5%, +13.8%] |
| Monday near/far spread | +9.5% | [+3.3%, +21.0%] | 0.00 | [-3.6%, +23.3%] |

Thu regime, the one-session straddle (opened Wednesday): -16.6% [-30.5%, -5.9%], negative in 99.7%
of draws. **The one-session error is negative in essentially every bootstrap clock in both
regimes.** Only that row and the spread are robust in sign; everything over two or more sessions is
indistinguishable from zero once the clock's own uncertainty is carried.

**Hedging.** The clock-risk floor at the point fit is 12.16 (Tue regime) and 13.14 (Thu regime)
index points, but across bootstrap clocks it spans [4.9, 23.1] and [7.8, 19.3]. That is the right
size, not noise: the floor is linear in the clock's deviation from flat (D-27), so it inherits the
weights' full relative uncertainty. Predictive sd of the calendar-minus-clock P&L at one hedge per
session, `sqrt(E_w[sd^2])`: 12.34 (Tue) and 12.48 (Thu). The simulated `I_64` sd matches its exact
per-clock value (11.997 vs 12.011 at the point fit, and draw by draw across the band), which is the
check that the inner simulation is right.

**Nested-simulation bias.** Every P&L here has mean exactly zero (D-25), so the predictive variance
is `E_w[sd(w)^2]`, linear in the inner second moments and unbiased at any inner N. The outer
quantiles of `sd(w)` carry the Gordy-Juneja inflation, about `(kurtosis - 1) sd^2 / (4N)`, measured as
0.02-0.07% of the between-draw variance for the clock-risk quantities (`diff_sd`, `ekjs_sd`). It is
not small for the clock trader's own sd (5% Tue, 88% Thu regime): that sd barely depends on the
clock, so its spread across draws is mostly inner noise, and its band is not read as parameter
uncertainty.

**Read with care.** Per-draw *means* share one path realisation, so their P(< 0) of 1.00 (clock
trader, Tue regime: -0.35) is one common Monte Carlo error, not an effect. The exact mean is zero.

**Rejected.** Reporting the hedging floor at the point fit alone: it hides that the clock's
uncertainty, not path noise, is what makes the floor uncertain. Re-seeding per draw: it would mix
path noise into the parameter band.

---

## D-30 · STT on index options: sourced, and it cancels from the entry-price error - SET

**Rates** (data/reference/stt_rates.json; every number read from a primary document, with its URL and sha256 prefix):
- Statute: Finance (No. 2) Act 2004 s.98, serial 4.
- Option sale, on the premium, paid by the seller: 0.0625% to 2024-09-30. 0.1% from 2024-10-01 (Finance (No. 2) Act 2024 s.162; NSE/FATAX/63809; BSE 20240916-61). 0.15% from 2026-04-01 (Finance Act 2026 s.159; NSE/FATAX/73524).
- Exercised option, on the intrinsic price (settlement minus strike), paid by the purchaser: 0.125%, rising to 0.15% from 2026-04-01.
- Futures: 0.0125 -> 0.02 -> 0.05%.
- Before 2019-09-01, exercise was taxed on the settlement price (Memorandum to the Finance (No. 2) Bill 2019). That is the base behind the folklore "STT trap". It is outside the sample.

**Result** (src/experiment/stt.py -> results/tables/stt_effect.parquet; Black-76 closed forms, no MC):
- Reservation prices:
  - a buyer who holds to exercise pays at most (1-e)C;
  - one who sells on expiry day instead pays (1-p)C less the spread;
  - a writer accepts at least C/(1-p).
- Every one of these is C times a constant. So the calendar-vs-clock error, a ratio of two prices, is **unchanged at -21.83%** (1 session) and at +1.97 / -0.58 / -1.26 / 0.00% (2-5 sessions), in all three rate periods.
- Largest measured shift: 2.2e-16. The cancellation is exact, not approximate. Quadrature of the taxed payoff at the clock and flat variances reproduces it to 1e-9, independently of the closed forms.
- It survives windows that straddle 2024-10-01 or 2026-04-01, because the entry price carries only p and the exit only e.
- Buyer-seller band: (p/(1-p) + e)C = 0.19 / 0.225 / 0.30% of the price by period, 0.24-1.10 pts across periods and opening days. Exercise STT alone is 0.16-0.55 pts.
- Selling on expiry day instead of exercising saves (e-p)C:
  - 0.08-0.23 pts to 2024-09;
  - 0.03-0.09 pts from 2024-10 to 2026-03;
  - zero from 2026-04, when the two rates are equal.
- Rupee size of the error scales by at most 0.15%.

**Expiry-day trap (D-17's worry).**
- On the intrinsic base, exercise nets (1-e) times intrinsic >= 0 for every settlement. No trap exists, analytically.
- On the pre-2019 notional base the losing band is K/(1+e) < S_T < K/(1-e), about 2eK wide (62.5 pts at 0.125%).
- A 1-session ATM straddle settles inside that band 15.5% of the time under the clock, against 12.1% under flat variance. The band is fixed while the low Tuesday weight cuts the sd of S_T from 205 to 160 pts, which concentrates mass near the strike. It falls to 5.4% at 5 sessions, where clock and flat agree.
- That base would have widened the error to -27.0% (forced exercise) or -26.2% (optimal exercise) at 0.125%, and to -28.3% / -27.2% at 0.15%.
- Only a tax that is not proportional to the payoff moves the clock result. Current law has none for options held to expiry or closed on expiry day.

**Rejected.**
- (i) Modelling STT as a premium haircut on the clock price alone. That is an asymmetric treatment, and it manufactures an error that the symmetric statute cancels.
- (ii) Using the notional base for the sample. It is not the law after 2019-09-01 (memo2024 confirms the intrinsic base). It is kept only as the labelled cf_ columns.

**Not covered.** Futures STT on the delta hedge (0.02%, then 0.05%, of futures sold at each rebalance). It depends on the path and on the hedge frequency, so it does NOT cancel. It belongs to the hedging experiment (D-15/D-25), not the entry price, and is unmeasured here.

**Falsifiers.** Any of these would reopen the cancellation:
- a statute or exchange circular in the sample that taxes exercise on anything other than the intrinsic price;
- an STT rate that differs between the two legs of the comparison;
- a fixed per-contract charge. The verifier measured one: c0 = 1 / 5 pts moves the Monday error to -21.97 / -22.52%.

Open: BSE notice 20260331-7 was not read (the scripted fetch returned an empty page). The 2026 Sensex rates rest on the Act and on NSE/FATAX/73524; s.98 applies to every recognised exchange.

---

## D-31 · Bankex, the second BSE unit - SET (result: not identifiable)

Added 2026-11-10 (TO_DO 3.1). The aim was a BSE counterpart to Bank Nifty with a smaller treatment dose.
- Same reader, forward, inversion and filters as Sensex (D-22, D-05, D-06). The only change is `bse_option_symbols = ("SENSEX", "BANKEX")`.
- Every pre-existing NIFTY, BANKNIFTY and SENSEX row in the panels, forwards, surface and ATM tables is unchanged: same row counts, `DataFrame.equals` True.

**Measured timeline** (`regime_scan` -> `regime_timeline_bankex.json`):

| Regime | Span | Expiries |
|---|---|---|
| Monday | 2024-01-01 -> 2024-12-30 | 49; weekly through **2024-11-18**, then monthly only |
| Tuesday | 2025-01-28 -> 2025-08-26 | 8, monthly |
| Thursday | 2025-09-25 -> 2026-08-27 | 12, monthly |

- **Weekly removal at Nov-2024 is measured, two ways.**
  - Month-last rule: `last_weekly_expiry` 2024-11-18. The panel's tenor split agrees, but it uses the same rule, so that is a consistency check.
  - Spacing, with no month rule: consecutive actual expiries are at most 10 days apart up to 2024-11-25, and at least 27 days apart after it. 2024-12-30 is December's only expiry.
- **Rolls:** 7, all backward. Three Monday-regime rolls land on the previous Friday and one (2024-01-20) on a Saturday session. `days_early` counts across the weekend.
- **Lot size:** 15 up to the Jan-2025 contract. 30 for Feb-2025 onward, first quoted 2024-12-12. 30 alone from 2025-01-29.
- **Not run as an event:** Bankex moved Mon -> Tue (Jan-2025) and Tue -> Thu (Sep-2025), but there is no weekly in either regime.

**A2a: futures-side trade floor, adopted post hoc.** Bankex failed the unfiltered gate: median +1.08bp, MAD 5.22bp on 589 expiry-days. The gate then gained `A2A_MIN_FUTURE_TRADES = 5` for every symbol, on D-06's own reasoning: an untraded settle is an exchange computation, not a price (D-02a). This was chosen after seeing the failure. The evidence that it is the benchmark and not the forward:
- Bankex residual MAD by futures trade count: 8.2bp at 0 trades, 8.9bp at 1-4, 4.7bp at 5-49, 2.7bp at 50+. The parity forward's own standard error is 0.7-1.7bp throughout.
- The pass is not knife-edge:

| Symbol | Floor 0 | Floor 1 | Floor 5 | Floor 10 |
|---|---|---|---|---|
| BANKEX (n, median, MAD bp) | 589, +1.08, **5.22** | 499, +0.73, 4.38 | 384, +0.93, 3.88 | 301, +0.69, 3.47 |
| SENSEX | 1108, -1.22, 1.77 | 1102, -1.21, 1.76 | 1093, -1.23, 1.78 | 1084, -1.24, 1.75 |
| NIFTY | 1901, +1.51, 1.57 | same | same | same |
| BANKNIFTY | 1769, +0.40, 2.12 | same | same | same |

- `test_bankex` checks Bankex A2a at floors 1, 5 and 10.
- A2b: weekly 6.66 points, monthly 12.34.
- **Falsifier:** a Bankex forward whose MAD against *50+-trade* futures exceeds 5bp.

**The clock is not identified in any regime, and no filter choice fixes it.**
- 507 ATM expiry-days: 98 weekly, 409 monthly.
- Dates carrying two or more maturities: 1 in the Monday weekly era, 0 in Tuesday, 0 in Thursday.
- Why that is fatal: the options channel (D-08) identifies weights only through differences between maturities on the same date. Removing the per-date scale zeroes a single-maturity date.
- Result: rmse 0, condition 3.9e31 (Monday) or infinity (Tuesday, Thursday), bootstrap intervals collapse to points. The printed weights are the optimiser's start values; nothing may cite them.
- Cause: Bankex trades only its nearest contract. Median trades per expiry-day are 7,106 for the nearest listing, 4 for the second, 1 for the third. Dropping the D-06 floor from 5 to 1 adds a second maturity on only 37 dates.
- **Rejected:** loosening D-06 to manufacture a second maturity. That fits the exchange's settlement model, not the market.
- **Rejected:** a cross-regime fit with an expiry dummy. Every window still has one maturity per date.
- **What would change this:** a Bankex returns channel (D-09). Bankex index closes are not downloaded.
- **Falsifier:** a later sample in which more than ~30% of Bankex sessions carry two or more liquid maturities.

### D-31a · Correction to D-02b and D-22 · Contracts moved to new dates are not off-weekday expiries

`regime_scan` used to take the set of *listed* expiry dates. Exchanges move live contracts to new dates when the schedule changes. The old date is still a trading day but never expires, so the scan read it as an off-weekday expiry. An expiry now counts only if its contract is quoted on its own expiry session.

The scan now fails on a missing daily file instead of skipping it. A test pins that all 666 sessions have both the NSE and the BSE file, so an expiry cannot drop out silently.

- **NIFTY, confirmed from the raw zips.**
  - 2025-07-31: 198 rows (99 strikes) for Thu 2025-09-25, OI 27.6M.
  - 2025-08-01: 0 rows for 09-25, and 198 rows (99 strikes) for Tue 2025-09-30, OI 28.6M.
  - 2025-09-25: no NIFTY option expires.
  - 2025-12-24 and 2026-06-25 were moved the same way.
  - Effect: Tue regime 56 -> **53** expiries. Off-weekday expiries 11 -> **8**, all backward rolls. The "3 not rolls" in D-02b were these moved contracts, not other tenors.
- **SENSEX.**
  - Fri regime 54 -> **53**. 2024-11-01 was listed only 2024-09-24..09-26; the 2024-10-31 contract replaced it.
  - Tue regime 39 -> **34**. 2025-01-10, 01-17, 01-31, 02-07 and 03-28 were all last quoted 2024-12-31 and moved to Tuesday.
  - Thu regime unchanged at 53. Off-weekday expiries 15 -> **10**, all backward.
  - **Retracted:** "five Friday expiries sit inside the Tuesday regime, contracts listed under the old schedule running off". None of them expired.
- **Unchanged:** regime boundaries, lot staircases, weekend sessions, XpryDt mismatches (0), for both symbols.
- **Resolved in D-26:** `src.clock.estimate._expiry_days` had marked the moved dates as expiry days:
  - NIFTY 2025-09-25, 12-24, 2026-06-25
  - SENSEX 2024-11-01, 2025-01-10, 01-17, 01-31, 02-07, 03-28
  - BANKEX 2025-01-27, 02-24
  - This affected only fits with the `expiry_day` dummy. D-26 applies the same quoted-on-its-own-session rule there.

---

## D-32 · Bank Nifty dose-response across Nov-2024 and Sep-2025 - SET, RUN: null

TO_DO 3.2, D-04. `src.analysis.dose_response`. Roles are read from the panel per event. A unit is treated when the modal weekday of its own expiry sessions changes between windows. An expiry counts only if its series is quoted on its own session; BANKNIFTY 2025-01-29, re-dated and last quoted 2025-01-01, is not an expiry session.

**Measured roles.**
- Nov-2024: BANKNIFTY treated, Wed -> Thu. The pre-window had 24 Wednesday weeklies and 2 holiday-shifted Tuesdays, so d* = Wed. The post-window has 6 Thursday monthlies. Dose 1.00 -> 0.25.
- Nov-2024: NIFTY is the control, Thu -> Thu, dose 1.00.
- Sep-2025: both treated, Thu -> Tue. NIFTY dose 1.00 -> 0.96, BANKNIFTY 0.25 -> 0.23.

**Windows and the lot staircase.** `tests/test_dose_response.py::test_window_cut_offs_match_the_measured_lot_staircase` measures the lot staircase from the panel and asserts it against WINDOWS. These dates are therefore measured, not remembered.
- Bank Nifty lots: 15 -> 15&30 (2024-11-28) -> 30 (2025-01-31) -> 30&35 (2025-04-25) -> 35 (2025-06-27) -> 30&35 (2025-10-29) -> 30 (2025-12-31).
- Nifty lots: 25 (2024-04-26) -> 25&75 (2024-11-22) -> 75 (2025-01-31) -> 65&75 (2025-10-29) -> 65 (2025-12-31).
- The D-10 blackout ends on 2025-01-31, the day both overlaps resolve.
- Windows with one lot size per unit: the Nov-2024 pre-window, the Nov-2024 post-window cut to 2025-04-24, and the Sep-2025 post-window cut to 2025-10-28.
- Mixed windows: the Nov-2024 full post-window (BN), the Sep-2025 full post-window (both), and **the Sep-2025 pre-window (BN, 2025-04-25 and 2025-06-27), which no variant cleans.**

**Pre-registration, written 2026-11-10T09:05+05:30 before any statistic (verbatim, tmp/dose_prereg.txt):**

    Bank Nifty dose-response (TO_DO 3.2, D-04) - PRE-REGISTRATION
    Written 2026-11-10T09:05+05:30, before any Bank Nifty weight, return statistic or expiry weekday was
    computed for this item. Nothing below has been looked at in the data yet.

    DEFINITIONS (fixed now)
    - d*  = the modal weekday of BANKNIFTY *weekly* expiry dates in the Nov-2024 pre-window, measured
            from the panel (tenor == "weekly"), not assumed. Expected from memory: Wednesday; the code
            uses whatever is measured.
    - Roles are derived from the data per event, never hardcoded: a symbol is TREATED at an event if
      the weekdays carrying its expiry sessions change between the pre- and post-window; CONTROL if
      they do not. Expected: Nov-2024 treated = BANKNIFTY (weekly ceases), control = NIFTY; Sep-2025
      both treated, BANKNIFTY at lower dose (monthly only).
    - dose  = share of the moved weekday's sessions in the window that are own-expiry sessions.
    - Weights: mean-one over the window's weekday sessions (D-01).

    WINDOWS (fixed now)
    - Nov-2024: pre 2024-05-13 .. 2024-11-12 (six months, starts after the 2024-04-26 lot change);
      post 2025-02-01 .. 2025-07-31 (six months after the D-10 blackout 2024-11-13 .. 2025-01-31).
      If a BANKNIFTY lot change is measured inside either window, it is reported, not trimmed silently.
    - Sep-2025: D-10 windows, pre 2025-03-02 .. 2025-08-28, post 2025-09-02 .. 2026-02-28, plus the
      contamination-free sub-window 2025-09-02 .. 2025-10-28 reported beside it.

    CHANNELS (fixed now)
    - Returns: close-to-close log returns of "Nifty Bank" and "Nifty 50" on the trading-session
      sequence (weekend sessions stay in the chain, excluded from the weekday vector, D-02c), divided by
      an EWMA variance forecast (lambda 0.94, information to t-1 only). Weekday weight = mean z^2 on
      that weekday / mean z^2 over weekday sessions. Budget and RBI MPC sessions excluded from the
      weekday means (the D-07 macro control, done by exclusion). Inference: block bootstrap resampling
      whole trading weeks, the same weeks for both indices, pre and post independently, 2,000 draws,
      seed 0.
    - Options: src.clock.estimate.fit(symbol, window, dummies=("weekend",)), 300 draws. A fit is read
      only if condition number <= 30 and every weekday weight lies in [0.1, 10] (the D-11 health rule).
      The options DiD is reported only if all four fits pass; otherwise "not evaluable".

    ESTIMANDS
    - Nov-2024:  DiD = [ln w_BN(d*,post) - ln w_BN(d*,pre)] - [ln w_N(d*,post) - ln w_N(d*,pre)].
      Same weekday in both units, so any common shock to d* (other NSE weeklies removed at the same
      event, calendar drift) cancels.
    - Sep-2025:  S_u = [ln w_Tue(post) - ln w_Tue(pre)] - [ln w_Thu(post) - ln w_Thu(pre)] per unit,
      and the dose ratio R = S_BN / S_N.

    PREDICTIONS
    H1 (own-expiry suppression, Nov-2024): if the expiry-session suppression is caused by the unit's
       own weekly expiry, BANKNIFTY's d* weight rises toward 1 after its weekly is removed while
       NIFTY's d* weight does not move: DiD > 0. With a suppression multiplier of 0.65-0.70 (the Nifty
       point estimates, D-08b), the expected DiD is about +0.35 to +0.45. Corollary: pre-window,
       w_BN(d*) < w_N(d*). NIFTY's own Thursday stays low across Nov-2024 (its weekly survived).
    H0 / H_market: the suppression follows the dominant NSE expiry (Nifty Thursday), not the unit's
       own: DiD ~ 0 and BANKNIFTY's d* weight is not low pre-window.
    H2 (dose, Sep-2025): S_BN has the same sign as S_N (negative) and is smaller in magnitude. Under
       H_own with multiplier m and dose f, S ~ 2 ln(1 - f(1-m)): with m ~ 0.65 and f_BN ~ 0.2-0.25,
       R ~ 0.2. Under H_market, in the returns channel R ~ 1 (the Bank Nifty index shares the market
       factor and the Nifty expiry day).

    POWER, stated before running
    - About 26 sessions per weekday per six-month window; z^2 has variance 2 (Gaussian) to ~5 (fat
      tails), so SE(ln w_d) ~ 0.3-0.45 per window and per unit; the cross-index correlation of z^2
      (expected ~0.5) shrinks the DiD SE to roughly 0.4-0.6. An effect of +0.4 is therefore expected
      to be at or below one standard error. The returns channel is expected to be underpowered for H1,
      and a CI straddling 0 is the most likely outcome. That would be a non-result, not evidence for
      H0.
    - Options channel post-Nov-2024: BANKNIFTY is monthly-only; D-09 expects it weakly identified.
      Expected to fail the health rule; if so it is not read.

    RANDOMIZATION INFERENCE
    - Returns channel only (no fits, so cheap). Pseudo events every 5 sessions, h = 63 sessions each
      side, eligibility from src.analysis.permutation._eligible (no window crosses Sep-2025 or touches
      the Nov-2024 blackout). True-event statistic at h = 63: the 63 sessions before 2024-11-13 against
      the 63 sessions from 2025-02-01. Effectively independent windows ~ 666 / 126 ~ 5, so p cannot be
      much below 1/6. Kill condition as D-11: true DiD inside the middle 90% of the pseudo
      distribution -> no evidence the Nov-2024 removal moved Bank Nifty's d* weight.

    FALSIFIERS
    - H1 falsified (not merely unsupported) if the DiD 95% CI lies entirely below +0.2 with its upper
      end below the smallest predicted effect, i.e. the data exclude a suppression of 0.80 or stronger.
    - H2 falsified if S_BN and S_N have opposite signs with both CIs excluding 0, or if R's CI lies
      entirely above 1.

**What is pre-registered and what is not.**
- Pre-registered: the Nov-2024 DiD on the stated windows, level_gap_pre, Nifty's own-Thursday move, the Sep-2025 full windows and the 2025-10-28 sub-window, the h = 63 randomization with its kill condition, and the options health rule.
- Post-hoc: the Nov-2024 post-window cut to 2025-04-24 (added after the BN 2025-04-25 lot change was measured, at 09:06, after the pre-registration), the disjoint-design count, the EWMA-0.97 and drop-largest-session sensitivities, and the Wednesday observation below.
- The killed first attempt measured lot sizes, expiry weekdays and raw fits after 09:05. Its randomization null values were seen before the burn-in rule was considered; see Rejected.

**Result, returns channel (2,000 week-block draws, seed 0):**

| event | window | quantity | estimate | 95% CI | registered |
|---|---|---|---|---|---|
| Nov-2024 | full | DiD (BN - N, Wed) | +0.189 | [-0.708, +1.093] | yes |
| Nov-2024 | full | level gap pre (BN - N, Wed) | +0.165 | [-0.307, +0.816] | yes |
| Nov-2024 | full | Nifty d ln w_Thu | -0.499 | [-1.483, +0.738] | yes |
| Nov-2024 | post to 2025-04-24 | DiD | +0.198 | [-0.810, +1.642] | no |
| Nov-2024 | full, EWMA 0.97 | DiD | +0.289 | [-0.539, +1.206] | no |
| Nov-2024 | full, without 2024-06-04 | DiD | +0.148 | [-0.690, +1.097] | no |
| Sep-2025 | full | S_N / S_BN | +0.086 / -0.334 | [-1.18, +1.25] / [-1.62, +0.78] | yes |
| Sep-2025 | to 2025-10-28 | S_N / S_BN | -1.379 / -1.543 | [-2.99, +0.58] / [-3.07, +0.52] | yes |
| Sep-2025 | to 2025-10-28 | R | 1.119 | [-1.46, +4.93] | yes |

No returns-channel bootstrap draw is non-finite (`boot_nonfinite` = 0 on every row), so the R intervals are over all 2,000 draws.

**Reading the result.**
- The DiD bootstrap sd is 0.449, inside the pre-registered 0.4-0.6.
- H1 is neither supported nor falsified. Every interval contains both 0 and the predicted +0.35 to +0.45.
- The corollary fails on its point estimate. Bank Nifty's own-expiry Wednesday was not lower than Nifty's Wednesday, and it did not move when its weekly was removed (ln w -0.791 -> -0.775).
- Across the two sensitivities the DiD point moves between +0.15 and +0.29, so the null is stable but the point is not invariant.
  - The slower EWMA moves BN Wed pre to -0.68.
  - Dropping the largest-z^2 session moves it to -0.51. That session is found by the code, not named: 2024-06-04 (Tue), Nifty z^2 36.7, Bank Nifty 43.2. It is not a D-07 macro date, and it is measured only as the largest z^2, so no claim is made about what happened that day. Removing it lowers the window mean that every weight is normalised by.
- On the full Sep-2025 window, R is not interpretable because S_N straddles 0.
- The full window and the sub-window disagree for Nifty (+0.09 vs -1.38). By the D-10 clause, the late post-window, with its lot change, is doing work. The Sep-2025 pre-window also carries two Bank Nifty lot changes that no variant removes.
- H2 is not falsified by either falsifier.

**Randomization, h = 63:** true DiD +0.515, percentile 83.3 of 48 pseudo dates, upper p 0.184. **The kill condition fires.** Only 3 pseudo designs fit without sharing a session, not the ~5 the pre-registration assumed, so p cannot go much below 1/4.

**Options channel (300 draws; health rule cond <= 30 and |ln w| <= ln 10):**
- Nov-2024: the BN post-window fit has cond 377.7 (1.6e9 on the cut post-window), so it is not readable and the options DiD is not evaluable. D-09 predicted this.
- Nov-2024 pre-window fits are healthy (cond 5.7 BN, 6.8 Nifty). BN own-expiry Wednesday w = 1.136 [0.565, 1.599] against Nifty own-expiry Thursday 0.522 [0.108, 0.974]. The point estimate shows no suppression on Bank Nifty's own weekly expiry. The interval does not exclude the 0.65-0.70 multiplier, and the two intervals overlap, so this fails to show suppression rather than excluding it.
- Sep-2025 full: all four fits pass (cond 9.4, 29.1, 5.3, 5.9). S_N -1.765, S_BN -1.323, R = 0.75, as points only, because `fit` returns intervals, not draws. That sits nearer H_market's R ~ 1 than H_own's R ~ 0.2. It is not claimed: there is no interval, and the BN pre-window fit is 0.9 condition units from the threshold.
- Sep-2025 sub-window: the BN post fit has cond 1.06e10, so it is not readable.

**Unregistered observation, not tested.** In the returns channel Wednesday is the low day for *both* indices in three of four windows (w 0.32-0.46). That includes Nifty, whose expiry is Thursday, and Feb-Aug 2025, when no NSE index weekly expired on a Wednesday. It reverts to ~1 after Sep-2025. In close-to-close returns, the session before the Nifty Thursday expiry is quiet. That is not the own-expiry mechanism. It also restates D-08b: the returns channel does not reproduce the options-channel expiry-day suppression.

**Rejected.**
- Dropping pseudo windows whose pre-window starts inside the EWMA burn-in (first 63 sessions). It would move the percentile from 83 to 97 and p from 0.184 to 0.056, but the rule was written after the early null values from the killed attempt's table had been seen. That is the D-11 forking-path pattern. Those early values (+0.96 to +1.20) are also the same under any EWMA seed and under raw r^2, so the rule would remove real null mass, not an artefact.
- Excluding named sessions such as the election result from the headline. Dropping the largest-z^2 session is a data-chosen sensitivity only; common shocks cancel in the DiD.
- Trimming the Sep-2025 pre-window to Bank Nifty's constant-lot stretch (2025-06-27 to 2025-08-28, about 2 months). It would be an unregistered fork and too thin to identify a weekday weight.

**Falsifiers that still stand:** an H1 DiD CI entirely below +0.2. Under H2: opposite-signed S_BN and S_N with both CIs excluding 0, or R's CI entirely above 1.

**What could change the answer:**
- Draws from `fit`, for an options-channel interval on R.
- Intraday returns (D-09).
- The legacy 2023 reader: more pseudo windows, and a pre-Sep-2023 Bank Nifty Thursday weekly as a second Nov-type event.

---

## D-33 · Figures and the report (amends D-21) - SET

D-21 made the folder the deliverable with no paper. Asked again on 2026-11-10, the answer is figures
plus one self-contained HTML page: `uv run python -m src.report.build` writes `results/figures/*.png`
and `results/report.html` (images embedded, opens offline). Still no paper; the narrative stays here.

Rules the figures follow, so the next one does not re-decide them (`src/report/style.py`):
- Every estimate carries its interval; a fit flagged unidentified is drawn hollow, never dropped.
- Multiplicative quantities (weights, standard deviations across rebalancing grids) on log axes.
- One y-scale per panel. Colour is identity in a fixed slot order (blue = the clock or the quantity
  under study, orange = its comparison, aqua = a third series), never rank.
- Each figure's takeaway sentence is computed from the results tables at build time, and each
  figure has a table view in the report, so no number is typed in twice.
- The form follows the question: point-range forests for estimates, cumulative variance for the
  clock as a time change, log-log sd against hedge frequency for the floor (it turns `m^-1/2` into
  a straight line and the floor into a flat one), hexbin scatter for the pathwise EKJS identity,
  heatmaps for the two-parameter Heston sweep, histograms of 300 bootstrap clocks for the
  propagated price error, and a symlog strip of the randomization null so degenerate pseudo-fits
  stay visible without flattening the band that matters.

Every figure was rendered and inspected before it shipped; label collisions, a scale crushed by
degenerate fits, duplicate rows from a step-refinement study, and one overclaiming sentence were
fixed on that pass.

---

## Open

- **Intraday data** (TO_DO 1.3). The only route to the mechanism behind the Nifty expiry dip; both
  daily-data candidates failed (D-08b). A budget decision, not a modelling one.
- **Legacy 2023 reader** (D-02, D-11). Pre-committed if D-11 came out thin; it did, and D-32 found
  only 3 disjoint pseudo designs. Would add about 250 sessions and a pre-Sep-2023 Bank Nifty event.
- **Futures STT on the delta hedge** (D-30). Path-dependent, does not cancel; belongs to the hedging
  experiment and is unmeasured.
- **Bankex and Bank Nifty returns channels** (D-31, D-32). Bankex index closes are not downloaded.
- **Within-regime specification** (D-08b). Settled in practice: weekday-only within regime, the
  expiry dummy only across regimes.

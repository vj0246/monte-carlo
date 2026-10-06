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

**OPEN:** `data/reference/macro_events.json` has Union Budget days only. RBI MPC dates are not
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

**The hedging experiment is close to a null on this sample.** Short an at-the-money weekly
straddle, delta-hedge once per session, both traders forced to the same entry price (D-15).
200,000 paths, truth following the fitted Tuesday-regime clock:

| cost (bp) | clock sd | calendar sd | difference sd | share of hedging-error sd |
|---|---|---|---|---|
| 0.0 | 497.06 | 493.52 | 11.52 | 2.3% |
| 0.5 | 496.91 | 493.38 | 11.52 | 2.3% |
| 2.0 | 496.46 | 492.97 | 11.52 | 2.3% |

Discrete rebalancing error swamps the clock by a factor of forty, and trading cost does not change
that. The plan made this the headline; on this sample it is not one.

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
single session it does not. **The clock is a one-day-to-expiry phenomenon and it shows up in the
price, not the hedge.** The corresponding near/far calendar spread opened on a Monday is mispriced
+9.5%.

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
| A6 | Hedging simulator vs the EKJS closed form on each grid; correct-clock mean zero | either check missed | **pass**; original wording corrected in D-23 |
| A7 | Headline published as a surface over rebalance frequency and cost, with provenance | a single figure is published instead | **pass** (16 cells, clock share 2.1 to 2.3%) |
| A8 | Provenance complete, config hash stable, seeded runs bit-for-bit reproducible | a manifest is missing or a seeded run differs | **partial**: automated part passes, the clean-clone run is manual (TO_DO.md) |

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
can show up. Measured dispersion stays near 2% of a correct-clock hedger's P&L standard deviation
across the whole A7 grid.

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

---

## Open

- **RBI MPC dates** (D-07). Not derivable from NSE archives, not assertable from memory. Needed
  before the macro dummy means anything.
- **Within-regime specification** (D-08b). Plan: drop the expiry dummy within regime, keep it across.

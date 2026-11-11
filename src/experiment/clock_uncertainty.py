"""What the clock is worth, with the clock's own uncertainty carried through (D-08b, D-08c, D-25).

D-08c and D-25 price the calendar-time trader's error at the *point* weights and attach the
weight's marginal interval afterwards. This module redoes both experiments once per bootstrap
draw of the NIFTY within-regime clock (``clock_boot.parquet``, 300 draws per regime) and reports
the distribution across draws.

**Normalisation.** Each draw is rescaled so its five weekday weights average one. Both traders
agree on the weekly total by construction (D-15), so this is the only scale that keeps a flat
trader's week and a clock trader's week equal in every draw. The stored draws average one over the
window's observations instead; 95% of their five-day means lie in [0.994, 1.014] (Thursday
regime) and [0.988, 0.998] (Tuesday).

**What propagation can and cannot add, derived before running.** With the week pinned at 5:

- A straddle opened one session before expiry spans ``u_exp`` alone; opened four sessions before,
  it spans ``5 - u_first``. Either way the error is a monotone function of one weight, so a
  percentile interval of that weight maps through exactly, and the old "-45% to -8%" mapping of
  the Monday row is already right.
- The near/far calendar spread opened the session before expiry is long ``u_exp + 5`` and short
  ``u_exp``: again one weight, monotone, so its exact band is the map of ``u_exp``'s interval.
  What propagation corrects there is reading the far leg's six sessions off six marginal
  intervals.
- The two- and three-session spans, and the full-week straddle, need the joint law. Mean-one
  normalisation makes the weights negatively correlated (Mon-Tue -0.79 in the Tuesday regime), so
  adding up marginal intervals overstates the band; for the full week it invents one where the
  error is identically zero. ``naive`` is that sum of marginal endpoints, for comparison.
- Hedging clock risk depends on the whole vector, so it has to be recomputed per draw.

**Hedging, nested.** Per draw: the calendar-minus-clock P&L at one hedge per session, and the
pathwise EKJS integral on 64 steps per session as its continuous-hedging proxy, both on one
simulation (the daily path is every 64th point of the fine one, an exact GBM coupling). Every draw
uses the same seed, so path noise is common to all draws and the spread across them is parameter
uncertainty. Two numbers per draw need no simulation and check it: ``E[I_64^2]`` exactly
(``continuous_hedge.riemann_second_moment``) and the continuous floor (``floor_second_moment``).

Every P&L here has mean exactly zero (martingale spot, equal entry; D-25), so its sd is
``sqrt(E[X^2])`` and is computed that way. The law of total variance, ``Var X = Var_w E[X|w] +
E_w Var(X|w)``, then has a first term that is zero exactly, and the predictive variance is
``E_w[sd(w)^2]``: linear in the inner second moments, so unbiased at any inner N.
The nonlinear outer quantities, the spread and quantiles of ``sd(w)`` across draws, carry the
nested-simulation bias of Gordy and Juneja (2010): independent inner noise inflates
``Var_w(sd_hat)`` by ``E_w[Var_MC(sd_hat)]``, about ``(kurtosis - 1) sd^2 / (4 N)``. Common random
numbers make the inner errors nearly equal across draws, so the realised inflation is smaller
still; the EKJS column measures it directly against the exact per-draw value.

Run:  uv run python -m src.experiment.clock_uncertainty
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl

from src.config import Settings, settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT
from src.provenance import manifest, write_manifest
from src.sim import continuous_hedge
from src.sim.analytic import believed_paths, ekjs_pathwise
from src.sim.engine import hedge_short_straddle, simulate, straddle_price

WEEK = ("Mon", "Tue", "Wed", "Thu", "Fri")
REGIMES = {"Thu regime": "Thu", "Tue regime": "Tue"}  # label in clock_boot -> expiry weekday
SIGMA = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
PER_SESSION = 64  # EKJS grid; E[I_m^2] sits within a/m of the floor, measured below
N_PATHS = 50_000  # inner paths per draw (25,000 antithetic pairs); module docstring
CHUNK = 25_000  # paths per simulate call, divides N_PATHS: 25k x 321 x 8 bytes = 64 MB
SEED = 43
P_LO, P_HI = 2.5, 97.5


def week_order(expiry: str) -> list[str]:
    """The five sessions of one weekly cycle, ending on ``expiry``."""
    k = WEEK.index(expiry)
    return [WEEK[(k + 1 + j) % 5] for j in range(5)]


def products(expiry: str) -> dict[str, tuple[list[str], list[str]]]:
    """name -> (long-leg days, short-leg days). Straddles opened 1..5 sessions before expiry, and
    the near/far calendar spread opened the session before expiry: long the straddle that expires
    a week later, short the one that expires next session."""
    order = week_order(expiry)
    out = {f"open_{order[4 - n]}": (order[-n:], []) for n in range(1, 6)}
    out[f"spread_{order[3]}"] = ([expiry] + order, [expiry])
    return out


def _price(total_weight: np.ndarray) -> np.ndarray:
    """ATM straddle on ``total_weight`` average sessions of variance; zero sessions price zero."""
    w = np.asarray(total_weight, dtype=float)
    return np.where(w > 0, straddle_price(SPOT, SPOT, SIGMA**2 * np.maximum(w, 1e-300)), 0.0)


def entry_errors(u: np.ndarray, expiry: str) -> dict[str, np.ndarray]:
    """Calendar-time trader's relative price error per row of ``u`` (rows: draws, columns: WEEK,
    five-day mean one). Exact Black-76 per row: the inner loop is analytic."""
    col = {d: j for j, d in enumerate(WEEK)}
    out = {}
    for name, (long, short) in products(expiry).items():
        clock = _price(u[:, [col[d] for d in long]].sum(1)) - _price(
            u[:, [col[d] for d in short]].sum(1) if short else np.zeros(len(u)))
        flat = _price(np.array(len(long))) - _price(np.array(len(short)))
        out[name] = clock / flat - 1.0
    return out


def naive_band(u: np.ndarray, expiry: str) -> dict[str, tuple[float, float]]:
    """The band read off the marginal intervals: every weight at its own 2.5% point together, then
    every weight at its 97.5% point. Exact for a one-weight product, wrong otherwise."""
    corners = np.percentile(u, [P_LO, P_HI], axis=0)
    errors = entry_errors(corners, expiry)
    return {k: (float(v.min()), float(v.max())) for k, v in errors.items()}


def _pairs(x: np.ndarray) -> np.ndarray:
    """``simulate`` mirrors its second half on its first (D-14): pair averages are iid."""
    half = len(x) // 2
    return 0.5 * (x[:half] + x[half:])


def hedging(weights: np.ndarray, n_paths: int = N_PATHS, seed: int = SEED) -> dict[str, float]:
    """Hedging clock risk on one clock (five-day mean one, opened after the previous expiry).

    Returns, for ``diff`` (calendar minus clock at one hedge per session), ``clock`` (the correct-
    clock hedger at one hedge per session) and ``ekjs`` (``I_64``): the mean, the sd as
    ``sqrt(E[X^2])``, and the MC variance of each. Then ``ekjs_exact`` and ``floor``, analytic.
    """
    truth, flat = believed_paths(weights, PER_SESSION)
    total = SIGMA**2 * weights.sum()
    entry = float(straddle_price(SPOT, SPOT, total))
    clock_rem = SIGMA**2 * weights[::-1].cumsum()[::-1]
    calendar_rem = total * (5 - np.arange(5)) / 5

    samples: dict[str, list[np.ndarray]] = {"diff": [], "clock": [], "ekjs": []}
    for k in range(n_paths // CHUNK):
        paths = simulate(truth, SIGMA, SPOT, CHUNK, np.random.default_rng([seed, k]))
        daily = paths[:, ::PER_SESSION]
        clock = hedge_short_straddle(daily, SPOT, clock_rem, entry, 0.0)
        calendar = hedge_short_straddle(daily, SPOT, calendar_rem, entry, 0.0)
        samples["diff"].append(calendar - clock)
        samples["clock"].append(clock)
        samples["ekjs"].append(ekjs_pathwise(paths, SPOT, SIGMA**2 * truth, SIGMA**2 * flat))

    out: dict[str, float] = {}
    for name, parts in samples.items():
        mean_pairs = np.concatenate([_pairs(x) for x in parts])
        sq_pairs = np.concatenate([_pairs(x**2) for x in parts])
        n = sq_pairs.size
        second = float(sq_pairs.mean())
        out[f"{name}_mean"] = float(mean_pairs.mean())
        out[f"{name}_mean_mcvar"] = float(mean_pairs.var(ddof=1) / n)
        out[f"{name}_sd"] = np.sqrt(second)
        out[f"{name}_sd_mcvar"] = float(sq_pairs.var(ddof=1) / n / (4.0 * second))
    out["share"] = out["diff_sd"] / out["clock_sd"]
    out["ekjs_exact"] = float(np.sqrt(
        continuous_hedge.riemann_second_moment(weights, SIGMA, SPOT, PER_SESSION)))
    out["floor"] = float(np.sqrt(continuous_hedge.floor_second_moment(weights, SIGMA, SPOT)))
    return out


def load(cfg: Settings, label: str) -> tuple[np.ndarray, np.ndarray]:
    """Point weights (5,) and draws (n, 5) for one NIFTY regime, each rescaled to five-day mean
    one, columns in ``WEEK`` order."""
    keep = (pl.col("symbol") == "NIFTY") & (pl.col("label") == label) & (
        pl.col("half") == "full") & (pl.col("spec") == "weekday")
    fits = pl.read_parquet(cfg.tables_dir / "clock_fits.parquet").filter(keep)
    point = np.array([fits.filter(pl.col("parameter") == d)["point"].item() for d in WEEK])
    draws = (pl.read_parquet(cfg.tables_dir / "clock_boot.parquet").filter(keep)
             .sort("draw").select(WEEK).to_numpy())
    return point / point.mean(), draws / draws.mean(axis=1, keepdims=True)


def regime_tables(
    label: str, point: np.ndarray, draws: np.ndarray, n_paths: int = N_PATHS, seed: int = SEED
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Per-draw values (``draw`` null for the point fit) and the summary across draws."""
    expiry = REGIMES[label]
    order = [WEEK.index(d) for d in week_order(expiry)]
    errors = entry_errors(np.vstack([point, draws]), expiry)
    hedges = [hedging(w[order], n_paths, seed) for w in np.vstack([point, draws])]

    rows = []
    for i in range(len(draws) + 1):
        values = {k: v[i] for k, v in errors.items()}
        values |= {k: v for k, v in hedges[i].items() if not k.endswith("mcvar")}
        for quantity, value in values.items():
            rows.append({"regime": label, "draw": i - 1 if i else None, "quantity": quantity,
                         "value": float(value), "mc_var": hedges[i].get(f"{quantity}_mcvar", 0.0)})
    per_draw = pl.DataFrame(rows, schema_overrides={"draw": pl.Int64})
    return per_draw, summarise(per_draw, naive_band(draws, expiry))


def summarise(per_draw: pl.DataFrame, naive: dict[str, tuple[float, float]]) -> pl.DataFrame:
    """Across draws, per quantity: point, median, 95% band, P(< 0), the naive band where one
    exists, ``var_draws`` (outer variance of the per-draw estimate), ``mc_var`` (its mean inner MC
    variance: the Gordy-Juneja inflation bound) and, for an sd, the predictive sd
    ``sqrt(E_w[sd^2] + Var_w(mean))`` with the between-draw mean term debiased by its MC variance."""
    rows = []
    for (label, quantity), g in per_draw.group_by(["regime", "quantity"], maintain_order=True):
        point = g.filter(pl.col("draw").is_null())["value"].item()
        d = g.filter(pl.col("draw").is_not_null())
        x = d["value"].to_numpy()
        lo, hi = naive.get(quantity, (None, None))
        predictive = None
        if quantity.endswith("_sd"):
            mean = per_draw.filter((pl.col("regime") == label) & pl.col("draw").is_not_null()
                                   & (pl.col("quantity") == quantity.replace("_sd", "_mean")))
            between = max(float(mean["value"].var()) - float(mean["mc_var"].mean()), 0.0)
            predictive = float(np.sqrt(np.mean(x**2) + between))
        rows.append({
            "regime": label, "quantity": quantity, "n_draws": len(x), "point": point,
            "median": float(np.median(x)), "lo": float(np.percentile(x, P_LO)),
            "hi": float(np.percentile(x, P_HI)), "p_negative": float(np.mean(x < 0)),
            "naive_lo": lo, "naive_hi": hi, "var_draws": float(np.var(x, ddof=1)),
            "mc_var": float(d["mc_var"].mean()), "predictive_sd": predictive,
        })
    return pl.DataFrame(rows, schema_overrides={"naive_lo": pl.Float64, "naive_hi": pl.Float64,
                                                "predictive_sd": pl.Float64})


def run(cfg: Settings = settings) -> tuple[pl.DataFrame, pl.DataFrame]:
    parts = [regime_tables(label, *load(cfg, label)) for label in REGIMES]
    return pl.concat([p for p, _ in parts]), pl.concat([s for _, s in parts])


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    per_draw, summary = run(cfg)
    inputs = tuple(cfg.tables_dir / f"{n}.parquet" for n in ("clock_boot", "clock_fits"))
    extra = {"n_paths": N_PATHS, "per_session": PER_SESSION, "annual_vol": ANNUAL_VOL,
             "spot": SPOT, "regimes": REGIMES, "normalisation": "five-day mean one per draw"}
    for name, table in (("clock_uncertainty", per_draw), ("clock_uncertainty_summary", summary)):
        path = cfg.tables_dir / f"{name}.parquet"
        table.write_parquet(path)
        write_manifest(path, manifest(f"clock uncertainty: {name}", cfg, seed=SEED,
                                      inputs=inputs, extra=extra))
        logging.info("wrote %s (%d rows)", path, table.height)
    logging.info("%-10s %-14s %9s %9s %9s %9s %8s %9s %9s %9s", "regime", "quantity", "point",
                 "median", "lo", "hi", "P(<0)", "naive_lo", "naive_hi", "pred_sd")
    for r in summary.iter_rows(named=True):
        fmt = lambda v: f"{v:9.4f}" if v is not None else f"{'':9}"  # noqa: E731
        logging.info("%-10s %-14s %s %s %s %s %8.3f %s %s %s", r["regime"], r["quantity"],
                     fmt(r["point"]), fmt(r["median"]), fmt(r["lo"]), fmt(r["hi"]),
                     r["p_negative"], fmt(r["naive_lo"]), fmt(r["naive_hi"]),
                     fmt(r["predictive_sd"]))


if __name__ == "__main__":
    main()

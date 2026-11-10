"""Securities transaction tax on index options, and whether it touches the entry-price error (D-17).

D-17 left STT out of v1 on the grounds that it is large and interacts with expiry-day exercise. The
statute settles most of that. Rates and dates are in ``data/reference/stt_rates.json``, each read
from a primary document. Two legs matter for an option held to expiry:

* sale of an option: rate ``p`` on the premium, paid by the seller at entry;
* exercise: rate ``e`` on the intrinsic price (settlement minus strike), paid by the purchaser.

Zero rates (as in src.sim.engine), forward F, strike K, frictionless Black-76 price C. A buyer who
holds to exercise receives ``(1 - e) * intrinsic``, so pays at most ``(1 - e) * C``; one who sells
on expiry day instead pays ``p`` on the sale, so ``(1 - p) * C`` less the spread. A writer keeps
``(1 - p)`` of the premium and pays intrinsic untaxed, so accepts at least ``C / (1 - p)``. Every
one of these prices is C times a constant, whatever the variance. The
calendar-vs-clock error is a ratio of two such prices, so the constant cancels exactly: STT does not
move the -21.8%, not even approximately. Only the rupee size of the error scales, by under 0.2%.
A window that straddles a rate change (2024-10-01, 2026-04-01) still cancels: the seller pays the
entry-date ``p``, the purchaser the expiry-date ``e``, and each price carries only one of them.

What would not cancel is a tax that is not proportional to the payoff. Until 2019-09-01 exercise was
taxed on the settlement price, ``e * S_T`` (memo2019 in the rate file). Then an ATM straddle always
pays about ``e * F`` whatever happens (exactly one leg ends in the money), and the price ratio
becomes ``(C_clock - eF) / (C_flat - eF)``. That base predates the sample, so it appears only as a
labelled counterfactual (``cf_`` columns), to measure how much the proportional base matters.

On the intrinsic base the trap does not exist: exercise nets ``(1 - e) * I >= 0`` for every
settlement, so no barely-in-the-money option costs more to exercise than it pays. On the notional
base the losing band is ``K / (1 + e) < S_T < K / (1 - e)``, about ``2 e K`` wide.

Run:  uv run python -m src.experiment.stt
"""

from __future__ import annotations

import json
import logging

import numpy as np
import polars as pl
from scipy.stats import norm

from src.config import Settings, settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPANS, SPOT, TUE_REGIME
from src.iv.invert import black76_undiscounted
from src.provenance import manifest, write_manifest
from src.sim.engine import straddle_price

log = logging.getLogger(__name__)

OPENED = ("Mon", "Fri", "Thu", "Wed", "Tue")  # one to five sessions to the Tuesday expiry


def load_periods(cfg: Settings = settings) -> list[dict]:
    path = cfg.reference_dir / "stt_rates.json"
    return json.loads(path.read_text(encoding="utf-8"))["periods"]


def notional_base_value(fwd: float, strike: float, total_var: float, e: float) -> float:
    """Long ATM straddle under the counterfactual notional base, held by a holder who exercises a
    leg only when its intrinsic beats ``e * S_T``.

    Call leg: exercise iff ``S - K > e S``, payoff ``(1 - e)(S - K / (1 - e))``. Put leg likewise
    with ``1 + e``. So the value is two Black-76 prices at shifted strikes.
    """
    sigma = np.sqrt(total_var)
    call = black76_undiscounted(sigma, fwd, strike / (1 - e), 1.0, True)
    put = black76_undiscounted(sigma, fwd, strike / (1 + e), 1.0, False)
    return float((1 - e) * call + (1 + e) * put)


def trap_probability(fwd: float, strike: float, total_var: float, e: float) -> float:
    """P(K/(1+e) < S_T < K/(1-e)) for lognormal S_T: the settlements where, on a notional base,
    the in-the-money leg pays less than the tax on exercising it."""
    sd = np.sqrt(total_var)

    def cdf(level: float) -> float:
        return float(norm.cdf((np.log(level / fwd) + 0.5 * total_var) / sd))

    return cdf(strike / (1 - e)) - cdf(strike / (1 + e))


def _prices(total_var: float, p: float, e: float) -> dict[str, float]:
    c = straddle_price(SPOT, SPOT, total_var)
    return {
        "frictionless": c,
        "long": (1 - e) * c,
        "short": c / (1 - p),
        "cf_forced": c - e * SPOT,  # notional base, every in-the-money leg exercised
        "cf_optimal": notional_base_value(SPOT, SPOT, total_var, e),
    }


def run(cfg: Settings = settings, weights: dict[str, float] = TUE_REGIME) -> pl.DataFrame:
    sigma2 = ANNUAL_VOL**2 / SESSIONS_PER_YEAR
    mean_w = float(np.mean(list(weights.values())))
    rows = []
    for period in load_periods(cfg):
        p = period["option_sale_pct"] / 100
        e = period["option_exercised_pct"] / 100
        for day in OPENED:
            span = SPANS[day]
            var_clock = sigma2 * sum(weights[d] for d in span) / mean_w
            clock = _prices(var_clock, p, e)
            flat = _prices(sigma2 * len(span), p, e)
            err = {k: clock[k] / flat[k] - 1 for k in clock}
            rows.append(
                {
                    "period_from": period["from"],
                    "period_to": period["to"],
                    "option_sale_pct": period["option_sale_pct"],
                    "option_exercised_pct": period["option_exercised_pct"],
                    "opened": day,
                    "sessions": len(span),
                    "price_clock": clock["frictionless"],
                    "price_flat": flat["frictionless"],
                    "long_max_price_clock": clock["long"],
                    "short_min_price_clock": clock["short"],
                    "exercise_stt_pts": e * clock["frictionless"],
                    "premium_stt_pts": p * clock["short"],
                    "band_pct_of_price": (clock["short"] - clock["long"]) / clock["frictionless"],
                    "sell_not_exercise_saving_pts": (e - p) * clock["frictionless"],
                    "err_frictionless": err["frictionless"],
                    "err_long": err["long"],
                    "err_short": err["short"],
                    "err_shift_max": max(
                        abs(err["long"] - err["frictionless"]),
                        abs(err["short"] - err["frictionless"]),
                    ),
                    "cf_long_forced_clock": clock["cf_forced"],
                    "cf_err_forced": err["cf_forced"],
                    "cf_long_optimal_clock": clock["cf_optimal"],
                    "cf_err_optimal": err["cf_optimal"],
                    "cf_trap_width_pts": SPOT / (1 - e) - SPOT / (1 + e),
                    "cf_trap_prob_clock": trap_probability(SPOT, SPOT, var_clock, e),
                }
            )
    return pl.DataFrame(rows)


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    table = run(cfg)
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.tables_dir / "stt_effect.parquet"
    table.write_parquet(path)
    rates = cfg.reference_dir / "stt_rates.json"
    write_manifest(
        path,
        manifest(
            "STT effect on the entry-price error",
            cfg,
            inputs=(rates,),
            extra={"clock": TUE_REGIME, "spot": SPOT, "annual_vol": ANNUAL_VOL},
        ),
    )
    log.info("wrote %s (%d rows)", path, table.height)
    log.info(
        "%-11s %-4s %4s %8s %9s %9s %10s %9s %9s %8s",
        "from", "open", "sess", "price", "err", "shift", "exer stt", "cf forced", "cf optim", "cf trap",
    )
    for r in table.iter_rows(named=True):
        log.info(
            "%-11s %-4s %4d %8.2f %8.2f%% %9.1e %10.3f %8.2f%% %8.2f%% %7.1f%%",
            r["period_from"], r["opened"], r["sessions"], r["price_clock"],
            100 * r["err_frictionless"], r["err_shift_max"], r["exercise_stt_pts"],
            100 * r["cf_err_forced"], 100 * r["cf_err_optimal"], 100 * r["cf_trap_prob_clock"],
        )


if __name__ == "__main__":
    main()

"""Estimate the variance clock from the options channel (D-07, D-08).

    log w(d) = a[weekday] + e*1[weekend] + b*1[expiry day] + c*1[macro event]
    V(t,T)   = s(t) * sum_{d in (t,T]} w(d)

One nuisance scale s(t) per (date, symbol). In logs that is an additive group effect, so it is
profiled out by within-group demeaning instead of carried as thousands of free parameters. Seven
parameters remain.

Identification comes from variation across dates in which weekdays remain before expiry, not from
any single date -- one date gives 3-6 maturities against 7 parameters. Gate A3 checks the pooling
actually produced full rank.

The level is unidentified (scale w up, s down, nothing changes). Monday is pinned during the fit,
and the vector is rescaled afterwards to average one (D-01).

Run:  uv run python -m src.clock.estimate
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl
from scipy import sparse
from scipy.optimize import least_squares

from src.config import Settings, settings

log = logging.getLogger(__name__)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
#: Monday is the reference level, dropped from the free parameters (see module docstring).
PARAM_NAMES = ("Tue", "Wed", "Thu", "Fri", "weekend", "expiry_day", "macro_event")
#: A parameter needs at least this many sessions carrying it before the window can identify it.
MIN_ACTIVE_SESSIONS = 3


@dataclass
class ClockFit:
    params: dict[str, float]
    weights: dict[str, float]
    combined: dict[str, float]
    dropped: list[str]
    n_obs: int
    n_groups: int
    rmse: float
    condition_number: float
    vif: dict[str, float]
    boot: dict[str, tuple[float, float]] = field(default_factory=dict)


@functools.lru_cache(maxsize=8)
def _expiry_days(panel_dir: str, symbol: str) -> frozenset[dt.date]:
    """Every option expiry date for ``symbol``. Cached: it scans 1.85M panel rows, and the
    permutation test (D-11) calls ``fit`` a few hundred times."""
    expiries = (
        pl.concat(
            [pl.scan_parquet(f) for f in sorted(Path(panel_dir).glob("fo_*.parquet"))],
            how="vertical_relaxed",
        )
        .filter((pl.col("symbol") == symbol) & (pl.col("instr") == "IDO"))
        .select(pl.col("expiry").unique())
        .collect()
    )
    return frozenset(expiries["expiry"].to_list())


def _day_features(
    cfg: Settings, symbol: str, param_names: tuple[str, ...] = PARAM_NAMES
) -> tuple[list[dt.date], np.ndarray]:
    """Design matrix over every session in the sample, one row per session."""
    cal = json.loads((cfg.raw_dir / "calendar.json").read_text(encoding="utf-8"))
    sessions = [dt.date.fromisoformat(d) for d in cal["trading_days"]]

    expiry_days = _expiry_days(str(cfg.panel_dir), symbol)

    macro = json.loads((cfg.reference_dir / "macro_events.json").read_text(encoding="utf-8"))
    event_days = {dt.date.fromisoformat(d) for d in macro["union_budget"] + macro["rbi_mpc"]}

    design = np.zeros((len(sessions), len(param_names)))

    def mark(row: int, name: str) -> None:
        if name in param_names:
            design[row, param_names.index(name)] = 1.0

    for i, day in enumerate(sessions):
        wd = day.weekday()
        if wd >= 5:
            # D-02c: a weekend session cannot carry a weekday weight (four Saturdays and one
            # Sunday in the whole sample), but it must stay in the day sequence.
            mark(i, "weekend")
        elif wd > 0:  # Monday is the pinned reference
            mark(i, WEEKDAYS[wd])
        if day in expiry_days:
            mark(i, "expiry_day")
        if day in event_days:
            mark(i, "macro_event")
    return sessions, design


def _observations(
    cfg: Settings, symbol: str, sessions: list[dt.date], variance_col: str, window: tuple[dt.date, dt.date] | None
) -> tuple[np.ndarray, sparse.csr_matrix, np.ndarray]:
    """Return log total variance, the (obs x session) window indicator, and a group id per obs."""
    idx = {d: i for i, d in enumerate(sessions)}
    atm = pl.read_parquet(cfg.tables_dir / "atm_variance.parquet").filter(
        (pl.col("symbol") == symbol)
        & pl.col("tenor").is_in(["weekly", "monthly"])
        & (pl.col("dte_trd") >= 2)
        & pl.col(variance_col).is_not_null()
    )
    if window is not None:
        # The whole variance span (t, T] must sit inside the window, not just the quote date. A
        # pre-regime quote on 2025-08-27 expiring Tuesday 2025-09-02 sums weights from both
        # regimes, which pulls the two regime estimates toward each other.
        atm = atm.filter(
            (pl.col("trade_date") >= window[0]) & (pl.col("expiry") <= window[1])
        )

    rows, cols, y, groups = [], [], [], []
    group_ids: dict[dt.date, int] = {}
    for r, rec in enumerate(atm.iter_rows(named=True)):
        t, T = rec["trade_date"], rec["expiry"]
        if t not in idx or T not in idx:
            continue
        # Days in (t, T]: the trade date itself is already elapsed, the expiry session is not.
        span = range(idx[t] + 1, idx[T] + 1)
        rows.extend([len(y)] * len(span))
        cols.extend(span)
        y.append(np.log(rec[variance_col]))
        groups.append(group_ids.setdefault(t, len(group_ids)))

    window_matrix = sparse.csr_matrix(
        (np.ones(len(rows)), (rows, cols)), shape=(len(y), len(sessions))
    )
    return np.asarray(y), window_matrix, np.asarray(groups)


def _demean(values: np.ndarray, groups: np.ndarray, n_groups: int) -> np.ndarray:
    """Subtract the per-group mean. This is what profiles out the nuisance scale s(t)."""
    sums = np.bincount(groups, weights=values, minlength=n_groups)
    counts = np.bincount(groups, minlength=n_groups)
    return values - (sums / np.maximum(counts, 1))[groups]


def _normalise(
    theta: np.ndarray, names: tuple[str, ...], design: np.ndarray, touched: np.ndarray
) -> dict[str, float]:
    """Weekday levels rescaled so the fitted weights average one over the window (D-01).

    Reporting raw ``exp(theta)`` would leave every number relative to the pinned Monday, which is
    misleading here: a Monday session carries the whole weekend's information, so Monday is
    genuinely a high-variance session. Against a Monday baseline every other weekday looks
    suppressed, and "the expiry weekday is low" becomes partly a restatement of the weekend effect.
    Mean-one normalisation separates the two.

    Weekday entries are levels. ``expiry_day``, ``weekend`` and ``macro_event`` stay as
    multipliers applied on top of a weekday, so they are not rescaled.
    """
    scale = float(np.exp(design[touched] @ theta).mean())
    lookup = {name: theta[k] for k, name in enumerate(names)}
    out = {wd: float(np.exp(lookup.get(wd, 0.0)) / scale) for wd in WEEKDAYS}
    for name in ("weekend", "expiry_day", "macro_event"):
        if name in lookup:
            out[name] = float(np.exp(lookup[name]))
    return out


def fit(
    cfg: Settings = settings,
    symbol: str = "NIFTY",
    variance_col: str = "total_var_corr",
    window: tuple[dt.date, dt.date] | None = None,
    n_boot: int = 0,
    seed: int = 0,
    dummies: tuple[str, ...] = ("weekend", "expiry_day", "macro_event"),
) -> ClockFit:
    """Fit the clock. ``dummies`` selects which non-weekday effects are estimated.

    Within a single regime the expiry day is nearly a deterministic function of weekday -- 83 of 87
    pre-Sep-2025 expiries fall on a Thursday -- so ``expiry_day`` and ``a[Thu]`` are not separately
    identified and the split between them is arbitrary. Pass ``dummies=("weekend",)`` for a
    within-regime fit and let the expiry weekday's own weight carry the effect. The expiry dummy is
    identifiable only across regimes, where the rule change supplies the variation (D-08b).
    """
    param_names = tuple(n for n in PARAM_NAMES if n in WEEKDAYS or n in dummies)
    sessions, full_design = _day_features(cfg, symbol, param_names)
    y, window_matrix, groups = _observations(cfg, symbol, sessions, variance_col, window)
    n_groups = int(groups.max()) + 1 if len(groups) else 0
    if len(y) < 50:
        raise ValueError(f"{symbol}: only {len(y)} observations; refusing to fit")
    y_dm = _demean(y, groups, n_groups)

    # Drop parameters this window cannot identify, and say which.
    #
    # Not defensive plumbing: on the Tuesday regime the only weekend session in the window is Sunday
    # 2026-02-01, which is also the only Union Budget session, so the weekend and macro-event
    # columns are the same column. Fitting anyway returned a condition number of 4.2e15 and a
    # macro-event weight of 8817. A rank check is cheaper than discovering that downstream.
    touched = np.asarray(window_matrix.sum(axis=0)).ravel() > 0
    active = full_design[touched]
    kept = [k for k in range(full_design.shape[1]) if active[:, k].sum() >= MIN_ACTIVE_SESSIONS]
    while len(kept) > 1 and np.linalg.matrix_rank(active[:, kept]) < len(kept):
        # Remove the column that is most nearly a combination of the others.
        gram = np.linalg.pinv(active[:, kept].T @ active[:, kept])
        worst = int(np.argmax(np.diag(gram)))
        kept.pop(worst)
    dropped = [param_names[k] for k in range(full_design.shape[1]) if k not in kept]
    design = full_design[:, kept]
    names = tuple(param_names[k] for k in kept)

    def residual(theta: np.ndarray) -> np.ndarray:
        weights = np.exp(design @ theta)
        g = np.log(window_matrix @ weights)
        return y_dm - _demean(g, groups, n_groups)

    def jacobian(theta: np.ndarray) -> np.ndarray:
        weights = np.exp(design @ theta)
        totals = window_matrix @ weights
        cols = [
            _demean((window_matrix @ (weights * design[:, k])) / totals, groups, n_groups)
            for k in range(design.shape[1])
        ]
        return -np.column_stack(cols)

    result = least_squares(residual, np.zeros(design.shape[1]), jac=jacobian, method="lm")
    theta = result.x

    jac = jacobian(theta)
    singular = np.linalg.svd(jac, compute_uv=False)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
    gram_inv = np.linalg.pinv(jac.T @ jac)
    # VIF: how much collinearity inflates each parameter's variance relative to an orthogonal design.
    vif = {name: float(gram_inv[k, k] * (jac[:, k] @ jac[:, k])) for k, name in enumerate(names)}

    weights = _normalise(theta, names, design, touched)
    # The weekday and expiry-day columns are near-collinear by construction: pre-Sep-2025 every
    # Thursday but four was an expiry. Splitting the effect between them is arbitrary; the product
    # is the economically meaningful number and is what gets reported.
    lookup = {name: theta[k] for k, name in enumerate(names)}
    combined = {
        f"{wd}{'+expiry' if exp else ''}": float(
            np.exp(lookup.get(wd, 0.0) + (lookup.get("expiry_day", 0.0) if exp else 0.0))
        )
        for wd in WEEKDAYS
        for exp in (False, True)
    }

    boot: dict[str, tuple[float, float]] = {}
    if n_boot:
        rng = np.random.default_rng(seed)
        draws = []
        for _ in range(n_boot):
            # Resample whole dates, not individual quotes: maturities sharing a date share the
            # nuisance scale and their errors are not independent.
            pick = rng.integers(0, n_groups, n_groups)
            mask = np.concatenate([np.flatnonzero(groups == g) for g in pick])
            sub_groups = np.concatenate(
                [np.full((groups == g).sum(), i) for i, g in enumerate(pick)]
            )
            sub_y = _demean(y[mask], sub_groups, len(pick))
            sub_w = window_matrix[mask]

            def boot_resid(th, sub_w=sub_w, sub_y=sub_y, sub_groups=sub_groups, k=len(pick)):
                g = np.log(sub_w @ np.exp(design @ th))
                return sub_y - _demean(g, sub_groups, k)

            try:
                draws.append(least_squares(boot_resid, theta, method="lm").x)
            except Exception:  # noqa: BLE001 - a failed draw is dropped, not silently zeroed
                continue
        if draws:
            normed = [_normalise(th, names, design, touched) for th in draws]
            for name in normed[0]:
                vals = np.array([d[name] for d in normed])
                boot[name] = (
                    float(np.percentile(vals, 2.5)),
                    float(np.percentile(vals, 97.5)),
                )

    return ClockFit(
        params={n: float(theta[k]) for k, n in enumerate(names)},
        weights=weights,
        combined=combined,
        dropped=dropped,
        n_obs=len(y),
        n_groups=n_groups,
        rmse=float(np.sqrt(np.mean(result.fun**2))),
        condition_number=condition,
        vif=vif,
        boot=boot,
    )


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for column in ("total_var_corr", "total_var_raw"):
        result = fit(cfg, symbol="NIFTY", variance_col=column, n_boot=200)
        log.info("=== NIFTY, %s ===", column)
        log.info(
            "n_obs=%d  n_dates=%d  rmse=%.4f  cond=%.1f",
            result.n_obs, result.n_groups, result.rmse, result.condition_number,
        )
        log.info("Mon = 1.000 (reference)")
        for name in PARAM_NAMES:
            ci = result.boot.get(name)
            span = f"  95%% CI [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
            log.info("%-12s %.3f  VIF=%5.1f%s", name, result.weights[name], result.vif[name], span)


if __name__ == "__main__":
    main()

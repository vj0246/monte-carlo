"""Bank Nifty against Nifty across the two expiry events: a dose-response test (D-04, D-09, D-10).

Nov-2024 removed the Bank Nifty weekly and left Nifty's, so there Bank Nifty is treated and Nifty is
the control. Sep-2025 moved both, Bank Nifty at a lower dose (one monthly a month against a weekly).
Roles belong to the event, not the symbol, so ``roles`` reads them from the data: a unit is treated
when the modal weekday of its own expiry sessions changes between the windows.

Pre-registered estimands (weights mean-one, D-01):

    Nov-2024  DiD = d ln w_BN(d*) - d ln w_N(d*),   d* = the treated unit's pre-event expiry weekday
    Sep-2025  S_u = d ln w_new - d ln w_old per unit, and the dose ratio S_low / S_high

The same weekday in both units makes the first a difference in differences: a shock common to d*
(other NSE weeklies removed the same day, calendar drift) cancels.

Returns channel (D-09): close-to-close index returns over an EWMA variance forecast, so a drifting
level is not read as a weekday pattern. Budget and RBI sessions are left out of the weekday means
(D-07 by exclusion); weekend sessions stay in the return chain, out of the weekday vector (D-02c).
The bootstrap resamples whole trading weeks, the same weeks for both indices, because both share
each session's shock (D-08b).

Options channel: ``src.clock.estimate.fit`` per unit and window, read only if it passes the D-11
health rule. ``fit`` returns per-weight intervals, not draws, so the options statistics are points.
Randomization inference reuses the D-11 pseudo dates, returns channel only. At h = 63 only three
pseudo designs fit without sharing a session, so its p-values are coarse.

Run:  uv run python -m src.analysis.dose_response
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter

import numpy as np
import polars as pl

from src.analysis.permutation import MAX_ABS_LOGW, MAX_COND, _eligible, _sessions
from src.clock.estimate import WEEKDAYS, fit
from src.config import Settings, settings
from src.provenance import manifest, write_manifest

log = logging.getLogger(__name__)

INDEX = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank"}
EWMA_LAMBDA = 0.94  # RiskMetrics daily; half-life about 11 sessions
EWMA_LAMBDA_SLOW = 0.97  # sensitivity only; half-life about 23 sessions
N_BOOT = 2000
OPTIONS_BOOT = 300
SEED = 0
H_RI = 63
BLACKOUT = (dt.date(2024, 11, 13), dt.date(2025, 1, 31))  # D-10, the measured lot overlap

D = dt.date
# (event, variant, pre, post). Nov-2024 pre starts after the 2024-04-26 Nifty lot change. Lot
# changes inside the windows (measured from the panel in tests/test_dose_response.py): Bank Nifty
# 2025-04-25 and 2025-06-27 (Nov-2024 post, and also the Sep-2025 pre-window, left uncleaned);
# Nifty and Bank Nifty 2025-10-29 (Sep-2025 post). The clean variants cut the post-windows (D-10).
WINDOWS = (
    ("nov2024", "full", (D(2024, 5, 13), D(2024, 11, 12)), (D(2025, 2, 1), D(2025, 7, 31))),
    ("nov2024", "pre_lot_change", (D(2024, 5, 13), D(2024, 11, 12)), (D(2025, 2, 1), D(2025, 4, 24))),
    ("sep2025", "full", (D(2025, 3, 2), D(2025, 8, 28)), (D(2025, 9, 2), D(2026, 2, 28))),
    ("sep2025", "pre_lot_change", (D(2025, 3, 2), D(2025, 8, 28)), (D(2025, 9, 2), D(2025, 10, 28))),
)


def own_expiries(cfg: Settings, symbol: str) -> list[dt.date]:
    """Weekly and monthly expiries whose series is quoted on its own expiry session. A contract
    re-dated by a schedule change keeps its old expiry in the panel without ever trading on it
    (BANKNIFTY 2025-01-29, last quoted 2025-01-01); that is not an expiry session."""
    panel = pl.concat(
        [pl.scan_parquet(f) for f in sorted(cfg.panel_dir.glob("fo_*.parquet"))], how="vertical_relaxed"
    )
    hit = panel.filter(
        (pl.col("symbol") == symbol)
        & (pl.col("instr") == "IDO")
        & pl.col("tenor").is_in(["weekly", "monthly"])
        & (pl.col("trade_date") == pl.col("expiry"))
    )
    return sorted(hit.select(pl.col("expiry").unique()).collect()["expiry"].to_list())


def _inside(day: dt.date, window: tuple) -> bool:
    return window[0] <= day <= window[1]


def roles(expiries: dict[str, list[dt.date]], sessions: list[dt.date], pre: tuple, post: tuple) -> dict:
    """Treated or control per unit, from the modal weekday of its expiry sessions in each window.
    ``dose`` is the share of that weekday's sessions that are own-expiry sessions: 1 for a weekly,
    about 0.2 for a monthly."""
    out = {}
    for symbol, days in expiries.items():
        modal = [Counter(d.weekday() for d in days if _inside(d, w)).most_common(1)[0][0] for w in (pre, post)]

        def dose(window: tuple, weekday: int, days: set = set(days)) -> float:
            pool = [s for s in sessions if _inside(s, window) and s.weekday() == weekday]
            return sum(s in days for s in pool) / len(pool)

        out[symbol] = {
            "role": "treated" if modal[0] != modal[1] else "control",
            "old": WEEKDAYS[modal[0]], "new": WEEKDAYS[modal[1]],
            "dose_pre": dose(pre, modal[0]), "dose_post": dose(post, modal[1]),
        }
    return out


# --- returns channel ---------------------------------------------------------------------------


def ewma_forecast(r: np.ndarray, lam: float = EWMA_LAMBDA) -> np.ndarray:
    """sigma^2_t from returns up to t-1 only. Seeded with the first 20 squares; every window used
    here starts after session 63, where the seed's weight is 0.94^63 = 2%, except the first D-11
    pseudo windows. Measured: a 10x seed moves only the first three pseudo DiDs (by up to 0.37);
    the large early ones (+0.96 to +1.20) are the same under any seed and under raw r^2."""
    f = np.empty_like(r)
    f[0] = np.mean(r[:20] ** 2)
    for t in range(1, len(r)):
        f[t] = lam * f[t - 1] + (1 - lam) * r[t - 1] ** 2
    return f


def standardised_squares(cfg: Settings, lam: float = EWMA_LAMBDA) -> tuple[list[dt.date], np.ndarray]:
    """z^2 = r^2 / EWMA forecast, one row per unit in INDEX order, on the trading-session chain."""
    ix = pl.read_parquet(cfg.panel_dir / "index_daily.parquet")
    dates, rows = None, []
    for name in INDEX.values():
        sub = ix.filter(pl.col("index_name") == name).sort("session_idx")
        if dates is not None and sub["trade_date"].to_list()[1:] != dates:
            raise ValueError(f"{name} is not on the same session sequence as the other indices")
        dates = sub["trade_date"].to_list()[1:]
        r = np.diff(np.log(sub["close"].to_numpy()))
        rows.append(r**2 / ewma_forecast(r, lam))
    return dates, np.vstack(rows)


def week_sums(z2: np.ndarray, dates: list[dt.date], window: tuple, exclude: set) -> tuple:
    """Sums of z^2 per (unit, trading week, weekday) and session counts per (week, weekday). The
    week is the bootstrap block: it keeps each draw's weekday mix and carries short-range
    dependence with it."""
    keep = [i for i, d in enumerate(dates) if _inside(d, window) and d.weekday() < 5 and d not in exclude]
    ids: dict[tuple, int] = {}
    week = np.array([ids.setdefault(dates[i].isocalendar()[:2], len(ids)) for i in keep])
    weekday = np.array([dates[i].weekday() for i in keep])
    sums = np.zeros((z2.shape[0], len(ids), 5))
    for u in range(z2.shape[0]):
        np.add.at(sums[u], (week, weekday), z2[u, keep])
    counts = np.zeros((len(ids), 5))
    np.add.at(counts, (week, weekday), 1.0)
    return sums, counts


def log_weights(sums: np.ndarray, counts: np.ndarray, mult: np.ndarray | None = None) -> np.ndarray:
    """ln w per (unit, weekday), mean-one over the window's weekday sessions (D-01). ``mult`` is
    how often each week is drawn, shape (n_draws, n_weeks), adding a leading draw axis."""
    m = np.ones(counts.shape[0]) if mult is None else mult
    s = np.einsum("...w,uwd->...ud", m, sums)
    n = np.einsum("...w,wd->...d", m, counts)[..., None, :]
    overall = s.sum(-1, keepdims=True) / n.sum(-1, keepdims=True)
    return np.log(s / n / overall)


def did(lw_pre: np.ndarray, lw_post: np.ndarray, treated: int, control: int, day: int) -> np.ndarray:
    delta = lw_post - lw_pre
    return delta[..., treated, day] - delta[..., control, day]


def shift(lw_pre: np.ndarray, lw_post: np.ndarray, unit: int, old: int, new: int) -> np.ndarray:
    delta = lw_post - lw_pre
    return delta[..., unit, new] - delta[..., unit, old]


def bootstrap(pre: tuple, post: tuple, n_boot: int, rng: np.random.Generator) -> tuple:
    """ln w draws per window. Pre and post are disjoint and resampled apart; within a window both
    units get the same weeks."""

    def draw(sums: np.ndarray, counts: np.ndarray) -> np.ndarray:
        k = counts.shape[0]
        return log_weights(sums, counts, rng.multinomial(k, np.full(k, 1 / k), size=n_boot))

    return draw(*pre), draw(*post)


def _statistics(rl: dict) -> list[tuple]:
    """(quantity, symbol, label, f(ln w pre, ln w post)) for the estimands this event supports."""
    units = list(INDEX)
    idx = {s: (units.index(s), WEEKDAYS.index(rl[s]["old"]), WEEKDAYS.index(rl[s]["new"])) for s in units}
    treated = [s for s in units if rl[s]["role"] == "treated"]
    control = [s for s in units if rl[s]["role"] == "control"]
    if len(treated) == 1 and len(control) == 1:
        (t, day, _), (c, own, _) = idx[treated[0]], idx[control[0]]
        return [
            ("did", treated[0], WEEKDAYS[day], lambda a, b: did(a, b, t, c, day)),
            ("level_gap_pre", treated[0], WEEKDAYS[day], lambda a, b: a[..., t, day] - a[..., c, day]),
            ("delta_own", control[0], WEEKDAYS[own], lambda a, b: b[..., c, own] - a[..., c, own]),
        ]
    if len(treated) == 2:
        low, high = sorted(treated, key=lambda s: rl[s]["dose_post"])
        out = [("S", s, f"{rl[s]['old']}->{rl[s]['new']}", lambda a, b, s=s: shift(a, b, *idx[s])) for s in treated]
        ratio = lambda a, b: shift(a, b, *idx[low]) / shift(a, b, *idx[high])  # noqa: E731
        return out + [("dose_ratio", low, f"{low}/{high}", ratio)]
    return []


# --- the run -----------------------------------------------------------------------------------


def _inputs(cfg: Settings) -> tuple:
    macro = json.loads((cfg.reference_dir / "macro_events.json").read_text(encoding="utf-8"))
    exclude = {dt.date.fromisoformat(d) for d in macro["union_budget"] + macro["rbi_mpc"]}
    dates, z2 = standardised_squares(cfg)
    return _sessions(cfg), dates, z2, exclude, {s: own_expiries(cfg, s) for s in INDEX}


def _ci(x: np.ndarray) -> dict:
    """95% percentile interval over the finite draws, with the count left out: a ratio whose
    denominator straddles 0 throws off inf and nan draws, and the reader should see how many."""
    finite = x[np.isfinite(x)]
    lo, hi = np.percentile(finite, [2.5, 97.5])
    return {"ci_lo": float(lo), "ci_hi": float(hi), "boot_nonfinite": int(x.size - finite.size)}


def _returns_rows(base: dict, rl: dict, pre: tuple, post: tuple, data: tuple, rng) -> list[dict]:
    _, dates, z2, exclude, _ = data
    sp, sq = week_sums(z2, dates, pre, exclude), week_sums(z2, dates, post, exclude)
    point, boot = (log_weights(*sp), log_weights(*sq)), bootstrap(sp, sq, N_BOOT, rng)
    base = base | {"channel": "returns", "readable": True}

    rows = []
    for w, (side, (_, counts)) in enumerate((("pre", sp), ("post", sq))):
        for u, symbol in enumerate(INDEX):
            for k, wd in enumerate(WEEKDAYS):
                rows.append(base | {"quantity": f"ln_w_{side}", "symbol": symbol, "weekday": wd,
                                    "estimate": float(point[w][u, k]), "n": int(counts[:, k].sum())}
                            | _ci(boot[w][:, u, k]))
    for quantity, symbol, label, f in _statistics(rl):
        rows.append(base | {"quantity": quantity, "symbol": symbol, "weekday": label,
                            "estimate": float(f(*point))} | _ci(f(*boot)))
    return rows


def _options_rows(cfg: Settings, base: dict, rl: dict, pre: tuple, post: tuple) -> list[dict]:
    """Per-fit weights, then the event statistics as points, readable only if all four fits are."""
    base = base | {"channel": "options"}
    rows, lw, healthy = [], np.full((2, len(INDEX), 5), np.nan), []
    for side, window in enumerate((pre, post)):
        for u, symbol in enumerate(INDEX):
            # An unidentified fit overflows inside its own bootstrap; ``readable`` carries that.
            try:
                with np.errstate(all="ignore"):
                    f = fit(cfg, symbol, "total_var_corr", window=window, n_boot=OPTIONS_BOOT,
                            seed=SEED, dummies=("weekend",))
            except ValueError as e:  # fit refuses a window it cannot identify: not evaluable
                log.info("options %s %s..%s refused: %s", symbol, *window, e)
                healthy.append(False)
                continue
            lw[side, u] = [np.log(f.weights[d]) for d in WEEKDAYS]
            ok = bool(f.condition_number <= MAX_COND and np.abs(lw[side, u]).max() <= MAX_ABS_LOGW)
            healthy.append(ok)
            for k, wd in enumerate(WEEKDAYS):
                lo, hi = f.boot.get(wd, (np.nan, np.nan))
                rows.append(base | {"quantity": f"ln_w_{('pre', 'post')[side]}", "symbol": symbol,
                                    "weekday": wd, "estimate": float(lw[side, u, k]),
                                    "ci_lo": float(np.log(lo)), "ci_hi": float(np.log(hi)),
                                    "cond": f.condition_number, "readable": ok, "n": f.n_groups})
    for quantity, symbol, label, stat in _statistics(rl):
        rows.append(base | {"quantity": quantity, "symbol": symbol, "weekday": label,
                            "estimate": float(stat(*lw)), "readable": all(healthy)})
    return rows


def run(cfg: Settings = settings) -> tuple[pl.DataFrame, dict]:
    data = _inputs(cfg)
    rng = np.random.default_rng(SEED)
    rows, all_roles = [], {}
    for event, variant, pre, post in WINDOWS:
        rl = roles(data[4], data[0], pre, post)
        all_roles[f"{event}/{variant}"] = rl
        base = {"event": event, "variant": variant}
        rows += _returns_rows(base, rl, pre, post, data, rng) + _options_rows(cfg, base, rl, pre, post)
    return pl.DataFrame(rows + _sensitivity_rows(cfg, data, all_roles["nov2024/full"], rng),
                        infer_schema_length=None), all_roles


def _sensitivity_rows(cfg: Settings, data: tuple, rl: dict, rng) -> list[dict]:
    """Unregistered robustness for the headline Nov-2024 windows: a slower EWMA, and the windows
    without their largest Nifty z^2 session (found from the data, so no date is asserted)."""
    sessions, dates, z2, exclude, expiries = data
    _, _, pre, post = WINDOWS[0]
    pool = [i for i, d in enumerate(dates)
            if (_inside(d, pre) or _inside(d, post)) and d.weekday() < 5 and d not in exclude]
    worst = dates[max(pool, key=lambda i: z2[0, i])]
    slow = standardised_squares(cfg, lam=EWMA_LAMBDA_SLOW)[1]
    variants = ((f"ewma_{EWMA_LAMBDA_SLOW}", (sessions, dates, slow, exclude, expiries)),
                (f"drop_{worst}", (sessions, dates, z2, exclude | {worst}, expiries)))
    rows = []
    for variant, d in variants:
        rows += _returns_rows({"event": "nov2024", "variant": variant}, rl, pre, post, d, rng)
    return rows


def randomization(cfg: Settings = settings, h: int = H_RI) -> pl.DataFrame:
    """The Nov-2024 DiD at the true event and at every D-11 pseudo date, h sessions each side. The
    true design skips the blackout (h sessions before 2024-11-13, h after 2025-01-31); pseudo
    designs are contiguous."""
    sessions, dates, z2, exclude, expiries = _inputs(cfg)
    _, _, pre, post = WINDOWS[0]
    ((_, symbol, weekday, stat),) = [
        s for s in _statistics(roles(expiries, sessions, pre, post)) if s[0] == "did"
    ]
    i0 = sessions.index(BLACKOUT[0])
    j0 = next(i for i, d in enumerate(sessions) if d > BLACKOUT[1])
    designs = [(True, i0 - h, i0, j0)] + [(False, tau - h, tau, tau) for tau in _eligible(sessions, h)]
    rows = []
    for is_event, a, b, c in designs:
        windows = ((sessions[a], sessions[b - 1]), (sessions[c], sessions[c + h - 1]))
        lw = [log_weights(*week_sums(z2, dates, w, exclude)) for w in windows]
        rows.append({"is_event": is_event, "tau": b, "pre_start": sessions[a], "post_start": sessions[c],
                     "symbol": symbol, "weekday": weekday, "did": float(stat(*lw))})
    return pl.DataFrame(rows)


def disjoint_designs(taus: list[int], h: int) -> int:
    """How many pseudo designs fit without sharing a session: the honest count of independent
    windows behind the permutation distribution (D-11)."""
    n, last = 0, -(2 * h)
    for tau in taus:
        if tau - last >= 2 * h:
            n, last = n + 1, tau
    return n


def _ri_rows(ri: pl.DataFrame) -> list[dict]:
    """True-event DiD, its percentile and upper-sided p (H1 predicts DiD > 0)."""
    event = ri.filter(pl.col("is_event")).row(0, named=True)
    null = ri.filter(~pl.col("is_event"))
    star, x = event["did"], null["did"].to_numpy()
    stats = (("did", star), ("ri_percentile", 100 * float((x < star).mean())),
             ("ri_p_upper", (1 + int((x >= star).sum())) / (1 + len(x))),
             ("ri_disjoint_windows", disjoint_designs(null["tau"].to_list(), H_RI)))
    return [{"event": "nov2024", "variant": f"ri_h{H_RI}", "channel": "returns", "quantity": q,
             "symbol": event["symbol"], "weekday": event["weekday"], "estimate": float(v),
             "readable": True, "n_pseudo": len(x)} for q, v in stats]


def main(cfg: Settings = settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    table, all_roles = run(cfg)
    ri = randomization(cfg)
    table = pl.concat([table, pl.DataFrame(_ri_rows(ri))], how="diagonal_relaxed")

    for key, rl in all_roles.items():
        log.info("%-24s %s", key, {s: f"{v['role']} {v['old']}->{v['new']} dose "
                                      f"{v['dose_pre']:.2f}/{v['dose_post']:.2f}" for s, v in rl.items()})
    for r in table.filter(~pl.col("quantity").str.starts_with("ln_w")).iter_rows(named=True):
        span = f"[{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]" if r["ci_lo"] is not None else ""
        if r["boot_nonfinite"]:
            span += f" ({r['boot_nonfinite']} non-finite draws left out)"
        log.info("%-8s %-15s %-8s %-14s %-9s %-20s %+.3f %s%s", r["event"], r["variant"], r["channel"],
                 r["quantity"], r["symbol"], r["weekday"], r["estimate"], span,
                 "" if r["readable"] else "  NOT READABLE")

    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.tables_dir / "dose_response.parquet"
    table.write_parquet(path)
    write_manifest(path, manifest(
        "bank nifty dose-response", cfg, seed=SEED,
        inputs=(cfg.panel_dir / "index_daily.parquet", cfg.tables_dir / "atm_variance.parquet",
                cfg.reference_dir / "macro_events.json"),
        extra={"roles": all_roles, "ewma_lambda": EWMA_LAMBDA, "ewma_lambda_slow": EWMA_LAMBDA_SLOW,
               "n_boot": N_BOOT, "options_boot": OPTIONS_BOOT, "h_ri": H_RI,
               "ri_null_did": ri.filter(~pl.col("is_event"))["did"].to_list(),
               "windows": [[e, v, *map(str, p), *map(str, q)] for e, v, p, q in WINDOWS]},
    ))


if __name__ == "__main__":
    main()

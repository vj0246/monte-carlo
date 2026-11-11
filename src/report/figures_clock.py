"""Figures for the measurement half: the clock itself, its stability, and the tests on it.

Every number in a takeaway is read from a results table at build time, never typed in, so a rerun
that moves an estimate moves the sentence with it.
"""

from __future__ import annotations

import matplotlib.dates as mdates
import numpy as np
import polars as pl
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

from src.config import Settings
from src.report import style
from src.report.style import AQUA, BLUE, INK_2, MUTED, ORANGE, WEEKDAYS, Exhibit

REGIMES = (("Thu regime", "Thu", "Thursday expiry, to 2025-08-28"),
           ("Tue regime", "Tue", "Tuesday expiry, from 2025-09-02"))
WEIGHT_TICKS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3]


def _log_weights(ax, lo: float = 0.2, hi: float = 2.4, axis: str = "y") -> None:
    scale = ax.set_yscale if axis == "y" else ax.set_xscale
    lim = ax.set_ylim if axis == "y" else ax.set_xlim
    target = ax.yaxis if axis == "y" else ax.xaxis
    scale("log")
    lim(lo, hi)
    target.set_major_locator(FixedLocator([t for t in WEIGHT_TICKS if lo <= t <= hi]))
    target.set_minor_locator(NullLocator())
    target.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))


def _fits(cfg: Settings) -> pl.DataFrame:
    return pl.read_parquet(cfg.tables_dir / "clock_fits.parquet")


def clock_weights(cfg: Settings) -> Exhibit:
    fits = _fits(cfg).filter((pl.col("symbol") == "NIFTY") & (pl.col("half") == "full")
                             & pl.col("parameter").is_in(WEEKDAYS))
    fig, axes = style.figure(2, width=10, height=4.2, sharey=True)
    x = np.arange(5)
    for ax, (label, expiry, subtitle) in zip(axes, REGIMES):
        for spec, colour, dx, face in (("weekday", BLUE, -0.12, BLUE), ("macro", ORANGE, 0.12, "none")):
            sub = fits.filter((pl.col("label") == label) & (pl.col("spec") == spec))
            rows = [sub.filter(pl.col("parameter") == d).row(0, named=True) for d in WEEKDAYS]
            pt = np.array([r["point"] for r in rows])
            ax.vlines(x + dx, [r["lo"] for r in rows], [r["hi"] for r in rows], color=colour, lw=2)
            ax.scatter(x + dx, pt, s=46, color=face, edgecolor=colour, linewidth=1.6, zorder=3,
                       label="weekday-only (published)" if spec == "weekday" else "+ RBI/Budget dummy")
        style.null_line(ax, 1.0, "uniform clock")
        ax.set_xticks(x, [f"{d}\nexpiry" if d == expiry else d for d in WEEKDAYS])
        ax.set_title(f"Nifty, {subtitle}")
        _log_weights(ax)
    axes[0].set_ylabel("variance weight (mean one, log scale)")
    axes[0].legend(loc="upper left")
    thu = fits.filter((pl.col("label") == "Thu regime") & (pl.col("parameter") == "Thu")
                      & (pl.col("spec") == "weekday")).row(0, named=True)
    tue = fits.filter((pl.col("label") == "Tue regime") & (pl.col("parameter") == "Tue")
                      & (pl.col("spec") == "weekday")).row(0, named=True)
    takeaway = (f"In both regimes the expiry session carries the least variance: Thursday "
                f"{thu['point']:.3f} [{thu['lo']:.3f}, {thu['hi']:.3f}], Tuesday {tue['point']:.3f} "
                f"[{tue['lo']:.3f}, {tue['hi']:.3f}]; 95% bootstrap intervals over dates. Adding the "
                "RBI/Budget dummy (hollow) leaves the expiry dip in place and removes most of the "
                "Thursday-regime Friday premium, which was scheduled macro variance.")
    table = fits.select("label", "spec", "parameter", "point", "lo", "hi", "condition_number")
    return Exhibit("clock_weights", "The variance clock", takeaway,
                   style.save(fig, cfg.figures_dir / "01_clock_weights.png"), table)


def business_time(cfg: Settings) -> Exhibit:
    """Cumulative variance through one expiry cycle: the clock as a time change."""
    boot = pl.read_parquet(cfg.tables_dir / "clock_boot.parquet")
    fits = _fits(cfg)
    fig, axes = style.figure(2, width=10, height=4.2, sharey=True)
    rows = []
    for ax, (label, expiry, subtitle) in zip(axes, REGIMES):
        k = WEEKDAYS.index(expiry)
        order = [WEEKDAYS[(k + 1 + j) % 5] for j in range(5)]
        keep = (pl.col("symbol") == "NIFTY") & (pl.col("label") == label) & (
            pl.col("half") == "full") & (pl.col("spec") == "weekday")
        draws = boot.filter(keep).select(order).to_numpy()
        draws = 5 * draws / draws.sum(axis=1, keepdims=True)
        point = np.array([fits.filter(keep & (pl.col("parameter") == d))["point"].item() for d in order])
        point = 5 * point / point.sum()
        cum = np.hstack([np.zeros((len(draws), 1)), draws.cumsum(axis=1)])
        lo, hi = np.percentile(cum, [2.5, 97.5], axis=0)
        t = np.arange(6)
        ax.fill_between(t, lo, hi, color=BLUE, alpha=0.16, linewidth=0, label="clock, 95% band")
        ax.plot(t, np.r_[0, point.cumsum()], color=BLUE, marker="o", label="clock (point fit)")
        ax.plot(t, t, color=ORANGE, label="calendar time")
        ax.set_xticks(t, ["prev\nexpiry"] + [f"{d}\nexpiry" if d == expiry else d for d in order])
        ax.set_title(f"Nifty, {subtitle}")
        share = point[-1] / 5
        ax.text(0.97, 0.06, f"expiry session: {100 * share:.0f}% of the week's variance\n"
                "(calendar time: 20%)", transform=ax.transAxes, ha="right", color=INK_2, fontsize=9)
        rows += [{"regime": label, "session": d, "clock_cum": c, "lo": a, "hi": b}
                 for d, c, a, b in zip(["start"] + order, np.r_[0, point.cumsum()], lo, hi)]
    axes[0].set_ylabel("cumulative variance (average sessions)")
    axes[0].legend(loc="upper left")
    takeaway = ("Read as a time change, the clock is how fast the option's world moves. The two "
                "curves agree at both ends by construction (both traders agree on the week), so "
                "every pricing and hedging difference lives in the bend: how much of the week's "
                "variance is still to come when a position is opened.")
    return Exhibit("business_time", "Business time against calendar time", takeaway,
                   style.save(fig, cfg.figures_dir / "02_business_time.png"), pl.DataFrame(rows))


def stability(cfg: Settings) -> Exhibit:
    """Own-expiry weight in every fit: regimes, halves, symbols, specifications."""
    fits = _fits(cfg)
    colours = {"NIFTY": BLUE, "SENSEX": ORANGE, "BANKNIFTY": AQUA}
    rows = []
    for sym in ("NIFTY", "SENSEX", "BANKNIFTY"):
        for label in fits.filter(pl.col("symbol") == sym)["label"].unique(maintain_order=True):
            own = label.split()[0]
            for half in ("full", "h1", "h2"):
                for spec in ("weekday", "macro") if half == "full" else ("weekday",):
                    r = fits.filter((pl.col("symbol") == sym) & (pl.col("label") == label)
                                    & (pl.col("half") == half) & (pl.col("spec") == spec)
                                    & (pl.col("parameter") == own))
                    if r.height:
                        rows.append(r.row(0, named=True))
    table = pl.DataFrame(rows).select("symbol", "label", "half", "spec", "parameter", "point", "lo",
                                      "hi", "condition_number", "identified")
    fig, ax = style.figure(width=9, height=0.3 * table.height + 1.4)
    y = np.arange(table.height)[::-1]
    for yi, r in zip(y, table.iter_rows(named=True)):
        c = colours[r["symbol"]]
        lo, hi = max(r["lo"], 0.05), min(r["hi"], 4.0)
        ax.hlines(yi, lo, hi, color=c, lw=2, alpha=1.0 if r["identified"] else 0.45)
        ax.scatter(min(max(r["point"], 0.05), 4.0), yi, s=40, zorder=3, edgecolor=c, linewidth=1.6,
                   color=c if r["identified"] else "none")
    names = [f"{r['symbol']} {r['label'].replace(' regime', '')} {r['half']}"
             + (" +macro" if r["spec"] == "macro" else "") for r in table.iter_rows(named=True)]
    ax.set_yticks(y, names, fontsize=8.5)
    ax.grid(axis="y", visible=False)
    style.null_line(ax, 1.0, "uniform", horizontal=False)
    _log_weights(ax, 0.05, 4.0, axis="x")
    ax.xaxis.set_major_locator(FixedLocator([0.1, 0.25, 0.5, 1, 2, 4]))
    ax.set_xlabel("weight of the symbol's own expiry weekday (log scale; hollow = unidentified fit)")
    for sym, c in colours.items():
        ax.scatter([], [], color=c, label=sym)
    ax.legend(loc="lower right")
    takeaway = ("Nifty's own-expiry weight sits below one in every half and specification; the "
                "direction never flips. Sensex does not replicate it: where its fits are identified "
                "the own-expiry session is flat or high, and its Tuesday regime is not identified. "
                "Bank Nifty's own expiry is never significantly low. Hollow markers are fits whose "
                "design is degenerate (condition number above 30 or a multiplier at a boundary).")
    return Exhibit("stability", "Is the expiry dip stable, and does it generalise?", takeaway,
                   style.save(fig, cfg.figures_dir / "03_stability.png"), table)


def identification(cfg: Settings) -> Exhibit:
    """What identifies the clock: the term structure seen from different opening weekdays."""
    obs = pl.read_parquet(cfg.tables_dir / "clock_obs.parquet").filter(pl.col("sessions") <= 12)
    palette = [BLUE, ORANGE, AQUA, "#eda100", "#e87ba4"]
    fig, axes = style.figure(2, width=10, height=4.4)
    tables = []
    for ax, (label, _, subtitle) in zip(axes, REGIMES):
        g = (obs.filter(pl.col("label") == label).group_by("open_weekday", "sessions")
             .agg(pl.col("observed_dm").mean(), pl.col("fitted_dm").mean(), pl.len().alias("n"))
             .filter(pl.col("n") >= 5).sort("sessions"))
        for d, c in zip(WEEKDAYS, palette):
            s = g.filter(pl.col("open_weekday") == d)
            if not s.height:
                continue
            ax.plot(s["sessions"], s["fitted_dm"], color=c, lw=1.6)
            ax.scatter(s["sessions"], s["observed_dm"], s=22, color=c, zorder=3,
                       label=f"opened {d}")
        ax.set_title(f"Nifty, {subtitle}")
        ax.set_xlabel("sessions to expiry")
        tables.append(g.with_columns(pl.lit(label).alias("regime")))
    axes[0].set_ylabel("log total variance, demeaned within date")
    axes[0].legend(loc="lower right", ncols=2)
    table = pl.concat(tables)
    rmse = float(np.sqrt(((table["observed_dm"] - table["fitted_dm"]) ** 2).mean()))
    takeaway = ("Dots are observed ATM total variance (averaged per opening weekday and maturity), "
                "lines the fitted clock. One date gives only a few maturities, so the clock is "
                "identified by how the curve shifts with the opening weekday: the same number of "
                "sessions spans a different weekday mix. Five weekday weights fit these averages "
                f"to an RMSE of {rmse:.3f} in log variance.")
    return Exhibit("identification", "What identifies the clock", takeaway,
                   style.save(fig, cfg.figures_dir / "04_identification.png"), table)


def permutation(cfg: Settings) -> Exhibit:
    df = pl.read_parquet(cfg.tables_dir / "permutation.parquet")
    fig, axes = style.figure(2, width=10, height=4.0, sharey=True)
    rows = []
    for ax, h in zip(axes, (63, 42)):
        d = df.filter(pl.col("h") == h)
        null = d.filter(~pl.col("is_event")).drop_nans("s_nifty")
        star = d.filter(pl.col("is_event"))["s_nifty"].item()
        p = (1 + int((null["s_nifty"] <= star).sum())) / (1 + null.height)
        ax.scatter(null["tau"], null["s_nifty"], s=16, color=MUTED, label="pseudo-event dates")
        ax.scatter(d.filter(pl.col("is_event"))["tau"], [star], s=110, marker="*", color=BLUE,
                   zorder=3, label="Sep-2025 rule change")
        q05 = float(np.quantile(null["s_nifty"], 0.05))
        style.null_line(ax, q05, "5th percentile of the null")
        # Degenerate pseudo-window fits reach |S| ~ 400; symlog keeps them visible without
        # flattening the informative band around zero.
        ax.set_yscale("symlog", linthresh=2.0)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.set_title(f"window h = {h} sessions: one-sided p = {p:.3f}")
        rows.append({"h": h, "s_star": star, "n_pseudo": null.height, "p": p, "q05": q05})
    axes[0].set_ylabel("S = Δln w(Tue) − Δln w(Thu)  (symlog)")
    axes[0].legend(loc="upper left")
    t = pl.DataFrame(rows)
    takeaway = (f"The true event ranks low but inside the middle 90% at both window lengths "
                f"(p = {t['p'][0]:.3f} and {t['p'][1]:.3f}), so the pre-registered kill condition "
                "fires: the Thursday-to-Tuesday shift is not distinguishable from calendar drift. "
                "Neighbouring pseudo dates share almost all their data, so there are only about "
                "five independent windows; no p-value from this design can be much below 1/6.")
    return Exhibit("permutation", "Did the rule move the clock? Randomization inference", takeaway,
                   style.save(fig, cfg.figures_dir / "05_permutation.png"), t)


def dose_response(cfg: Settings) -> Exhibit:
    d = pl.read_parquet(cfg.tables_dir / "dose_response.parquet").filter(
        (pl.col("event") == "nov2024") & (pl.col("quantity") == "did") & (pl.col("channel") == "returns")
        & pl.col("ci_lo").is_not_null())
    fig, ax = style.figure(width=8, height=3.0)
    y = np.arange(d.height)[::-1]
    ax.hlines(y, d["ci_lo"], d["ci_hi"], color=AQUA, lw=2)
    ax.scatter(d["estimate"], y, color=AQUA, s=44, zorder=3)
    ax.set_yticks(y, [v.replace("_", " ") for v in d["variant"]])
    ax.grid(axis="y", visible=False)
    style.null_line(ax, 0.0, "no effect", horizontal=False)
    ax.set_xlabel("DiD of ln own-expiry weight, Bank Nifty minus Nifty, Nov-2024 (returns channel)")
    head = d.filter(pl.col("variant") == "full").row(0, named=True)
    takeaway = (f"Removing Bank Nifty's weekly did not measurably lift its Wednesday variance "
                f"relative to Nifty: {head['estimate']:+.3f} [{head['ci_lo']:+.3f}, "
                f"{head['ci_hi']:+.3f}] under the pre-registered windows, and every robustness "
                "variant straddles zero. Daily returns are too noisy for this question.")
    return Exhibit("dose_response", "Bank Nifty dose-response at Nov-2024", takeaway,
                   style.save(fig, cfg.figures_dir / "12_dose_response.png"),
                   d.select("variant", "estimate", "ci_lo", "ci_hi", "n"))

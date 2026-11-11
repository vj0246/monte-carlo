"""Figures for the simulation half: what the clock is worth, and how we know the numbers are right.

Takeaway numbers are read from the result tables at build time. The one figure that simulates
(the pathwise EKJS scatter) uses a fixed seed and 20,000 paths, enough to show the identity and
cheap enough to rebuild.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from matplotlib.colors import LinearSegmentedColormap

from src.config import Settings
from src.experiment.mispricing import ANNUAL_VOL, SESSIONS_PER_YEAR, SPOT, TUE_REGIME
from src.report import style
from src.report.style import AQUA, BLUE, INK, MUTED, ORANGE, RAMP, Exhibit
from src.sim.analytic import believed_paths, ekjs_pathwise
from src.sim.engine import hedge_short_straddle, simulate, straddle_price

SESSION_ORDER = ("Wed", "Thu", "Fri", "Mon", "Tue")


def _limit(cfg: Settings) -> pl.DataFrame:
    return pl.read_parquet(cfg.tables_dir / "hedging_limit.parquet")


def _value(table: pl.DataFrame, name: str, method_prefix: str) -> float:
    return table.filter((pl.col("name") == name) & pl.col("method").str.starts_with(method_prefix))[
        "value"].item()


def hedging_floor(cfg: Settings) -> Exhibit:
    """The headline after D-25: discretisation error falls, the clock error does not."""
    levels = pl.read_parquet(cfg.tables_dir / "mlmc_levels.parquet")
    lim = _limit(cfg)
    floor = _value(lim, "floor", "closed form")
    c_clock = _value(lim, "c_clock", "closed form")
    m_star = _value(lim, "m_star_clock", "(c/F)^2, closed")
    fig, ax = style.figure(width=9, height=4.8)
    series = (("clock", BLUE, "clock-aware hedger (total P&L sd)"),
              ("calendar", ORANGE, "calendar-time hedger (total P&L sd)"),
              ("diff", AQUA, "calendar minus clock (the clock's own risk)"))
    for q, colour, label in series:
        s = levels.filter(pl.col("quantity") == q).sort("hedges_per_session")
        ax.plot(s["hedges_per_session"], s["sd"], color=colour, marker="o", label=label)
    m = np.geomspace(1, 256, 50)
    ax.plot(m, c_clock / np.sqrt(m), color=MUTED, lw=1.0, label=r"$c\,m^{-1/2}$, closed form")
    style.null_line(ax, floor)
    ax.annotate(f"clock-risk floor F = {floor:.2f} (closed form)", (1, floor), xytext=(0, 5),
                textcoords="offset points", color=MUTED, fontsize=9)
    ax.axvline(m_star, color=MUTED, lw=1.0)
    ax.annotate(f"m* = {m_star:.0f}: discretisation\nerror equals the floor", (m_star, 60),
                xytext=(-6, 0), textcoords="offset points", ha="right", color=INK, fontsize=9)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks([1, 2, 4, 8, 16, 32, 64, 128, 256], ["1", "2", "4", "8", "16", "32", "64", "128", "256"])
    ax.set_yticks([5, 10, 20, 50, 100, 200], ["5", "10", "20", "50", "100", "200"])
    ax.set_xlabel("delta hedges per session (log scale)")
    ax.set_ylabel("P&L standard deviation, index points")
    ax.set_title("Short weekly ATM straddle, Tue-regime clock, opened Wednesday")
    ax.legend(loc="upper right")
    entry = straddle_price(SPOT, SPOT, (ANNUAL_VOL**2 / SESSIONS_PER_YEAR) * 5)
    takeaway = (f"Discretisation error falls as c/sqrt(m) with c = {c_clock:.1f}; the clock's "
                f"contribution does not fall at all. It converges to F = {floor:.2f} index points "
                f"({100 * floor / entry:.2f}% of the {entry:.0f}-point premium), the pathwise EKJS "
                f"integral that no rebalancing removes. Beyond m* = {m_star:.0f} hedges per session "
                "a calendar-time hedger's dominant risk is the wrong clock, not the hedge frequency. "
                "Curves are multilevel Monte Carlo levels (Giles 2008); the floor and the rate "
                "constant are closed forms.")
    table = levels.filter(pl.col("quantity").is_in(["clock", "calendar", "diff"])).select(
        "hedges_per_session", "quantity", "sd", "n_pairs")
    return Exhibit("hedging_floor", "Clock risk is a floor that rebalancing cannot remove", takeaway,
                   style.save(fig, cfg.figures_dir / "06_hedging_floor.png"), table)


def mlmc_diagnostics(cfg: Settings) -> Exhibit:
    levels = pl.read_parquet(cfg.tables_dir / "mlmc_levels.parquet").filter(pl.col("quantity") == "diff")
    lim = _limit(cfg)
    alpha, beta = _value(lim, "alpha", "fit"), _value(lim, "beta", "fit")
    fig, axes = style.figure(2, width=10, height=4.0)
    lv = levels["level"].to_numpy()
    var_p = levels["var_y"].to_numpy()[:1]  # at level 0, Y_0 = P_0
    axes[0].plot(lv[1:], np.log2(levels["var_y"].to_numpy()[1:]), color=BLUE, marker="o",
                 label=r"Var$[Y_l]$, $Y_l = P_l - P_{l-1}$")
    axes[0].scatter([0], np.log2(var_p), color=ORANGE, zorder=3, label=r"Var$[P_0]$")
    fit = np.polyfit(lv[2:], np.log2(levels["var_y"].to_numpy()[2:]), 1)
    ref = np.polyval(fit, lv)
    axes[0].plot(lv, ref, color=MUTED, lw=1.0, label=f"fitted slope β = {beta:.2f}")
    axes[0].set_ylabel(r"log$_2$ variance (points$^4$)")
    axes[0].set_title("Variance of the level corrections")
    mean_y = np.abs(levels["mean_y"].to_numpy())
    axes[1].plot(lv[1:], np.log2(mean_y[1:]), color=BLUE, marker="o", label=r"$|E[Y_l]|$")
    fit = np.polyfit(lv[2:], np.log2(mean_y[2:]), 1)
    ref = np.polyval(fit, lv)
    axes[1].plot(lv, ref, color=MUTED, lw=1.0, label=f"fitted slope α = {alpha:.2f}")
    axes[1].set_ylabel(r"log$_2$ |mean| (points$^2$)")
    axes[1].set_title("Bias of the level corrections")
    for ax in axes:
        ax.set_xlabel("level l  (2^l hedges per session)")
        ax.legend(loc="upper right")
    saving = _value(lim, "cost_saving", "single / control")
    takeaway = (f"Giles' complexity diagnostics for P = (calendar - clock)^2. Both rates are near the "
                f"theoretical 1 (β = {beta:.2f}, α = {alpha:.2f}) and equal to the cost rate γ = 1, "
                "the boundary case where MLMC costs O(ε^-2 log² ε). Honest outcome: here plain MLMC "
                "did not beat a single fine level, because the level-0 variance dominates; a control "
                f"variate (the exact EKJS sum) did, by {saving:.0f}x.")
    return Exhibit("mlmc_diagnostics", "Multilevel Monte Carlo: rates and cost", takeaway,
                   style.save(fig, cfg.figures_dir / "07_mlmc_diagnostics.png"),
                   levels.select("level", "hedges_per_session", "n_pairs", "mean_y", "var_y"))


def ekjs_pathwise_figure(cfg: Settings, n_paths: int = 20_000, seed: int = 5) -> Exhibit:
    """Gate A6 made visible: per path, the hedging difference against the EKJS integral."""
    sigma = ANNUAL_VOL / np.sqrt(SESSIONS_PER_YEAR)
    w = np.array([TUE_REGIME[d] for d in SESSION_ORDER])
    w = w / w.mean()
    entry = straddle_price(SPOT, SPOT, float(sigma**2 * w.sum()))
    fig, axes = style.figure(2, width=10, height=4.4, sharex=True, sharey=True)
    rows = []
    cmap = LinearSegmentedColormap.from_list("blue", ["#fcfcfb"] + RAMP)
    for ax, m in zip(axes, (1, 64)):
        truth, flat = believed_paths(w, m)
        r, b = sigma**2 * truth, sigma**2 * flat
        rem = lambda v: v.sum() - np.concatenate([[0.0], np.cumsum(v)[:-1]])  # noqa: E731
        paths = simulate(truth, sigma, SPOT, n_paths, np.random.default_rng(seed))
        diff = (hedge_short_straddle(paths, SPOT, rem(b), entry, 0.0)
                - hedge_short_straddle(paths, SPOT, rem(r), entry, 0.0))
        ekjs = ekjs_pathwise(paths, SPOT, r, b)
        ax.hexbin(ekjs, diff, gridsize=55, cmap=cmap, mincnt=1, extent=(-40, 60, -40, 60))
        ax.plot([-40, 60], [-40, 60], color=MUTED, lw=1.0)
        corr = float(np.corrcoef(ekjs, diff)[0, 1])
        rel = float(np.sqrt(np.mean((diff - ekjs) ** 2)) / diff.std())
        ax.set_title(f"{m} hedge{'s' if m > 1 else ''} per session: corr {corr:.3f}, "
                     f"relative RMS gap {rel:.2f}")
        ax.set_xlabel("EKJS integral along the path, index points")
        rows.append({"hedges_per_session": m, "corr": corr, "relative_rms_gap": rel,
                     "diff_sd": float(diff.std()), "ekjs_sd": float(ekjs.std())})
    axes[0].set_ylabel("simulated P&L, calendar minus clock")
    t = pl.DataFrame(rows)
    takeaway = ("Each hexagon counts simulated paths. At one hedge per session the two quantities "
                f"are nearly unrelated (corr {t['corr'][0]:.2f}); at 64 they collapse onto the "
                f"diagonal (corr {t['corr'][1]:.3f}). The clock's hedging cost is not a mean effect "
                "(the mean is exactly zero on every grid, D-25) but this pathwise integral, whose "
                "dispersion is the floor in the previous figure.")
    return Exhibit("ekjs_pathwise", "The simulator converges pathwise to the EKJS integral", takeaway,
                   style.save(fig, cfg.figures_dir / "08_ekjs_pathwise.png"), t)


def entry_error_uq(cfg: Settings) -> Exhibit:
    summary = pl.read_parquet(cfg.tables_dir / "clock_uncertainty_summary.parquet")
    per_draw = pl.read_parquet(cfg.tables_dir / "clock_uncertainty.parquet")
    regime = "Tue regime"
    names = [q for q in summary.filter(pl.col("regime") == regime)["quantity"]
             if q.startswith(("open_", "spread_"))]
    fig, axes = style.figure(3, 2, width=10, height=6.2)
    for ax, q in zip(axes.ravel(), names):
        x = 100 * per_draw.filter((pl.col("regime") == regime) & (pl.col("quantity") == q)
                                  & pl.col("draw").is_not_null())["value"].to_numpy()
        s = summary.filter((pl.col("regime") == regime) & (pl.col("quantity") == q)).row(0, named=True)
        ax.hist(x, bins=30, color=BLUE, alpha=0.85, edgecolor=style.SURFACE, linewidth=0.6)
        ax.axvline(100 * s["point"], color=INK, lw=1.4)
        top = ax.get_ylim()[1]
        ax.hlines(top * 0.92, 100 * s["lo"], 100 * s["hi"], color=BLUE, lw=3,
                  label="95% band, propagated")
        if s["naive_lo"] is not None:
            ax.hlines(top * 0.80, 100 * s["naive_lo"], 100 * s["naive_hi"], color=ORANGE, lw=3,
                      label="naive: marginal intervals")
        style.null_line(ax, 0.0, horizontal=False)
        day = q.split("_")[1]
        kind = "spread" if q.startswith("spread") else "straddle"
        ax.set_title(f"{kind} opened {day}", fontsize=10)
        ax.grid(axis="y", visible=False)
        ax.set_yticks([])
    for ax in axes[-1]:
        ax.set_xlabel("calendar-time trader's price error, %")
    week = axes.ravel()[names.index("open_Tue")]
    week.text(0.5, 0.45, "full week: error identically 0\nin all 300 clocks",
              transform=week.transAxes, ha="center", color=style.INK_2, fontsize=9,
              bbox={"facecolor": style.SURFACE, "edgecolor": "none"})
    week.legend(*axes[0, 0].get_legend_handles_labels(), loc="lower center", fontsize=8,
                frameon=True, facecolor=style.SURFACE, edgecolor="none", framealpha=1.0)
    mon = summary.filter((pl.col("regime") == regime) & (pl.col("quantity") == "open_Mon")).row(0, named=True)
    takeaway = (f"300 bootstrap clocks pushed through exact Black-76 prices. The one-session "
                f"straddle (opened Monday): {100 * mon['point']:.1f}% at the point fit, 95% band "
                f"[{100 * mon['lo']:.1f}%, {100 * mon['hi']:.1f}%], negative in "
                f"{100 * mon['p_negative']:.0f}% of draws. For one-weight products the naive band is "
                "already exact; for multi-session spans it is too wide, because the mean-one "
                "normalisation makes weekday weights negatively correlated, and for the full week "
                "it invents an error that is identically zero.")
    return Exhibit("entry_error_uq", "Entry-price error with the clock's own uncertainty", takeaway,
                   style.save(fig, cfg.figures_dir / "09_entry_error_uq.png"),
                   summary.filter(pl.col("regime") == regime))


def _heatmap(ax, grid: np.ndarray, rhos, xis, fmt: str, title: str) -> None:
    cmap = LinearSegmentedColormap.from_list("blue", RAMP[:5])
    im = ax.imshow(grid, cmap=cmap, aspect="auto")
    ax.set_xticks(range(len(xis)), [f"{x:g}" for x in xis])
    ax.set_yticks(range(len(rhos)), [f"{r:g}" for r in rhos])
    ax.set_xlabel("vol-of-vol ξ")
    ax.set_ylabel("correlation ρ")
    ax.grid(False)
    mid = np.nanmean(grid)
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            ax.text(j, i, format(grid[i, j], fmt), ha="center", va="center", fontsize=9,
                    color="#ffffff" if grid[i, j] > mid else INK)
    ax.set_title(title, fontsize=10)
    return im


def heston(cfg: Settings) -> Exhibit:
    sweep = pl.read_parquet(cfg.tables_dir / "heston_robustness.parquet").filter(
        pl.col("rebalances_per_session") == 1)
    rob = sweep.filter(pl.col("xi") > 1e-3)
    gbm = sweep.filter(pl.col("xi") <= 1e-3)["diff_sd"].item()
    entry = pl.read_parquet(cfg.tables_dir / "heston_entry_error.parquet").filter(
        (pl.col("product") == "Mon") & (pl.col("xi") > 0))
    rhos, xis = sorted(rob["rho"].unique(), reverse=True), sorted(rob["xi"].unique())
    g1 = np.array([[rob.filter((pl.col("rho") == r) & (pl.col("xi") == x))["diff_sd"].item()
                    for x in xis] for r in rhos])
    g2 = np.array([[entry.filter((pl.col("rho") == r) & (pl.col("xi") == x))["shift_pp"].item()
                    for x in xis] for r in rhos])
    fig, axes = style.figure(2, width=10, height=4.0)
    _heatmap(axes[0], g1, rhos, xis, ".2f", "clock's hedging risk (sd, index points), 1 hedge/session")
    _heatmap(axes[1], g2, rhos, xis, "+.4f", "shift in the Monday entry error vs GBM (pp)")
    takeaway = (f"Under stochastic volatility the clock's hedging risk stays in "
                f"[{g1.min():.2f}, {g1.max():.2f}] index points (GBM limit: {gbm:.2f}) and the one-session "
                f"entry error moves by at most {np.abs(g2).max():.4f} percentage points, as a "
                "cumulant expansion predicts (the shift is of order ρξT(√w - 1)/8, tiny over one "
                "session). Neither headline rests on constant volatility. Prices are exact "
                "piecewise-clock Heston Fourier; the hedging cells are QE Monte Carlo.")
    table = rob.select("rho", "xi", "diff_sd", "clock_sd", "share_of_sd", "feller_ok").join(
        entry.select("rho", "xi", "error", "shift_pp"), on=["rho", "xi"])
    return Exhibit("heston", "Does any of it rest on constant volatility?", takeaway,
                   style.save(fig, cfg.figures_dir / "10_heston.png"), table)


def mc_efficiency(cfg: Settings) -> Exhibit:
    eff = pl.read_parquet(cfg.tables_dir / "mc_efficiency.parquet").filter(
        (pl.col("rho") == -0.6) & (pl.col("xi") == 0.5) & (pl.col("steps_per_session") == 8))
    methods = ["plain", "antithetic", "conditional", "conditional_cv", "rqmc_plain", "rqmc_conditional"]
    colours = dict(zip(methods, [MUTED, ORANGE, AQUA, "#008300", "#6250d6", BLUE]))
    fig, axes = style.figure(2, width=10.5, height=4.4)
    mon = eff.filter(pl.col("product") == "Mon")
    for meth in methods:
        s = mon.filter(pl.col("method") == meth).sort("n_paths")
        axes[0].plot(s["n_paths"], s["rmse"], color=colours[meth], marker="o", ms=4,
                     label=f"{meth.replace('_', ' + ')} (slope {s['rmse_slope'][0]:.2f})")
    axes[0].set_xscale("log", base=2)
    axes[0].set_yscale("log")
    axes[0].set_xlabel("paths (log scale)")
    axes[0].set_ylabel("RMSE vs exact Fourier price, index points")
    axes[0].set_title("Monday 1-session straddle, ρ = -0.6, ξ = 0.5")
    axes[0].legend(loc="lower left", fontsize=7.5)
    first = eff.group_by("product", "method", maintain_order=True).first()
    products = ["Mon", "Fri", "spread_Mon"]
    y = np.arange(len(methods))[::-1]
    for k, prod in enumerate(products):
        s = first.filter(pl.col("product") == prod)
        vals = [s.filter(pl.col("method") == m).row(0, named=True) for m in methods]
        dy = (k - 1) * 0.25
        axes[1].hlines(y + dy, [v["eff_vs_plain_lo"] for v in vals], [v["eff_vs_plain_hi"] for v in vals],
                       color=[BLUE, ORANGE, AQUA][k], lw=2)
        axes[1].scatter([v["efficiency_vs_plain"] for v in vals], y + dy, color=[BLUE, ORANGE, AQUA][k],
                        s=28, zorder=3, label=prod.replace("_", " "))
    axes[1].set_xscale("log")
    axes[1].set_yticks(y, [m.replace("_", " + ") for m in methods])
    axes[1].grid(axis="y", visible=False)
    style.null_line(axes[1], 1.0, "plain MC", horizontal=False)
    axes[1].set_xlabel("efficiency vs plain MC (1 / variance × time), log scale")
    axes[1].set_title("Work-normalised gain, 95% interval")
    axes[1].legend(loc="upper right")
    best = first.filter((pl.col("product") == "Mon") & (pl.col("method") == "rqmc_conditional")).row(0, named=True)
    anti = first.filter((pl.col("product") == "Mon") & (pl.col("method") == "antithetic")).row(0, named=True)
    takeaway = (f"Conditioning on the variance path (Willard 1997) removes the spot noise; scrambled "
                f"Sobol then converges at slope {best['rmse_slope']:.2f}, against -0.5 for "
                f"pseudo-random sampling, for a work-normalised gain of about "
                f"{best['efficiency_vs_plain']:.0f}x. Antithetic sampling loses "
                f"({anti['efficiency_vs_plain']:.2f}x): a straddle payoff is nearly even in the "
                "driving normal, so a path and its mirror pay almost the same and the pair costs "
                "twice for little variance reduction.")
    return Exhibit("mc_efficiency", "State-of-the-art terminal pricing: what each trick buys", takeaway,
                   style.save(fig, cfg.figures_dir / "11_mc_efficiency.png"),
                   first.select("product", "method", "efficiency_vs_plain", "eff_vs_plain_lo",
                                "eff_vs_plain_hi", "rmse_slope"))

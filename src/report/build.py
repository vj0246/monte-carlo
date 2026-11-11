"""Build every figure and one self-contained HTML report (D-21, D-33).

The report is a single file with the images embedded, so it opens offline and can be sent as is.
Each figure carries its takeaway and a table view of the numbers behind it; the three headline
numbers at the top are read from the result tables, never typed in.

Run:  uv run python -m src.report.build      ->  results/report.html, results/figures/*.png
"""

from __future__ import annotations

import base64
import html
import logging

import polars as pl

from src.config import Settings, settings
from src.provenance import git_commit
from src.report import figures_clock, figures_sim
from src.report.style import Exhibit

SECTIONS = (
    ("What the market does", "Measured from NSE and BSE option settlements, 2024-01 to 2026-09.",
     (figures_clock.clock_weights, figures_clock.business_time, figures_clock.identification,
      figures_clock.stability, figures_clock.permutation, figures_clock.dose_response)),
    ("What it is worth", "Simulated on the estimated clock; every simulator is checked against a "
     "closed form before its numbers are used.",
     (figures_sim.entry_error_uq, figures_sim.hedging_floor, figures_sim.ekjs_pathwise_figure,
      figures_sim.mlmc_diagnostics, figures_sim.heston, figures_sim.mc_efficiency)),
)

CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #f9f9f7; color: #0b0b0b;
       font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; }
h2 { font-size: 21px; margin: 48px 0 4px; border-top: 1px solid #e1e0d9; padding-top: 24px; }
h3 { font-size: 17px; margin: 32px 0 8px; }
.sub { color: #52514e; margin: 0 0 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;
         margin: 24px 0; }
.tile { background: #fcfcfb; border: 1px solid rgba(11,11,11,0.10); border-radius: 8px;
        padding: 14px 16px; }
.tile .v { font-size: 26px; font-weight: 600; }
.tile .l { color: #52514e; font-size: 14px; }
figure { margin: 0; background: #fcfcfb; border: 1px solid rgba(11,11,11,0.10);
         border-radius: 8px; padding: 12px; }
figure img { width: 100%; height: auto; display: block; }
p.take { margin: 10px 2px 6px; }
details { margin: 4px 2px 0; color: #52514e; font-size: 14px; }
table { border-collapse: collapse; margin-top: 8px; font-variant-numeric: tabular-nums;
        display: block; overflow-x: auto; }
td, th { padding: 3px 10px; border-bottom: 1px solid #e1e0d9; text-align: right; white-space: nowrap; }
th { color: #0b0b0b; }
footer { margin-top: 48px; color: #898781; font-size: 13px; }
"""


def _table_html(df: pl.DataFrame, max_rows: int = 60) -> str:
    def cell(v) -> str:
        if isinstance(v, float):
            return f"{v:.4g}"
        return html.escape(str(v))

    head = "".join(f"<th>{html.escape(c)}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>"
                   for row in df.head(max_rows).iter_rows())
    more = f"<p>{df.height - max_rows} more rows in the parquet table.</p>" if df.height > max_rows else ""
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def _exhibit_html(e: Exhibit) -> str:
    data = base64.b64encode(e.path.read_bytes()).decode()
    return (f'<h3 id="{e.slug}">{html.escape(e.title)}</h3>'
            f'<figure><img alt="{html.escape(e.title)}" src="data:image/png;base64,{data}"></figure>'
            f'<p class="take">{html.escape(e.takeaway)}</p>'
            f"<details><summary>Table view ({e.path.name})</summary>{_table_html(e.table)}</details>")


def _tiles(cfg: Settings) -> str:
    fits = pl.read_parquet(cfg.tables_dir / "clock_fits.parquet")
    tue = fits.filter((pl.col("symbol") == "NIFTY") & (pl.col("label") == "Tue regime")
                      & (pl.col("half") == "full") & (pl.col("spec") == "weekday")
                      & (pl.col("parameter") == "Tue")).row(0, named=True)
    uq = pl.read_parquet(cfg.tables_dir / "clock_uncertainty_summary.parquet").filter(
        (pl.col("regime") == "Tue regime") & (pl.col("quantity") == "open_Mon")).row(0, named=True)
    lim = pl.read_parquet(cfg.tables_dir / "hedging_limit.parquet")
    floor = lim.filter((pl.col("name") == "floor") & pl.col("method").str.starts_with("closed"))["value"].item()
    m_star = lim.filter((pl.col("name") == "m_star_clock")
                        & pl.col("method").str.contains("closed"))["value"].item()
    perm = pl.read_parquet(cfg.tables_dir / "permutation.parquet").filter(pl.col("h") == 63)
    null = perm.filter(~pl.col("is_event")).drop_nans("s_nifty")["s_nifty"]
    star = perm.filter(pl.col("is_event"))["s_nifty"].item()
    p = (1 + int((null <= star).sum())) / (1 + null.len())
    tiles = (
        (f"{tue['point']:.2f}", f"Nifty expiry-session variance weight (average session = 1), "
         f"95% CI [{tue['lo']:.2f}, {tue['hi']:.2f}]"),
        (f"{100 * uq['point']:.1f}%", f"price error of a one-session straddle for a calendar-time "
         f"trader, band [{100 * uq['lo']:.0f}%, {100 * uq['hi']:.0f}%]"),
        (f"{floor:.1f} pts", f"clock risk left after continuous hedging; exceeds discretisation "
         f"error beyond {m_star:.0f} hedges per session"),
        (f"p = {p:.2f}", "pre-registered randomization test of the Sep-2025 rule change: inside "
         "the null's middle 90%, so no causal claim"),
    )
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="v">{html.escape(v)}</div><div class="l">{html.escape(l)}</div></div>'
        for v, l in tiles) + "</div>"


def build(cfg: Settings = settings) -> None:
    parts = [
        "<h1>Ghadi: the variance clock of Indian index options</h1>",
        '<p class="sub">Variance does not accrue evenly through the week. This measures the shape '
        "of that accumulation in Nifty and Sensex options across the 2024 and 2025 SEBI "
        "expiry-day changes, and prices what it costs to assume it is flat.</p>",
        _tiles(cfg),
    ]
    for title, sub, functions in SECTIONS:
        parts.append(f"<h2>{html.escape(title)}</h2><p class=\"sub\">{html.escape(sub)}</p>")
        for fn in functions:
            exhibit = fn(cfg)
            logging.info("figure %s", exhibit.path.name)
            parts.append(_exhibit_html(exhibit))
    parts.append(f"<footer>Built from commit {git_commit() or 'unknown'}. Every number above is "
                 "read from results/tables; DECISIONS.md records how each was produced and what "
                 "it does not show.</footer>")
    page = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Ghadi: variance clock report</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>")
    path = cfg.results_root / "report.html"
    path.write_text(page, encoding="utf-8")
    logging.info("wrote %s (%.1f MB)", path, path.stat().st_size / 1e6)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build()


if __name__ == "__main__":
    main()

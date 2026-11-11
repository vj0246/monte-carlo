"""One visual system for every figure (D-21: the folder is the deliverable, so the figures are too).

The rules, so the next figure follows them without re-deciding:

- Colour carries identity only, in a fixed slot order, never cycled: slot 1 (blue) is always the
  clock or the quantity under study, slot 2 (orange) its comparison (calendar time, the naive
  reading), slot 3 (aqua) a third series. Muted grey is reference geometry (the null line, a
  theoretical rate). Colour never encodes rank.
- One y-scale per panel. Quantities on different scales get their own panel, never a twin axis.
- Multiplicative quantities (clock weights, standard deviations across rebalancing grids) sit on
  log axes, so a halving and a doubling look the same size.
- Every estimate carries its interval. A fit flagged unidentified is drawn hollow, not dropped, so
  the reader sees it exists and that it is not evidence.
- Hairline grid, no top/right spines, no dashed rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
#: Sequential blue ramp, light to dark (heatmaps).
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


@dataclass
class Exhibit:
    """A figure plus what it says and the numbers behind it (the report's table view)."""

    slug: str
    title: str
    takeaway: str
    path: Path
    table: pl.DataFrame


def setup() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
        "font.size": 10,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "lines.linewidth": 2.0,
        "lines.markersize": 7,
    })


def figure(ncols: int = 1, nrows: int = 1, width: float = 9.0, height: float = 4.2, **kw):
    setup()
    return plt.subplots(nrows, ncols, figsize=(width, height), constrained_layout=True, **kw)


def null_line(ax, y: float = 1.0, label: str | None = None, horizontal: bool = True) -> None:
    """The reference value an estimate is tested against, in muted ink."""
    draw = ax.axhline if horizontal else ax.axvline
    draw(y, color=MUTED, linewidth=1.0, zorder=1)
    if label:  # below the line at the right edge, or at the foot of a vertical line: clear of data
        if horizontal:
            ax.annotate(label, (1.0, y), xycoords=("axes fraction", "data"), xytext=(-4, -11),
                        textcoords="offset points", ha="right", color=MUTED, fontsize=8)
        else:
            ax.annotate(label, (y, 0.0), xycoords=("data", "axes fraction"), xytext=(4, 4),
                        textcoords="offset points", color=MUTED, fontsize=8)


def save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path

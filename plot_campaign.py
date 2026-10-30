#!/usr/bin/env python3
"""Publication figures for the H100 four-engine campaign, drawn from analyze_campaign.py's stats.json.

    plot_campaign.py --stats ANALYSIS_DIR [--cross-host DIR] [--output FIG_DIR] [--only f01 f02 ...]
    plot_campaign.py --cross-host CROSS_DIR --output FIG_DIR     # only F11, from cross_host.json
    plot_campaign.py --synthetic --seed 7 --output OUT     # fake trees -> analysis -> figures, all watermarked

Every figure is written as PDF (vector, fonts embedded), PNG (300 dpi) and SVG, plus figures.json (id, files,
caption draft, data source). Design rules (dataviz skill): one fixed colour and marker per engine in every figure
(palette validated for colour-vision deficiency on a white surface; marker shape and direct labels carry identity
where colour contrast is under 3:1), thin marks, hairline recessive grids, ink text, one axis per panel, ratios on
log axes with a reference line at 1. Synthetic input gets a diagonal "SYNTHETIC — NOT RESULTS" watermark, a header
tag and a footer on every figure.

Figures: f01_forest_<scale> (per-query speedups with CIs), f02_suite_speedup_vs_scale, f03_heatmap_<suite>,
f04_gpu_coverage, f05_variability, f06_order_effects, f07_gpu_memory, f08_polars_version_bridge,
f02x_c1_e1_exploratory (post-hoc E1/E4 beside C1/C4), f09_a100_vs_h100, f10_headline_times, f11_gpu_ladder and f12_gpu_idle_fraction (cross-host; need cross_host.json from analyze_campaign.py).
Every figure has a two-column version (7.0 in) and, where a layout exists, a one-column version (3.4 in, files *_1col).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import colors as mcolors  # noqa: E402
from matplotlib import ticker  # noqa: E402
from matplotlib.legend import Legend  # noqa: E402
from matplotlib.legend_handler import HandlerBase  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from matplotlib.transforms import offset_copy  # noqa: E402
import numpy as np  # noqa: E402

import analyze_campaign as AC  # noqa: E402

WATERMARK = AC.WATERMARK
FORMATS = ("pdf", "png", "svg")
DPI = 300
SINGLE, DOUBLE = 3.4, 7.0  # inches: one and two journal columns

# ---- ink and chrome (palette.md, light surface = white for print)
SURFACE = "#ffffff"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
CRITICAL = "#d03b3b"

# ---- one colour and one marker per engine, in every figure.
# Palette slots blue / green / yellow / magenta validated with scripts/validate_palette.js on #ffffff, --pairs all:
# worst CVD (protan/deutan) dE 13.0, normal-vision dE 19.6. Yellow (2.17:1) and magenta (2.69:1) sit under 3:1 on white,
# so identity is also carried by marker shape, direct labels and the table twins (tables/t*.csv).
# In greyscale the Polars pair (blue, green) is darker than the Spark pair (yellow, magenta).
ENGINE_STYLE = {
    "polars": {"color": "#2a78d6", "marker": "o", "label": "Polars CPU"},
    "polars_gpu": {"color": "#008300", "marker": "D", "label": "Polars GPU"},
    "spark_cpu": {"color": "#eda100", "marker": "s", "label": "Spark CPU"},
    "spark_gpu": {"color": "#e87ba4", "marker": "^", "label": "Spark GPU"},
}
ENGINES = tuple(ENGINE_STYLE)
SUITE_STYLE = {"baseline": {"ls": "-", "filled": True, "label": "baseline suite"},
               "complex": {"ls": (0, (4, 2)), "filled": False, "label": "complex suite"}}
# the engine whose speedup a contrast reports gives its colour; C4 compares against the best of two engines
CONTRAST_AXIS = {
    "c1": ("CPU faster", "GPU faster", "polars_gpu"),
    "c2": ("CPU faster", "GPU faster", "spark_gpu"),
    "c3": ("Spark GPU faster", "Polars GPU faster", "polars_gpu"),
    "c4": ("Polars CPU faster", "best GPU faster", None),
}
CONTRAST_TITLE = {
    "c1": "C1  Polars CPU / Polars GPU",
    "c2": "C2  Spark CPU / Spark GPU",
    "c3": "C3  Spark GPU / Polars GPU",
    "c4": "C4  Polars CPU / best GPU",
}
SUITE_TITLE = {"baseline": "Baseline suite", "complex": "Complex suite"}
# neutral light-to-dark ramp for magnitude (time); no hue, so it is never mistaken for an engine colour
TIME_RAMP = ["#f4f3ef", "#e1dfd7", "#c9c6bb", "#aeaa9d", "#908c80", "#706c62", "#524f48", "#33312d", "#161614"]


class Context:
    """Everything a figure needs: stats, output directory, synthetic flag and the manifest being built."""

    def __init__(self, stats, out_dir, formats=FORMATS, headline_scale=None, cross=None):
        self.stats, self.out_dir, self.formats = stats, Path(out_dir), tuple(formats)
        self.cross = cross   # cross_host.json content (amendments A2/A3) or None
        self.synthetic = bool(stats.get("synthetic") or (cross or {}).get("synthetic"))
        self.headline_scale = headline_scale
        self.manifest = []
        self.out_dir.mkdir(parents=True, exist_ok=True)

    @property
    def host(self):
        """Short name of the host this campaign ran on (H100, B200, ...); the stats written before the host block existed are H100."""
        return (self.stats.get("host") or {}).get("gpu_short") or "H100"

    @property
    def host_line(self):
        h = self.stats.get("host") or {}
        parts = [h.get("gpu") or self.host, f"{h['vcpus']} vCPU" if h.get("vcpus") else None, f"{h['host_ram_gib']:g} GiB" if h.get("host_ram_gib") else None]
        return ", ".join(p for p in parts if p)

    @property
    def suites(self):
        return self.stats["campaign"]["suites"]

    def scales(self, suite):
        return self.stats["campaign"]["scales"][suite]

    @property
    def all_scales(self):
        return sorted({s for suite in self.suites for s in self.scales(suite)}, key=AC.scale_sort_key)

    @property
    def rounds(self):
        return self.stats["campaign"]["n_rounds"]


class HandlerSwatchLine(HandlerBase):
    """Legend swatch for any Line2D handle: the line runs unbroken across the whole swatch and the marker sits on top as a separate artist.

    The stock handler draws a short line with the marker in its middle; the marker's surface-coloured ring then cuts the line in two stubs and a
    solid line reads as dashed ("--o--"). Here a filled marker's ring takes the marker's own colour, so the swatch has exactly the line style of the
    data it stands for. The first artist returned is the line (or the marker when the handle has no line).
    """

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        y = ydescent + height / 2
        artists = []
        if orig_handle.get_linestyle() not in ("None", "none", " ", "") and orig_handle.get_linewidth() > 0 and orig_handle.get_color() not in ("none", "None", None):
            artists.append(Line2D([xdescent, xdescent + width], [y, y], color=orig_handle.get_color(), lw=orig_handle.get_linewidth(), ls=orig_handle.get_linestyle(),
                                  alpha=orig_handle.get_alpha(), solid_capstyle="butt", dash_capstyle="butt", transform=trans))
        if orig_handle.get_marker() not in (None, "None", "none", "", " "):
            face, edge = orig_handle.get_markerfacecolor(), orig_handle.get_markeredgecolor()
            size, ring = orig_handle.get_markersize(), orig_handle.get_markeredgewidth()
            if face not in ("none", None, SURFACE) and mcolors.same_color(edge, SURFACE):
                edge, size = face, max(size - ring, 2.0)       # no white ring (it would cut the line); the shrink keeps the visible marker size
            artists.append(Line2D([xdescent + width / 2], [y], marker=orig_handle.get_marker(), ms=size, mfc=face, mec=edge,
                                  mew=ring, ls="none", alpha=orig_handle.get_alpha(), transform=trans))
        return artists


def configure_matplotlib():
    Legend.update_default_handler_map({Line2D: HandlerSwatchLine()})
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 6.6, "axes.titlesize": 7.2, "axes.labelsize": 6.8,
        "xtick.labelsize": 6.2, "ytick.labelsize": 6.2, "legend.fontsize": 6.4, "figure.titlesize": 8,
        "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "axes.edgecolor": AXIS,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5,
        "ytick.major.size": 2.5, "xtick.minor.size": 1.5, "ytick.minor.size": 1.5, "xtick.minor.width": 0.4,
        "ytick.minor.width": 0.4, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": False,
        "grid.color": GRID, "grid.linewidth": 0.5, "grid.linestyle": "-", "axes.axisbelow": True,
        "lines.linewidth": 1.2, "lines.markersize": 4.2, "legend.frameon": False,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "path", "savefig.dpi": DPI,
        "axes.unicode_minus": True,
    })


# ------------------------------------------------------------------ helpers

def scale_label(name):
    return name.upper()


def scale_number(name):
    return float(AC.scale_value(name))


def style_axes(ax, grid="y", log_x=False, log_y=False):
    ax.set_axisbelow(True)
    if grid in ("y", "both"):
        ax.yaxis.grid(True)
    if grid in ("x", "both"):
        ax.xaxis.grid(True)
    ax.tick_params(which="both", direction="out")
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    if log_x:
        ax.xaxis.set_minor_formatter(ticker.NullFormatter())
    if log_y:
        ax.yaxis.set_minor_formatter(ticker.NullFormatter())


RATIO_TICKS = (  # (largest hi/lo span, candidate ticks): roughly evenly spaced on a log axis, about 4-7 labels
    (1.45, (0.8, 0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4)),
    (2.6, (0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.4, 1.7, 2, 2.5)),
    (5.5, (0.25, 0.3, 0.4, 0.5, 0.7, 1, 1.5, 2, 3, 4, 5, 7)),
    (math.inf, (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100)),
)


def ratio_ticks(ax, axis="x", lo=None, hi=None):
    """Readable log ticks for ratios (0.1 0.2 0.5 1 2 5 ...), denser for narrow ranges, always including 1 when it is in range."""
    lo = lo if lo is not None else (ax.get_xlim() if axis == "x" else ax.get_ylim())[0]
    hi = hi if hi is not None else (ax.get_xlim() if axis == "x" else ax.get_ylim())[1]
    candidates = next(c for span, c in RATIO_TICKS if hi / lo <= span)
    ticks = [t for t in candidates if lo * 0.999 <= t <= hi * 1.001]
    axis_obj = ax.xaxis if axis == "x" else ax.yaxis
    axis_obj.set_major_locator(ticker.FixedLocator(ticks))
    axis_obj.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    axis_obj.set_minor_locator(ticker.NullLocator())


def reference_line(ax, axis="x", value=1.0):
    (ax.axvline if axis == "x" else ax.axhline)(value, color=INK2, lw=0.8, zorder=2)


def engine_handles(engines, with_line=False, marker_size=5.2, outline=False):
    """Legend proxies for the engines; outline=True gives each marker the dark outline of its engine colour (F10)."""
    out = []
    for e in engines:
        s = ENGINE_STYLE[e]
        out.append(Line2D([0], [0], marker=s["marker"], color=s["color"], markerfacecolor=s["color"],
                          markeredgecolor=mix(s["color"], "#000000", 0.45) if outline else SURFACE,
                          markeredgewidth=0.8 if outline else 0.6, markersize=marker_size * (1.2 if s["marker"] == "^" else 1.0), lw=1.2 if with_line else 0, label=s["label"]))
    return out


def padded_limits(values, factor=1.12, floor=None, ceiling=None):
    values = [v for v in values if v is not None and v > 0]
    lo, hi = (min(values), max(values)) if values else (0.5, 2.0)
    lo, hi = lo / factor, hi * factor
    if floor is not None:
        lo = max(lo, floor)
    if ceiling is not None:
        hi = min(hi, ceiling)
    return lo, hi


def compact_seconds(value):
    if value is None:
        return ""
    if value >= 1000:
        return f"{value / 1000:.1f}k" if value < 10000 else f"{value / 1000:.0f}k"
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.0f}" if value >= 99.5 else f"{value:.1f}".rstrip("0").rstrip(".")
    if value >= 1:
        return f"{value:.1f}"
    return f"{value:.2f}" if value >= 0.1 else f"{value:.2f}"


def mix(color, other, amount):
    a, b = np.array(mcolors.to_rgb(color)), np.array(mcolors.to_rgb(other))
    return tuple(a * (1 - amount) + b * amount)


def text_color_on(fill):
    r, g, b = mcolors.to_rgb(fill)
    luminance = 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2
    return "#ffffff" if luminance < 0.18 else INK


def add_watermark(fig, ctx):
    """Visible on every synthetic figure: diagonal mark, header tag and footer."""
    if not ctx.synthetic:
        return
    fig.text(0.5, 0.5, WATERMARK, ha="center", va="center", rotation=22, fontsize=fig.get_figwidth() * 3.0,
             fontweight="bold", color=CRITICAL, alpha=0.17, zorder=1000, transform=fig.transFigure)
    fig.text(0.004, 0.995, WATERMARK, ha="left", va="top", fontsize=6.4, fontweight="bold", color=CRITICAL, zorder=1000)


def finish(fig, ctx, fig_id, title, caption, sources, width_class="double", alt=""):
    """Save all formats, record the manifest entry, close the figure."""
    add_watermark(fig, ctx)
    files = {}
    for fmt in ctx.formats:
        path = ctx.out_dir / f"{fig_id}.{fmt}"
        kwargs = {"dpi": DPI} if fmt == "png" else {}
        if fmt == "pdf":
            kwargs["metadata"] = {"Title": title + (f" [{WATERMARK}]" if ctx.synthetic else ""), "Creator": "plot_campaign.py"}
        fig.savefig(path, format=fmt, **kwargs)
        files[fmt] = path.name
    width, height = fig.get_size_inches()
    ctx.manifest.append({
        "id": fig_id, "title": title, "files": files, "width_class": width_class,
        "size_inches": [round(float(width), 2), round(float(height), 2)],
        "caption_draft": (f"[{WATERMARK}] " if ctx.synthetic else "") + caption,
        "alt_text": alt or title, "data_source": sources, "synthetic": ctx.synthetic,
        "table_twins": [s for s in sources if s.startswith("tables/")],
    })
    plt.close(fig)


def suite_panels(ctx, ncols_per_suite=1):
    return [(s, ctx.scales(s)) for s in ctx.suites]


def get_estimate(stats, contrast, suite, scale):
    return stats.get(contrast, {}).get(suite, {}).get(scale)


# ------------------------------------------------------------------ shared panel furniture

def wrap_title(text, width_in):
    import textwrap
    return textwrap.wrap(text, width=max(24, int((width_in - 0.2) / 0.066))) or [text]


def figure_title(fig, text, left=0.01):
    """Bold figure title just below the synthetic header tag; wraps to the figure width."""
    lines = wrap_title(text, fig.get_figwidth())
    fig.text(left, 1 - 0.17 / fig.get_figheight(), "\n".join(lines), fontsize=7.6, fontweight="bold", color=INK, va="top", ha="left",
             linespacing=1.15)


class Panels:
    """Inch-based panel grid: a single row at two-column width, or a stacked/gridded layout at one-column width."""

    def __init__(self, single, n, panel_h, title, *, legend_rows=1, extra_bottom=0.0, grid=None, ratios=None, left=0.58, right=0.10,
                 gap=0.14, hgap=0.82, sharey=True):
        self.single, self.n, self.sharey = single, n, sharey
        width = SINGLE if single else DOUBLE
        cols = (grid or 1) if single else n
        rows = math.ceil(n / cols)
        title_lines = len(wrap_title(title, width))
        self.top = 0.25 + 0.135 * title_lines + 0.42
        self.bottom = 0.50 + 0.19 * legend_rows + extra_bottom
        height = self.top + self.bottom + rows * panel_h + (rows - 1) * hgap
        self.fig = fig = plt.figure(figsize=(width, height))
        ratios = ratios if (ratios and not single) else [1] * cols
        usable = width - left - right - gap * (cols - 1)
        widths = [usable * r / sum(ratios) for r in ratios]
        self.axes = []
        for i in range(n):
            r, c = divmod(i, cols)
            x0 = left + sum(widths[:c]) + gap * c
            y0 = self.bottom + (rows - 1 - r) * (panel_h + hgap)
            ax = fig.add_axes([x0 / width, y0 / height, widths[c] / width, panel_h / height], sharey=self.axes[0] if (sharey and self.axes) else None)
            self.axes.append(ax)
            if sharey and c > 0:
                ax.tick_params(axis="y", labelleft=False, length=0)
        self.rows, self.cols, self.width, self.height, self.left = rows, cols, width, height, left
        self.panel_h, self.hgap = panel_h, hgap
        figure_title(fig, title)

    def ylabel(self, text):
        for i, ax in enumerate(self.axes):
            if i % self.cols == 0:
                ax.set_ylabel(text)

    def xlabel(self, text, y_in):
        """One shared x label centred under the panels, y_in inches from the bottom."""
        self.fig.text((self.left + (self.width - self.left - 0.1) / 2) / self.width, y_in / self.height, text, fontsize=6.8, color=INK2, ha="center")

    def legend(self, handles, ncol_double=5, ncol_single=2, y_in=0.0, handlelength=2.0, handler_map=None):
        ncol = ncol_single if self.single else ncol_double
        self.fig.legend(handles=handles, loc="lower center", ncol=ncol, bbox_to_anchor=(0.5, y_in / self.height), columnspacing=1.4, handlelength=handlelength,
                        handler_map=handler_map)
        return self.fig

    def tag(self):
        return "_1col" if self.single else ""

    def width_class(self):
        return "single" if self.single else "double"


def panel_title(ax, code, text, y=0.0):
    """Two-line panel title above the axes (offsets in points, so it hugs the panel at any height)."""
    for line, size, weight, color, dy in ((code, 7.0, "bold", INK, 11.5 + y), (text, 6.2, "normal", INK2, 3.0 + y)):
        ax.annotate(line, xy=(0.0, 1.0), xycoords="axes fraction", xytext=(0, dy), textcoords="offset points", fontsize=size,
                    fontweight=weight, color=color, va="bottom", ha="left", annotation_clip=False)


def direction_labels(ax, left, right, offset=-17.0, size=5.5):
    """Which side is faster, written under the x tick labels: left end and right end of the panel."""
    for x, text, ha in ((0.0, f"← {left}", "left"), (1.0, f"{right} →", "right")):
        ax.annotate(text, xy=(x, 0.0), xycoords="axes fraction", xytext=(0, offset), textcoords="offset points",
                    fontsize=size, color=INK2, ha=ha, va="top", annotation_clip=False)


def with_parity(lo, hi, pad=1.12):
    """Axis limits that always show the ratio 1."""
    return min(lo, 1 / 1.25), max(hi, 1.25 * pad)


# ------------------------------------------------------------------ F2: suite speedup vs scale

def contrast_color(contrast):
    subject = CONTRAST_AXIS[contrast][2]
    return ENGINE_STYLE[subject]["color"] if subject else INK


CONTRAST_SHORT = {"c1": ("C1", "Polars CPU / Polars GPU"), "c2": ("C2", "Spark CPU / Spark GPU"),
                  "c3": ("C3", "Spark GPU / Polars GPU"), "c4": ("C4", "Polars CPU / best GPU")}
CONTRAST_ENDS = {"c1": ("CPU", "GPU"), "c2": ("CPU", "GPU"), "c3": ("Spark GPU", "Polars GPU"), "c4": ("Polars CPU", "best GPU")}


def legend_xlabel_y(rows):
    return 0.19 * rows + 0.09


class SuiteKey:
    """Legend proxy for a suite's line style in F2 (drawn by HandlerSuiteKey from SUITE_STYLE, the same table the data lines use)."""

    def __init__(self, suite, label):
        self.suite, self._label = suite, label

    def get_label(self):
        return self._label


class HandlerSuiteKey(HandlerBase):
    """A long swatch: the suite's own dash pattern across the whole handle, the marker (filled or hollow) on top at its centre.

    A short Line2D handle with a marker in the middle cannot show a dash pattern (the marker covers the only gap), so the line and the marker are
    separate artists. The first artist returned is the line, whose style the figure tests compare with the plotted lines.
    """

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        style = SUITE_STYLE[orig_handle.suite]
        y = ydescent + height / 2
        line = Line2D([xdescent, xdescent + width], [y, y], color=INK, lw=1.2, ls=style["ls"], solid_capstyle="butt", transform=trans)
        marker = Line2D([xdescent + width / 2], [y], marker="o", ms=4.4, mfc=INK if style["filled"] else SURFACE, mec=INK, mew=1.0, ls="none", transform=trans)
        return [line, marker]


def fig_suite_speedup(ctx, single=False):
    stats = ctx.stats
    contrasts = ("c1", "c2", "c3", "c4")
    title = "Suite-level speedup by scale (geometric mean over queries per round; 95% t-interval across rounds)"
    legend_rows = 3 if single else 1
    P = Panels(single, 4, 1.50 if single else 1.65, title, legend_rows=legend_rows, extra_bottom=0.16, grid=2, hgap=0.86 if single else 0.8)
    axes = P.axes
    values = [stats[c][s][sc][k] for c in contrasts for s in ctx.suites for sc in ctx.scales(s)
              for k in ("ci_low", "ci_high", "estimate") if stats[c][s][sc][k]]
    lo, hi = with_parity(*padded_limits(values, 1.15))
    xs_all = [scale_number(s) for s in ctx.all_scales]
    for ax, c in zip(axes, contrasts):
        color = contrast_color(c)
        ax.set_xscale("log")
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_x=True, log_y=True)
        reference_line(ax, "y")
        for suite in ctx.suites:
            style = SUITE_STYLE[suite]
            points = [(scale_number(sc), stats[c][suite][sc]) for sc in ctx.scales(suite)]
            points = [(x, r) for x, r in points if r["estimate"] is not None]
            if not points:
                continue
            x = np.array([p[0] for p in points])
            est = np.array([p[1]["estimate"] for p in points])
            if all(p[1]["ci_low"] is not None for p in points):
                ax.fill_between(x, [p[1]["ci_low"] for p in points], [p[1]["ci_high"] for p in points], color=color,
                                alpha=0.18 if style["filled"] else 0.10, lw=0, zorder=2)
            ax.plot(x, est, ls=style["ls"], color=color, lw=1.2, zorder=3)
            ax.plot(x, est, ls="none", marker="o", ms=4.4, mfc=color if style["filled"] else SURFACE, mec=color if not style["filled"] else SURFACE,
                    mew=1.0 if not style["filled"] else 0.8, zorder=4)
        ax.set_xlim(min(xs_all) / 1.35, max(xs_all) * 1.35)
        ax.set_ylim(lo, hi)
        ax.xaxis.set_major_locator(ticker.FixedLocator(xs_all))
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ratio_ticks(ax, "y", lo, hi)
        panel_title(ax, *CONTRAST_SHORT[c])
        direction_labels(ax, *CONTRAST_ENDS[c], offset=-15.0)
        if c == "c4":
            x_c = stats["crossover"]["c4"]
            notes = []
            for suite in ctx.suites:
                first = x_c[suite]["first_gpu_faster_scale"]
                notes.append(f"{suite}: CI > 1 from {scale_label(first)}" if first else f"{suite}: CI > 1 at no scale")
            ax.annotate("\n".join(notes), xy=(0.98, 0.04), xycoords="axes fraction", ha="right", va="bottom", fontsize=5.5, color=INK2)
    P.ylabel("Speedup  t_ref / t_engine (log)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(legend_rows))
    P.legend([SuiteKey("baseline", "baseline suite (filled, solid)"), SuiteKey("complex", "complex suite (hollow, dashed)"),
              Patch(facecolor=INK, alpha=0.18, label=f"95% CI over rounds (df = {stats['campaign']['df']})")], ncol_double=3, ncol_single=1,
             handlelength=3.6, handler_map={SuiteKey: HandlerSuiteKey()})
    finish(P.fig, ctx, "f02_suite_speedup_vs_scale" + P.tag(), "Suite-level speedup versus scale factor",
           "Suite-level speedup (geometric mean of per-query ratios t_ref / t_engine, per round) against TPC-H-derived scale factor, one panel per "
           "primary contrast. Points are geometric means over rounds, bands are 95% t-intervals across rounds (df = rounds − 1), and the horizontal "
           "line marks parity. Colour is the engine whose speedup is shown (C4: the better of the two GPU engines per query, drawn in ink). "
           "Queries unsupported on Polars GPU are excluded from C1 and C3 and counted in the coverage figure.",
           ["c1.*.*", "c2.*.*", "c3.*.*", "c4.*.*", "crossover.c4.*", "tables/t01_suite_contrasts.csv"], width_class=P.width_class(),
           alt="Four panels of speedup versus scale factor for Polars GPU, Spark GPU, Spark GPU over Polars GPU, and best GPU over Polars CPU.")


# ------------------------------------------------------------------ F2x: exploratory E1 / E4 beside C1 / C4

def fig_exploratory(ctx, single=False):
    import textwrap
    stats = ctx.stats
    expl = stats.get("exploratory") or {}
    if not expl.get("e1", {}).get("meta", {}).get("available") or not any(expl["e1"].get(s) for s in ctx.suites):
        return "no exploratory analysis (needs a side study with two Polars versions)"
    label = expl["e1"]["meta"]["label"]
    version = expl["e1"]["meta"]["cpu_version"]
    panels = [("c1", "C1", "Polars CPU 1.44.2 / Polars GPU", stats["c1"], False), ("e1", "E1  EXPLORATORY", f"Polars CPU {version} / Polars GPU", expl["e1"], True),
              ("c4", "C4", "Polars CPU 1.44.2 / best GPU", stats["c4"], False), ("e4", "E4  EXPLORATORY", f"Polars CPU {version} / best GPU", expl["e4"], True)]
    title = "Locked-design C1 and C4 beside the EXPLORATORY E1 and E4 (post-hoc; not in the locked design §9): suite-level speedup by scale"
    note = (f"E1 and E4 are {label}: the CPU side is the {version} side-study sessions and the GPU side the main rounds, unpaired sessions from different rounds, so their bands are Welch-type "
            "95% intervals on the log scale (df from Welch-Satterthwaite). C1 and C4 are the locked-design contrasts with Polars 1.44.2 on the CPU side (t-interval over rounds, "
            "df = rounds − 1). E1 and E4 are not used by the crossover verdict or any other primary output.")
    lines = textwrap.wrap(note, int(((SINGLE if single else DOUBLE) - 0.2) / 0.052))
    foot_h = 0.115 * len(lines) + 0.10
    legend_rows = 3 if single else 1
    P = Panels(single, 4, 1.50 if single else 1.65, title, legend_rows=legend_rows, extra_bottom=0.16 + foot_h, grid=2, hgap=0.86 if single else 0.8)
    values = [r[k] for _, _, _, block, _ in panels for s in ctx.suites for sc in ctx.scales(s) for r in [block.get(s, {}).get(sc)] if r
              for k in ("ci_low", "ci_high", "estimate") if r.get(k)]
    lo, hi = with_parity(*padded_limits(values, 1.15))
    xs_all = [scale_number(s) for s in ctx.all_scales]
    for ax, (key, code, text, block, exploratory) in zip(P.axes, panels):
        color = contrast_color("c1" if key.endswith("1") else "c4")
        ax.set_xscale("log")
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_x=True, log_y=True)
        reference_line(ax, "y")
        for suite in ctx.suites:
            style = SUITE_STYLE[suite]
            points = [(scale_number(sc), block[suite][sc]) for sc in ctx.scales(suite) if sc in block.get(suite, {})]
            points = [(x, r) for x, r in points if r["estimate"] is not None]
            if not points:
                continue
            x = np.array([p[0] for p in points])
            est = np.array([p[1]["estimate"] for p in points])
            if all(p[1]["ci_low"] is not None for p in points):
                ax.fill_between(x, [p[1]["ci_low"] for p in points], [p[1]["ci_high"] for p in points], color=color,
                                alpha=0.18 if style["filled"] else 0.10, lw=0, zorder=2, hatch="////" if exploratory else None, edgecolor=color if exploratory else None)
            ax.plot(x, est, ls=style["ls"], color=color, lw=1.2, zorder=3)
            ax.plot(x, est, ls="none", marker="o" if not exploratory else "s", ms=4.4, mfc=color if style["filled"] else SURFACE,
                    mec=color if not style["filled"] else SURFACE, mew=1.0 if not style["filled"] else 0.8, zorder=4)
        ax.set_xlim(min(xs_all) / 1.35, max(xs_all) * 1.35)
        ax.set_ylim(lo, hi)
        ax.xaxis.set_major_locator(ticker.FixedLocator(xs_all))
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ratio_ticks(ax, "y", lo, hi)
        panel_title(ax, code, text)
        direction_labels(ax, *CONTRAST_ENDS["c1" if key.endswith("1") else "c4"], offset=-15.0)
        if exploratory:
            for spine in ax.spines.values():
                spine.set_linestyle((0, (3, 2)))
            ax.annotate("EXPLORATORY\npost-hoc", xy=(0.97, 0.05), xycoords="axes fraction", ha="right", va="bottom", fontsize=5.8, fontweight="bold", color=INK2, alpha=0.8)
    P.ylabel("Speedup  t_CPU / t_GPU (log)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(legend_rows) + foot_h)
    P.legend([SuiteKey("baseline", "baseline suite (filled, solid)"), SuiteKey("complex", "complex suite (hollow, dashed)"),
              Patch(facecolor=INK, alpha=0.18, label="95% interval (C: t over rounds; E: Welch-type, hatched)")], ncol_double=3, ncol_single=1,
             handlelength=3.6, handler_map={SuiteKey: HandlerSuiteKey()}, y_in=foot_h)
    P.fig.text(0.012, 0.03 / P.height, "\n".join(lines), ha="left", va="bottom", fontsize=5.6, color=INK2, linespacing=1.25)
    finish(P.fig, ctx, "f02x_c1_e1_exploratory" + P.tag(), "C1 and C4 beside the exploratory E1 and E4 (post-hoc)",
           f"Suite-level speedup (CPU time over GPU time; above 1 the GPU side was faster) against scale factor. Panels C1 and C4 are the locked-design contrasts, whose CPU side is Polars 1.44.2. "
           f"Panels E1 and E4 (dashed frame, hatched bands, square markers) are {label}: the CPU side is Polars {version} from the side-study sessions and the GPU side is the main rounds, "
           "unpaired, so their bands are Welch-type 95% intervals on the log scale; E1 uses Polars GPU, E4 the better GPU engine per query and round. "
           "E1 and E4 are never used by the crossover verdict or any other primary output.",
           ["c1.*.*", "c4.*.*", "exploratory.e1.*.*", "exploratory.e4.*.*", "exploratory.e1.meta", "tables/t23_exploratory.csv"], width_class=P.width_class(),
           alt="Four panels of CPU over GPU speedup against scale factor: locked-design C1 and C4 beside the exploratory E1 and E4 with Polars 2.0.0 on the CPU side.")


# ------------------------------------------------------------------ F1: forest plots

FAILURE_NOTE = {"unsupported_operation": ("✗", "not supported"), "out_of_memory": ("OOM", "out of memory"), "error": ("!", "other error (integrity)"),
                "timeout": ("!", "timeout (integrity)"), "session_failed": ("!", "session failed (integrity)"),
                "invalid": ("!", "invalid session (integrity)"), "unstable_coverage": ("◐", "unstable coverage")}   # short: they must fit one panel


def failure_note(record):
    """In-panel text for a contrast that has no estimate: the same glyph vocabulary as F3 and F4."""
    blocked = record["blocked_by"][0] if record["blocked_by"] else None
    if blocked is None:
        return "– " + record["status"].replace("_", " ")
    glyph, text = FAILURE_NOTE.get(blocked["reason_class"], ("!", blocked["reason_class"].replace("_", " ")))
    return f"{glyph} {text}" + (" on GPU" if blocked["reason_class"] == "unsupported_operation" and blocked["engine"] in AC.GPU_ENGINES else "")


def best_engine(record):
    counts = record.get("best_engine_rounds") or {}
    return max(counts, key=counts.get) if counts else None


def fig_forest(ctx, scale):
    stats = ctx.stats
    contrasts = ("c1", "c2", "c3", "c4")
    suites = [s for s in ctx.suites if scale in ctx.scales(s)]
    if not suites:
        return
    rows = {s: len(stats["campaign"]["queries"][s]) + 1 for s in suites}  # queries + suite geomean row
    row_h = 0.165
    panel_h = {s: row_h * (rows[s] + 1.6) for s in suites}
    gap = 0.34
    top_margin, bottom_margin = 0.78, 0.92
    height = top_margin + bottom_margin + sum(panel_h.values()) + gap * (len(suites) - 1)
    fig = plt.figure(figsize=(DOUBLE, height))
    left, right = 0.135, 0.995
    wspace = 0.075
    pw = (right - left - 3 * wspace) / 4
    values = []
    for c in contrasts:
        for s in suites:
            record = stats[c][s][scale]
            for r in [record, *record["q"].values()]:
                values += [r.get(k) for k in ("ci_low", "ci_high", "min", "max")]
    lo, hi = with_parity(*padded_limits(values, 1.15))
    y_cursor = height - top_margin
    for si, suite in enumerate(suites):
        queries = stats["campaign"]["queries"][suite]
        n = len(queries)
        y_cursor -= panel_h[suite]
        for ci, c in enumerate(contrasts):
            ax = fig.add_axes([left + ci * (pw + wspace), y_cursor / height, pw, panel_h[suite] / height])
            ax.set_xscale("log")
            style_axes(ax, grid="x", log_x=True)
            reference_line(ax, "x")
            record = stats[c][suite][scale]
            color = contrast_color(c)
            y_of = {q: n - i + 0.6 for i, q in enumerate(queries)}
            for q in queries:
                r, y = record["q"][q], y_of[q]
                if r["status"] in ("ok", "partial") and r["estimate"] is not None:
                    pc, marker = color, ENGINE_STYLE[CONTRAST_AXIS[c][2]]["marker"] if CONTRAST_AXIS[c][2] else "o"
                    if c == "c4":
                        engine = best_engine(r)
                        pc, marker = (ENGINE_STYLE[engine]["color"], ENGINE_STYLE[engine]["marker"]) if engine else (INK, "o")
                    ax.plot([r["min"], r["max"]], [y, y], color=INK2, lw=0.5, alpha=0.6, zorder=2, solid_capstyle="butt")
                    for edge in (r["min"], r["max"]):
                        ax.plot([edge, edge], [y - 0.16, y + 0.16], color=INK2, lw=0.5, alpha=0.6, zorder=2)
                    if r["ci_low"] is not None:
                        ax.plot([r["ci_low"], r["ci_high"]], [y, y], color=pc, lw=1.6, zorder=3, solid_capstyle="butt")
                    ax.plot([r["estimate"]], [y], marker=marker, ms=4.4, mfc=pc, mec=SURFACE, mew=0.7, ls="none", zorder=4)
                else:
                    ax.text(0.5, y, failure_note(r), ha="center", va="center", fontsize=5.5, color=INK2, zorder=3,
                            transform=ax.get_yaxis_transform(), bbox={"fc": SURFACE, "ec": "none", "pad": 0.5})
            ax.axhline(0.95, color=AXIS, lw=0.5, zorder=1)
            if record["estimate"] is not None:
                ax.plot([record["min"], record["max"]], [0, 0], color=INK2, lw=0.5, alpha=0.6, zorder=2)
                ax.plot([record["ci_low"], record["ci_high"]], [0, 0], color=color, lw=2.2, zorder=3, solid_capstyle="butt")
                ax.plot([record["estimate"]], [0], marker="P", ms=5.6, mfc=color, mec=SURFACE, mew=0.8, ls="none", zorder=4)
            ax.set_ylim(-0.7, n + 1.1)
            ax.set_xlim(lo, hi)
            ratio_ticks(ax, "x", lo, hi)
            if ci == 0:
                ax.set_yticks([*y_of.values(), 0])
                ax.set_yticklabels([*y_of.keys(), f"suite ({record['n_queries']}/{n})"])
                ax.get_yticklabels()[-1].set_fontweight("bold")
            else:
                ax.set_yticks([])
                ax.spines["left"].set_visible(False)
            if si == 0:
                panel_title(ax, *CONTRAST_SHORT[c])
            if si == len(suites) - 1:
                direction_labels(ax, *CONTRAST_ENDS[c], offset=-15.0)
        fig.text(0.004, (y_cursor + panel_h[suite]) / height + 0.0, SUITE_TITLE[suite], fontsize=7.2, fontweight="bold", va="bottom", ha="left")
        y_cursor -= gap
    fig.text(0.5, 0.50 / height, "Speedup  t_ref / t_engine  (log scale)", fontsize=6.8, color=INK2, ha="center", va="center")
    handles = [Line2D([0], [0], color=INK, lw=1.6, label="95% CI over rounds"),
               Line2D([0], [0], color=INK2, lw=0.6, alpha=0.8, marker="|", ms=5, label="min–max over rounds"),
               Line2D([0], [0], color=INK, lw=0, marker="P", ms=5.4, mec=SURFACE, label="suite geometric mean"),
               *engine_handles(("polars_gpu", "spark_gpu"))]
    fig.legend(handles=handles, loc="lower center", ncol=5, bbox_to_anchor=(0.5, 0.0), columnspacing=1.4, handlelength=1.8)
    figure_title(fig, f"Per-query speedup at {scale_label(scale)} (geometric mean over rounds; 95% t-interval, df = {stats['campaign']['df']})")
    finish(fig, ctx, f"f01_forest_{scale}", f"Per-query speedups at {scale_label(scale)}",
           f"Per-query speedup t_ref / t_engine at {scale_label(scale)} for the four primary contrasts. Dots are geometric means over rounds, thick bars the 95% "
           f"t-interval on the mean log-ratio (df = rounds − 1), thin bars the minimum and maximum round. The bottom row of each panel is the suite "
           "geometric mean over the queries available for both sides. Marker shape and colour name the engine whose speedup is shown (C4: the better GPU "
           "engine in most rounds). Queries an engine cannot complete are written out in the panel (✗ not supported and OOM out of memory are coverage outcomes; ◐ unstable coverage, a coverage outcome in some rounds only, is excluded from every estimate by A1; ! is an integrity failure that should not occur in accepted rounds) and are excluded from the suite row.",
           [f"c1..c4.<suite>.{scale}.q.*", f"c1..c4.<suite>.{scale}", "tables/t02_query_contrasts.csv", "tables/t01_suite_contrasts.csv"],
           alt=f"Forest plots of per-query speedups at {scale_label(scale)} for each contrast and suite.")


# ------------------------------------------------------------------ F3: heatmaps of median time

def engine_title(ax, engine, size=7.0, dy=4.0):
    """Engine name over a panel with its marker, so the colour/marker pair is learned once and reused everywhere."""
    style = ENGINE_STYLE[engine]
    transform = offset_copy(ax.transAxes, fig=ax.figure, x=3.5, y=dy + 3.2, units="points")
    ax.plot([0.0], [1.0], marker=style["marker"], ms=5.2, mfc=style["color"], mec=SURFACE, mew=0.6, ls="none", transform=transform,
            clip_on=False, zorder=5)
    ax.annotate(style["label"], xy=(0.0, 1.0), xycoords="axes fraction", xytext=(9, dy), textcoords="offset points", fontsize=size,
                fontweight="bold", color=INK, ha="left", va="bottom", annotation_clip=False)


def time_bounds(stats):
    values = [c["geomean"] for suite in stats["medians"].values() for by_scale in suite.values() for by_query in by_scale.values()
              for c in by_query.values() if c["geomean"]]
    return (min(values), max(values)) if values else (0.01, 100.0)


def fig_heatmap(ctx, suite):
    stats = ctx.stats
    scales, queries = ctx.scales(suite), stats["campaign"]["queries"][suite]
    engines = [e for e in ENGINES if e in stats["campaign"]["engines"]]
    vmin, vmax = time_bounds(stats)
    cmap = mcolors.LinearSegmentedColormap.from_list("time", TIME_RAMP)
    norm = mcolors.LogNorm(vmin=vmin, vmax=vmax)
    cell_w = 0.30 if len(scales) <= 3 else 0.255
    plot_w = len(scales) * cell_w
    left_w, gap = 0.95, 0.20
    width = left_w + len(engines) * plot_w + (len(engines) - 1) * gap + 0.10
    width = min(max(width, 5.0), DOUBLE)
    row_h = 0.215
    height = 0.84 + row_h * len(queries) + 0.84
    fig = plt.figure(figsize=(width, height))
    medians = stats["medians"][suite]
    for k, engine in enumerate(engines):
        x0 = (left_w + k * (plot_w + gap)) / width
        ax = fig.add_axes([x0, 0.84 / height, plot_w / width, row_h * len(queries) / height])
        for qi, query in enumerate(queries):
            for si, scale in enumerate(scales):
                cell = medians[scale][query][engine]
                y = len(queries) - 1 - qi
                if cell["geomean"] is not None:
                    fill = cmap(norm(cell["geomean"]))
                    ax.add_patch(Rectangle((si, y), 1, 1, fc=fill, ec=SURFACE, lw=0.9))
                    ax.text(si + 0.5, y + 0.5, compact_seconds(cell["geomean"]), ha="center", va="center", fontsize=5.5, color=text_color_on(fill))
                else:
                    reason = cell["reason_class"]
                    glyph = {"unsupported_operation": "✗", "out_of_memory": "OOM", "unstable_coverage": "◐"}.get(reason, "–" if cell["status"] == "not_run" else "!")
                    ax.add_patch(Rectangle((si, y), 1, 1, fc="#ffffff", ec=AXIS, lw=0.5, hatch="////", zorder=2))
                    ax.text(si + 0.5, y + 0.5, glyph, ha="center", va="center", fontsize=7.0 if glyph != "OOM" else 5.5, color=INK2, zorder=3,
                            bbox={"fc": SURFACE, "ec": "none", "pad": 0.4})
        ax.set_xlim(0, len(scales))
        ax.set_ylim(0, len(queries))
        ax.set_xticks(np.arange(len(scales)) + 0.5)
        ax.set_xticklabels([scale_label(s).replace("SF", "") for s in scales], fontsize=5.8)
        ax.xaxis.tick_top()
        ax.set_yticks(np.arange(len(queries)) + 0.5)
        ax.set_yticklabels(list(reversed(queries)) if k == 0 else [], fontsize=6.0)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        engine_title(ax, engine, dy=11)
        if k == 0:
            ax.annotate("SF", xy=(0, 1), xycoords="axes fraction", xytext=(-4, 3), textcoords="offset points", fontsize=5.6, color=INK2, ha="right", va="bottom")
    bar_w = min(2.2, width - left_w - 0.4)
    cax = fig.add_axes([left_w / width, 0.50 / height, bar_w / width, 0.085 / height])
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    bar = fig.colorbar(sm, cax=cax, orientation="horizontal")
    ticks = [t for t in (0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000, 3000) if vmin / 1.5 <= t <= vmax * 1.5]
    bar.set_ticks(ticks)
    bar.set_ticklabels([f"{t:g}" for t in ticks])
    bar.ax.tick_params(labelsize=5.6, length=2)
    bar.outline.set_visible(False)
    fig.text((left_w - 0.08) / width, 0.54 / height, "Time (s), log", fontsize=6.0, color=INK2, ha="right", va="center")
    import textwrap
    note = ("Darker is slower. Hatched cells have no timing: ✗ not supported on GPU, OOM out of memory (coverage outcomes); "
            "◐ unstable coverage (excluded, A1); ! other error (an integrity failure).")
    fig.text(left_w / width, 0.04 / height, "\n".join(textwrap.wrap(note, max(40, int((width - left_w - 0.1) / 0.052)))), fontsize=5.6, color=INK2, ha="left", va="bottom",
             linespacing=1.25)
    figure_title(fig, f"{SUITE_TITLE[suite]}: geometric-mean time per query, engine and scale (seconds)", left=0.012)
    finish(fig, ctx, f"f03_heatmap_{suite}", f"Median query time, {suite} suite",
           f"Per-query time in seconds for the {suite} suite: geometric mean over rounds of the per-round median of three measured trials. Rows are queries, "
           "columns scale factors, one panel per engine on a shared logarithmic scale (darker is slower). Hatched cells have no timing because the engine "
           "failed the query in every included round (✗ unsupported operation and OOM out of memory are coverage outcomes; ◐ unstable coverage, a coverage outcome in some rounds only, "
           "is excluded from every estimate by A1; ! is an integrity failure that should not occur in accepted rounds).",
           [f"medians.{suite}.*.*.*", "tables/t03_median_times.csv"], width_class="double" if width > 5.5 else "single",
           alt=f"Heatmap of query time for four engines across scale factors, {suite} suite.")


# ------------------------------------------------------------------ F4: GPU coverage matrix

# Coverage outcome classes (design amendment A1). Three failure classes differ in hue family, in lightness (greyscale L* 43 /
# 68 / 19, steps of 25-49) and in glyph shape, so each reads on its own in colour-vision deficiency and in a greyscale print.
# violet #6250d6 is palette slot 7, the amber #e8912d sits between slots 2 and 4 and is NOT an engine colour (Spark CPU is #eda100),
# and the dark red #5b1317 is deliberately outside the series lightness band (a filled cell with a white glyph, not a line mark).
# validate_palette.js on the three fills (--pairs all, white): worst CVD dE 30.7 (deutan), normal-vision dE 31.8 (both pass); the band
# check fails for the dark red (intended) and the contrast check warns for the amber (2.47:1), relieved by the in-cell "OOM" label,
# the legend and tables/t12_coverage. Pale green and grey are not data colours; the hatch and the k/n label mark "partial".
COVERAGE_STYLE = {
    "supported": {"fill": "#cfecce", "ink": "#006300", "glyph": "✓", "size": 7.0, "label": "Supported (all rounds)"},
    "partial": {"fill": "#cfecce", "ink": INK, "glyph": "◐", "size": 5.6, "label": "Unstable (some rounds, k/n)", "hatch": "////", "hatch_color": "#6f8f6f"},
    "unsupported_operation": {"fill": "#6250d6", "ink": "#ffffff", "glyph": "✗", "size": 7.4, "label": "Not supported on GPU"},
    "out_of_memory": {"fill": "#e8912d", "ink": INK, "glyph": "OOM", "size": 5.6, "label": "Out of memory"},
    "error": {"fill": "#5b1317", "ink": "#ffffff", "glyph": "!", "size": 7.4, "label": "Other error (integrity failure)"},
    "not_run": {"fill": "#f4f3ef", "ink": MUTED, "glyph": "–", "size": 5.6, "label": "Not run at this scale"},
}
COVERAGE_LEGEND = (("supported", "partial", "unsupported_operation"), ("out_of_memory", "error", "not_run"))


def coverage_key(cell):
    if cell is None or cell["status"] == "not_run":
        return "not_run"
    if cell["status"] in ("supported", "partial"):
        return cell["status"]
    key = cell["reason_class"] or "error"
    return key if key in COVERAGE_STYLE else "error"


def draw_coverage_cell(ax, x, y, w, h, key, extra="", outline=False):
    """One matrix cell (also the legend swatch): fill, optional hatch, glyph in the class ink, optional black outline."""
    spec = COVERAGE_STYLE[key]
    ax.add_patch(Rectangle((x, y), w, h, fc=spec["fill"], ec=SURFACE, lw=1.0, zorder=2))
    if outline:   # inset so the outline never spills into the neighbouring cell
        ax.add_patch(Rectangle((x + 0.016, y + 0.016), w - 0.032, h - 0.032, fc="none", ec=INK, lw=1.2, zorder=3))
    if spec.get("hatch"):
        ax.add_patch(Rectangle((x, y), w, h, fc="none", ec=spec["hatch_color"], lw=0, hatch=spec["hatch"], zorder=2.5))
    glyph = spec["glyph"] + extra
    kwargs = {"bbox": {"fc": spec["fill"], "ec": "none", "pad": 0.5}} if spec.get("hatch") else {}
    ax.text(x + w / 2, y + h / 2, glyph, ha="center", va="center", fontsize=spec["size"] if not extra else 5.5, fontweight="bold" if key != "not_run" else "normal",
            color=spec["ink"], zorder=4, **kwargs)


def fig_coverage(ctx, single=False):
    stats = ctx.stats
    gpu = [e for e in ("polars_gpu", "spark_gpu") if e in stats["campaign"]["engines"]]
    scales, suites = ctx.all_scales, ctx.suites
    matrix = stats["coverage"]["matrix"]
    rows = []
    for suite in suites:
        rows.append(("header", suite))
        rows += [("query", (suite, q)) for q in stats["campaign"]["queries"][suite]]
    width = SINGLE if single else DOUBLE
    ch, left_w, right, gap = (0.19, 0.84, 0.06, 0.0) if single else (0.215, 1.02, 0.08, 0.34)
    blocks_across = 1 if single else len(gpu)
    cw = (width - left_w - right - gap * (blocks_across - 1)) / (len(scales) * blocks_across)
    head_h = 0.46                                          # engine name + scale-factor labels above each block
    block_h = head_h + ch * len(rows)
    title = "RQ4  GPU coverage: which queries run on each GPU engine, by scale"
    title_h = 0.30 + 0.135 * len(wrap_title(title, width))
    legend_cols = 2 if single else 3
    legend_rows = math.ceil(6 / legend_cols)
    integrity_classes = AC.INTEGRITY_REASONS | {"session_failed", "invalid"}
    n_integrity = sum(1 for by_query in matrix.values() for by_scale in by_query.values() for by_engine in by_scale.values()
                      for engine, cell in by_engine.items() if engine in gpu and cell is not None and cell["status"] in ("failed", "partial")
                      and cell["reason_class"] in integrity_classes)
    note = ("Black outline: failure not pre-declared (design section 8 pre-declares cx_q13 and cx_window on Polars GPU). "
            "Not supported (✗) and out of memory (OOM) are coverage outcomes (A1); hatched: unstable coverage, a coverage outcome in some rounds only, "
            "excluded from every contrast in all rounds. Other error (!) is an integrity failure: it fails its round (section 7), so an accepted round should show none.")
    import textwrap
    note_lines = textwrap.wrap(note, int((width - 0.2) / 0.052))
    alarm_lines = textwrap.wrap(f"WARNING: {n_integrity} cell(s) in accepted rounds failed for an integrity reason (marked ! or hatched): the integrity gate and this analysis disagree.",
                                int((width - 0.2) / 0.052)) if n_integrity else []
    legend_h = 0.24 * legend_rows + 0.10
    foot_h = 0.12 * (len(note_lines) + len(alarm_lines)) + 0.08
    height = title_h + (len(gpu) if single else 1) * block_h + (len(gpu) - 1 if single else 0) * 0.18 + legend_h + foot_h + 0.06
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    for bi, engine in enumerate(gpu):
        style = ENGINE_STYLE[engine]
        x0 = left_w + (0 if single else bi * (len(scales) * cw + gap))
        top = height - title_h - (bi * (block_h + 0.18) if single else 0)
        ax.plot([x0 + 0.06], [top - 0.10], marker=style["marker"], ms=5.2, mfc=style["color"], mec=SURFACE, mew=0.6, ls="none", clip_on=False)
        ax.text(x0 + 0.16, top - 0.10, style["label"], fontsize=7.0, fontweight="bold", va="center", ha="left")
        for si, scale in enumerate(scales):
            ax.text(x0 + (si + 0.5) * cw, top - 0.33, scale_label(scale).replace("SF", ""), ha="center", va="center", fontsize=5.8, color=INK2)
        ax.text(x0 - 0.06, top - 0.33, "SF", ha="right", va="center", fontsize=5.6, color=INK2)
        for ri, (kind, item) in enumerate(rows):
            y = top - head_h - ch * (ri + 1)
            if kind == "header":
                ax.text(0.012 * width, y + ch / 2, SUITE_TITLE[item], fontsize=6.8, fontweight="bold", va="center", ha="left")
                continue
            suite, query = item
            if single or bi == 0:
                ax.text(x0 - 0.06, y + ch / 2, query, fontsize=6.0, color=INK2, va="center", ha="right")
            for si, scale in enumerate(scales):
                cell = matrix.get(suite, {}).get(query, {}).get(scale, {}).get(engine)
                key = coverage_key(cell)
                outline = key not in ("supported", "partial", "not_run") and not cell["predeclared_unsupported"]
                extra = f" {cell['rounds_ok']}/{cell['rounds_total']}" if key == "partial" else ""
                draw_coverage_cell(ax, x0 + si * cw, y, cw, ch, key, extra, outline)
    legend_top = foot_h + legend_h
    col_w = (width - 0.2) / legend_cols
    for index, key in enumerate(k for row in COVERAGE_LEGEND for k in row):
        r, c = divmod(index, legend_cols) if single else (index // 3, index % 3)
        lx, ly = 0.12 + c * col_w, legend_top - 0.10 - 0.24 * r - 0.17
        draw_coverage_cell(ax, lx, ly, 0.32, 0.17, key, " k/n" if key == "partial" else "")
        ax.text(lx + 0.40, ly + 0.085, COVERAGE_STYLE[key]["label"], fontsize=6.1, va="center", ha="left", color=INK)
    ax.text(0.12, foot_h - 0.04, "\n".join(note_lines), ha="left", va="top", fontsize=5.6, color=INK2, linespacing=1.25)
    if alarm_lines:
        ax.text(0.12, foot_h - 0.04 - 0.12 * len(note_lines), "\n".join(alarm_lines), ha="left", va="top", fontsize=5.8, color=CRITICAL, fontweight="bold",
                linespacing=1.25)
    figure_title(fig, title, left=0.012)
    finish(fig, ctx, "f04_gpu_coverage" + ("_1col" if single else ""), "GPU coverage matrix",
           "Support of each query on the two GPU engines at every scale, across the included rounds. Polars GPU runs under raise_on_fail=True, so a query it "
           "cannot execute entirely on the GPU is a recorded failure, never a CPU timing. Failure classes differ in fill, glyph and lightness: violet ✗ operation "
           "not supported on the GPU, amber OOM device or host out of memory (both coverage outcomes, A1), dark red ! any other error, which is an integrity failure that fails "
           "its round and so should not occur in accepted rounds; a hatched cell is unstable coverage (A1): the coverage outcome occurred in some rounds only (k of n rounds ran) and the cell is excluded from every contrast in all rounds. "
           "cx_q13 (inline regex flag) and cx_window (cum_count over a window) were declared unsupported before the campaign; black-outlined cells are "
           "failures that were not.",
           ["coverage.matrix.*.*.*.*", "coverage.discrepancies", "tables/t12_coverage.csv"], width_class="single" if single else "double",
           alt="Matrix of supported, unsupported, out-of-memory and other-error cells for Polars GPU and Spark GPU by query and scale factor.")


# ------------------------------------------------------------------ F5: variability

def fig_variability(ctx, single=False):
    stats = ctx.stats
    cells = stats["variability"]["cells"]
    engines = [e for e in ENGINES if e in stats["campaign"]["engines"]]
    scales = ctx.all_scales
    xs = {s: scale_number(s) for s in scales}
    title = "Variability of the measurements by engine and scale (line = median over cells)"
    legend_rows = 3 if single else 1
    P = Panels(single, 2, 1.55 if single else 1.7, title, legend_rows=legend_rows, hgap=0.78)
    rng = np.random.default_rng(7)
    floor = 1e-3
    every = [v for c in cells for v in ([*c["cv_within"], c["cv_between"]]) if v is not None and v > 0]
    y_lo = max(floor * 0.9, min(every) / 1.6) if every else floor
    y_hi = min(1.5, max(every) * 1.8) if every else 1.0
    spec = (("Within session", "cv_within", "CV of the 3 measured trials of one session"),
            ("Between rounds", "cv_between", "CV of the per-round medians across rounds"))
    for ax, (name, key, sub) in zip(P.axes, spec):
        ax.set_xscale("log")
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_x=True, log_y=True)
        for k, engine in enumerate(engines):
            style = ENGINE_STYLE[engine]
            shift = math.exp((k - (len(engines) - 1) / 2) * 0.12)
            med_x, med_y = [], []
            for scale in scales:
                vals = []
                for c in cells:
                    if c["engine"] != engine or c["scale"] != scale:
                        continue
                    vals += [v for v in (c[key] if key == "cv_within" else [c[key]]) if v is not None]
                if not vals:
                    continue
                vals = np.maximum(np.array(vals), floor)
                jitter = np.exp(rng.uniform(-0.035, 0.035, len(vals)))
                ax.plot(xs[scale] * shift * jitter, vals, ls="none", marker=style["marker"], ms=1.9, mfc=style["color"], mec="none", alpha=0.32, zorder=2)
                med_x.append(xs[scale] * shift)
                med_y.append(float(np.median(vals)))
            ax.plot(med_x, med_y, color=style["color"], lw=1.2, marker=style["marker"], ms=4.6, mfc=style["color"], mec=SURFACE, mew=0.7, zorder=4)
        ax.set_xlim(min(xs.values()) / 1.5, max(xs.values()) * 1.5)
        ax.set_ylim(y_lo, y_hi)
        ax.xaxis.set_major_locator(ticker.FixedLocator(list(xs.values())))
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_major_locator(ticker.FixedLocator([0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0]))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{100 * v:g}%"))
        panel_title(ax, name, sub)
    P.ylabel("CV (log scale)" if single else "Coefficient of variation (log scale)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(legend_rows))
    P.legend(engine_handles(engines, with_line=True) + [Line2D([0], [0], marker="o", color="none", mfc=INK2, mec="none", alpha=0.4, ms=3, label="one cell (query × suite × round)")])
    finish(P.fig, ctx, "f05_variability" + P.tag(), "Coefficient of variation by engine and scale",
           "Coefficient of variation of the timings by engine and scale factor. Top/left: within one session (three measured trials of a query). Bottom/right: between "
           "rounds (per-round medians of a query). Faint markers are individual cells (query, suite and, within session, round); lines connect the median over "
           "cells at each scale. Four rounds make the between-round CVs coarse.",
           ["variability.cells", "variability.by_engine_scale.*", "tables/t07_variability.csv", "tables/t08_variability_by_scale.csv"],
           width_class=P.width_class(), alt="Coefficient of variation against scale factor for four engines, within session and between rounds.")


# ------------------------------------------------------------------ F6: order and carryover

def fig_order(ctx, single=False):
    stats = ctx.stats
    order = stats["order"]
    if not order["position"]:
        return "no order information in the schedules"
    engines = [e for e in ENGINES if e in stats["campaign"]["engines"]]
    balance = order["design_balance"]
    note = ("Positions and adjacent pairs balanced (Williams design)" if balance.get("positions_balanced") and balance.get("pairs_balanced")
            else "Observed order is not a balanced Latin square")
    title = f"Order and carryover check (descriptive). {note}."
    legend_rows = 3 if single else 1
    P = Panels(single, 2, 1.55 if single else 1.7, title, legend_rows=legend_rows, ratios=[4, 5], hgap=0.78)
    axes = P.axes
    values = []
    for group in ("position", "predecessor"):
        for v in order[group].values():
            values += [v["ci_low_pct"], v["ci_high_pct"], v["mean_pct"]]
    for group in ("position_by_engine", "predecessor_by_engine"):
        for by_engine in order[group].values():
            values += [v["mean_pct"] for v in by_engine.values()]
    values = [v for v in values if v is not None]
    limit = max(1.0, max(abs(v) for v in values) * 1.15) if values else 5.0

    def draw(ax, keys, group, by_engine_group, labels):
        style_axes(ax, grid="y")
        ax.axhline(0, color=INK2, lw=0.8, zorder=2)
        for i, key in enumerate(keys):
            rec = order[group].get(key)
            if not rec or rec["mean_pct"] is None:
                continue
            for j, engine in enumerate(engines):
                sub = order[by_engine_group].get(key, {}).get(engine)
                if sub and sub["mean_pct"] is not None:
                    st = ENGINE_STYLE[engine]
                    ax.plot([i + (j - (len(engines) - 1) / 2) * 0.22], [sub["mean_pct"]], ls="none", marker=st["marker"], ms=3.8, mfc=st["color"],
                            mec=SURFACE, mew=0.5, alpha=0.95, zorder=3)
            if rec["ci_low_pct"] is not None:
                ax.plot([i, i], [rec["ci_low_pct"], rec["ci_high_pct"]], color=INK, lw=1.6, zorder=4, solid_capstyle="butt")
                for edge in (rec["ci_low_pct"], rec["ci_high_pct"]):
                    ax.plot([i - 0.05, i + 0.05], [edge, edge], color=INK, lw=1.0, zorder=4)
            ax.plot([i], [rec["mean_pct"]], ls="none", marker="_", ms=9, mew=1.8, color=INK, zorder=5)
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels(labels, fontsize=5.8 if single else 6.0)
        ax.set_xlim(-0.6, len(keys) - 0.4)
        ax.set_ylim(-limit, limit)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:+g}%" if v else "0"))

    positions = sorted(order["position"], key=int)
    draw(axes[0], positions, "position", "position_by_engine", [f"{p}" + (" (first)" if p == "1" else " (last)" if p == positions[-1] else "") for p in positions])
    preds = [p for p in ("none", *engines) if p in order["predecessor"]]
    draw(axes[1], preds, "predecessor", "predecessor_by_engine", ["none (first)" if p == "none" else ENGINE_STYLE[p]["label"].replace(" ", "\n") if single else ENGINE_STYLE[p]["label"] for p in preds])
    panel_title(axes[0], "Position in the block", "session order within a (suite, scale) block")
    panel_title(axes[1], "Predecessor engine", "engine that ran immediately before")
    P.ylabel("Deviation from cell mean")
    if single:
        axes[1].tick_params(axis="y", labelleft=True, length=2.5)
    P.legend([Line2D([0], [0], color=INK, lw=1.6, marker="_", ms=9, mew=1.8, label="all engines: mean (tick), 95% CI"), *engine_handles(engines)])
    finish(P.fig, ctx, "f06_order_effects" + P.tag(), "Position and carryover effects",
           "Mean deviation of log time from each cell's own mean over rounds, by session position within a (suite, scale) block (left/top) and by the engine that "
           "ran immediately before (right/bottom). Coloured markers split the mean by measured engine; the black bar is the pooled mean with a 95% t-interval over "
           "rounds. The Williams design places every engine in every position once and every ordered pair of neighbours once. Reported descriptively; no "
           "correction is applied.",
           ["order.position.*", "order.predecessor.*", "order.position_by_engine.*", "order.predecessor_by_engine.*", "order.design_balance",
            "tables/t09_order_position.csv", "tables/t10_order_predecessor.csv"], width_class=P.width_class(),
           alt="Time deviation by session position and by predecessor engine.")


# ------------------------------------------------------------------ F7: peak GPU memory

def fig_gpu_memory(ctx, single=False):
    stats = ctx.stats
    memory = stats["secondary"]["gpu_memory"]
    summary = memory["summary"]
    if not any(summary.get(s) for s in ctx.suites):
        return "no peak_gpu_memory_bytes recorded"
    capacity = memory["capacity_bytes"]
    measure = memory.get("measure", {})
    gpu = [e for e in ("polars_gpu", "spark_gpu") if e in stats["campaign"]["engines"]]
    spark_task = "spark_gpu" in gpu and measure.get("spark_gpu") == "task_reported_max_device_memory"
    spark_omitted = "spark_gpu" in gpu and not spark_task                     # only the pool reservation exists: not a memory figure
    constant = memory.get("constant_check", {}).get("spark_gpu", {})
    spark_pool = spark_task and constant.get("sessions", 0) > 0 and constant.get("constant") == constant.get("sessions")   # drawn only if it is a reservation
    gib = 1024 ** 3
    title = "GPU memory during the timed action: what each engine's number measures differs (see key)"
    legend_rows = 4 if single else 2
    P = Panels(single, len(ctx.suites), 1.75 if single else 1.7, title, legend_rows=legend_rows, hgap=0.8, extra_bottom=0.15 if spark_omitted else 0.0)

    def series(suite, engine):
        out = []
        for sc in ctx.scales(suite):
            block = summary.get(suite, {}).get(sc, {}).get(engine)
            if engine == "spark_gpu" and block:
                block = block.get("task_reported")
            if block:
                out.append((scale_number(sc), block))
        return out

    values = [b[k] / gib for suite in ctx.suites for e in ("polars_gpu", "spark_gpu") if e in gpu and not (e == "spark_gpu" and not spark_task)
              for _, b in series(suite, e) for k in ("min_bytes", "max_bytes")]
    low = max(min(values) / 1.6, 0.01) if values else 0.1
    high = (capacity / gib * 1.9) if capacity else max(values) * 2
    oom_drawn = []
    any_oom = any(c.get("reason_class") == "out_of_memory" for bq in stats["coverage"]["matrix"].values() for bs in bq.values() for be in bs.values() for c in be.values()
                  if c is not None)
    for ax, suite in zip(P.axes, ctx.suites):
        ax.set_xscale("log")
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_x=True, log_y=True)
        if capacity:
            ax.axhline(capacity / gib, color=INK2, lw=0.7, zorder=2)
            ax.annotate(f"device memory {capacity / gib:.1f} GiB", xy=(0.01, capacity / gib), xycoords=("axes fraction", "data"), xytext=(0, 2.5),
                        textcoords="offset points", fontsize=5.8, color=INK2, va="bottom")
        labelled = {}
        for engine in gpu:
            style = ENGINE_STYLE[engine]
            if engine == "spark_gpu" and spark_task:
                # the nvidia-smi sample is the RAPIDS pool reserved at plugin start: a labelled reference line, not a data series
                pool = [(scale_number(sc), summary[suite][sc]["spark_gpu"]) for sc in ctx.scales(suite) if "spark_gpu" in summary.get(suite, {}).get(sc, {})]
                if pool and spark_pool:
                    xs = [p[0] for p in pool]
                    ax.plot(xs, [p[1]["median_bytes"] / gib for p in pool], color=style["color"], lw=1.6, alpha=0.8, zorder=3, solid_capstyle="butt")
                    level = pool[-1][1]["median_bytes"] / gib
                    polars_top = max((p[1]["median_bytes"] / gib for p in series(suite, "polars_gpu")), default=0.0)
                    if polars_top < 0.72 * level:       # the Polars GPU curve near the pool line would run through the label; the legend names the line
                        ax.annotate("Spark GPU pool reservation (nvidia-smi)", xy=(max(xs), level), xytext=(-3, -3), textcoords="offset points",
                                    fontsize=5.6, color=INK2, ha="right", va="top")
            pts = series(suite, engine) if engine == "polars_gpu" or spark_task else []
            if pts:
                x = np.array([p[0] for p in pts])
                ax.fill_between(x, [p[1]["min_bytes"] / gib for p in pts], [p[1]["max_bytes"] / gib for p in pts], color=style["color"], alpha=0.16, lw=0, zorder=2)
                ax.plot(x, [p[1]["median_bytes"] / gib for p in pts], color=style["color"], lw=1.2, marker=style["marker"], ms=4.6, mfc=style["color"],
                        mec=SURFACE, mew=0.7, zorder=4)
            for sc in ctx.scales(suite):
                ooms = sorted({q for q, by_scale in stats["coverage"]["matrix"].get(suite, {}).items()
                               if by_scale.get(sc, {}).get(engine, {}).get("reason_class") == "out_of_memory"})
                if ooms and capacity:
                    # the failure sits on the device-memory line; its label goes above the line, clear of every curve
                    lift = 3.0 + 7.5 * labelled.get(sc, 0)
                    labelled[sc] = labelled.get(sc, 0) + 1
                    oom_drawn.append(sc)
                    ax.plot([scale_number(sc)], [capacity / gib], marker="X", ms=6.6, mfc=INK, mec=SURFACE, mew=0.8, ls="none", zorder=6)
                    ax.annotate(f"{style['label']} OOM: {', '.join(ooms)}", xy=(scale_number(sc), capacity / gib), xytext=(4, lift),
                                textcoords="offset points", fontsize=5.6, color=INK2, ha="right", va="bottom")
        xs_suite = [scale_number(s) for s in ctx.scales(suite)]
        ax.set_xlim(min(xs_suite) / 1.35, max(xs_suite) * 1.35)
        ax.set_ylim(low, high * (1.25 if any_oom else 1.0))
        ax.xaxis.set_major_locator(ticker.FixedLocator(xs_suite))
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_major_locator(ticker.FixedLocator([t for t in (0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300) if low * 0.999 <= t <= high * 1.3]))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        panel_title(ax, SUITE_TITLE[suite], "median over queries; band = min–max over queries")
    P.ylabel("GPU memory (GiB, log)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(legend_rows))
    handles = []
    if "polars_gpu" in gpu:
        handles.append(Line2D([0], [0], color=ENGINE_STYLE["polars_gpu"]["color"], lw=1.2, marker=ENGINE_STYLE["polars_gpu"]["marker"], ms=5.0,
                              mfc=ENGINE_STYLE["polars_gpu"]["color"], mec=SURFACE, label="Polars GPU: nvidia-smi, RMM pool high-water mark"))
    if spark_task:
        handles.append(Line2D([0], [0], color=ENGINE_STYLE["spark_gpu"]["color"], lw=1.2, marker=ENGINE_STYLE["spark_gpu"]["marker"], ms=5.0,
                              mfc=ENGINE_STYLE["spark_gpu"]["color"], mec=SURFACE, label="Spark GPU: largest task-reported device memory (not a total)"))
        if spark_pool:
            handles.append(Line2D([0], [0], color=ENGINE_STYLE["spark_gpu"]["color"], lw=1.6, alpha=0.8, label="Spark GPU: RAPIDS pool reservation (nvidia-smi)"))
    if oom_drawn:     # the key only appears when a failure is actually marked in a panel
        handles.append(Line2D([0], [0], marker="X", mfc=INK, mec=SURFACE, mew=0.8, ls="none", ms=6.4, label="out-of-memory failure (queries named)"))
    P.legend(handles, ncol_double=2, ncol_single=1)
    if spark_omitted:
        P.fig.text(0.012, 0.03 / P.height, "Spark GPU is not shown: its event logs carry no task-level device-memory accumulator, so only the RAPIDS pool reservation "
                   "(nvidia-smi) exists, which is not memory in use.", ha="left", va="bottom", fontsize=5.6, color=INK2)
    check = memory.get("high_water_check", {}).get("polars_gpu", {})
    caption = ("Device memory by engine and scale factor; what is measured differs by engine. Polars GPU: nvidia-smi device memory of the worker process during the timed "
               "action, maximum over the three trials and the included rounds. cudf-polars allocates through an RMM async pool that retains freed blocks, so this is the "
               "pool's high-water mark in the session so far" + (
                   "" if not check.get("sessions") else
                   f" (it never falls within a session in {check['nondecreasing']} of {check['sessions']} sessions)" if check["nondecreasing"] == check["sessions"] else
                   f" (it does not fall within a session in {check['nondecreasing']} of {check['sessions']} sessions; in the other "
                   f"{check['sessions'] - check['nondecreasing']} it falls by up to {check['largest_fall_bytes'] / 2 ** 30:.1f} GiB, where the pool nears the device size)")
               + " and an upper bound on what a query needs, not a per-query working set. ")
    if spark_task:
        caption += ("Spark GPU: the largest device allocation any single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes in the Spark event log) among the tasks "
                    "launched inside the action; it is not a simultaneous total over concurrent tasks. ")
        if spark_pool:
            caption += ("The thin line is the nvidia-smi sample, constant within every session (" f"{constant['constant']} of {constant['sessions']}): the RAPIDS memory pool "
                        "reserved at plugin start, drawn only as a reference. ")
        else:
            caption += (f"The nvidia-smi sample of the Spark process tree is constant within only {constant.get('constant', 0)} of {constant.get('sessions', 0)} sessions, "
                        "so it is not a reservation line and is not drawn. ")
    else:
        caption += ("Spark GPU is not shown: its event logs carry no task-level device-memory accumulator, so only the RAPIDS pool reservation exists (measure pool_reservation_only), "
                    "which is not memory in use. ")
    caption += "Lines are medians over queries and bands the range; the solid horizontal line is the device's memory."
    finish(P.fig, ctx, "f07_gpu_memory" + P.tag(), "GPU memory versus scale",
           caption,
           ["secondary.gpu_memory.summary.*", "secondary.gpu_memory.measure.*", "secondary.gpu_memory.high_water_check.*", "secondary.gpu_memory.capacity_bytes",
            "coverage.matrix.*", "tables/t13_gpu_memory.csv"],
           width_class=P.width_class(), alt="GPU memory against scale factor for Polars GPU and Spark GPU, one panel per suite.")


# ------------------------------------------------------------------ F8 / F9: bridges (version, host)

def bridge_panel(ax, ctx, ratio_by_suite, suite, color, marker, label_up, label_down, dot_alpha=0.5, shift=0.0):
    xs_all = [scale_number(s) for s in ctx.scales(suite)]
    ax.set_xscale("log")
    ax.set_yscale("log")
    style_axes(ax, grid="y", log_x=True, log_y=True)
    reference_line(ax, "y")
    scales = [s for s in ctx.scales(suite) if s in ratio_by_suite.get(suite, {})]
    for scale in scales:
        record = ratio_by_suite[suite][scale]
        x = scale_number(scale) * math.exp(shift)
        queries = list(record["q"].values())
        for i, q in enumerate(queries):
            if q["estimate"] is not None:
                ax.plot([x * math.exp((i - (len(queries) - 1) / 2) * 0.06)], [q["estimate"]], ls="none", marker=marker, ms=3.2, mfc="none", mec=color,
                        mew=0.8, alpha=dot_alpha, zorder=2)
        if record["estimate"] is not None:
            if record["ci_low"] is not None:
                ax.plot([x, x], [record["ci_low"], record["ci_high"]], color=color, lw=1.3, zorder=3, solid_capstyle="butt")
            ax.plot([x], [record["estimate"]], ls="none", marker=marker, ms=5.6, mfc=color, mec=SURFACE, mew=0.8, zorder=4)
    ax.set_xlim(min(xs_all) / 1.35, max(xs_all) * 1.35)
    ax.xaxis.set_major_locator(ticker.FixedLocator(xs_all))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    direction_labels(ax, label_down, label_up, offset=-15.0)


def bridge_limits(records):
    vals = []
    for by_suite in records:
        for by_scale in by_suite.values():
            for r in by_scale.values():
                vals += [r["ci_low"], r["ci_high"], r["estimate"], *[q["estimate"] for q in r["q"].values()]]
    return with_parity(*padded_limits(vals, 1.12), 1.0)


def fig_version_bridge(ctx, single=False):
    stats = ctx.stats
    side = stats["side_study"]
    if not side["meta"].get("available") or not side["ratio"]:
        return "no side-study data"
    suites = [s for s in ctx.suites if s in side["ratio"]]
    title = "RQ5  Polars version bridge: 2.0.0 versus 1.44.2 on the same host (CPU, streaming engine)"
    legend_rows = 2 if single else 1
    P = Panels(single, len(suites), 1.55 if single else 1.65, title, legend_rows=legend_rows, extra_bottom=0.16, hgap=0.86)
    lo, hi = bridge_limits([side["ratio"]])
    color, marker = ENGINE_STYLE["polars"]["color"], ENGINE_STYLE["polars"]["marker"]
    for ax, suite in zip(P.axes, suites):
        bridge_panel(ax, ctx, side["ratio"], suite, color, marker, "2.0.0 slower", "2.0.0 faster")
        ax.set_ylim(lo, hi)
        ratio_ticks(ax, "y", lo, hi)
        n = max((r["n_queries"] for r in side["ratio"][suite].values()), default=0)
        panel_title(ax, SUITE_TITLE[suite], f"suite geometric mean (filled), {n} queries (open)")
    P.ylabel("Time ratio 2.0.0 / 1.44.2 (log)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(legend_rows))
    P.legend([Line2D([0], [0], color=color, lw=1.8, marker=marker, mfc=color, mec=SURFACE, ms=5.4, label="suite geometric mean, 95% CI over rounds"),
              Line2D([0], [0], color=color, lw=0, marker=marker, mfc="none", mec=color, ms=3.4, label="single query (geometric mean over rounds)")],
             ncol_double=2, ncol_single=1)
    finish(P.fig, ctx, "f08_polars_version_bridge" + P.tag(), "Polars 2.0.0 versus 1.44.2",
           f"Ratio of Polars CPU times, version 2.0.0 over 1.44.2, on the same {ctx.host} host and protocol. A ratio above 1 means 2.0.0 is slower. Filled markers with bars "
           "are suite geometric means with 95% t-intervals over side-study rounds (df = rounds − 1); open markers are single queries. The comparison bridges the "
           "main campaign (Polars 1.44.2) to the earlier A100 study (Polars 2.0.0).",
           ["side_study.ratio.*", "side_study.meta", "tables/t17_polars_version_bridge.csv"], width_class=P.width_class(),
           alt="Polars 2.0.0 over 1.44.2 time ratio against scale factor for both suites.")


def fig_a100(ctx, single=False):
    import textwrap
    stats = ctx.stats
    a100 = stats["a100_vs_h100"]
    if not a100["meta"].get("available") or not a100["ratio"]:
        return "no A100 roots supplied"
    host = ctx.host
    engines = [e for e in ("polars_gpu", "spark_gpu") if e in a100["ratio"]]
    suites = [s for s in ctx.suites if any(s in a100["ratio"][e] for e in engines)]
    study = a100.get("study") or {}
    label = study.get("label") or "earlier A100 study"
    headline = study.get("difference_headline")
    title = f"GPU engines in the {label} versus this {host} campaign (non-equivalent hosts)"
    hosts = "; ".join(h["label"] for h in a100["meta"]["a100_hosts"][:1])
    note = (f"NOT an equivalent comparison: the {label} (saved evidence of a previous study, not the A100 campaign; {hosts or 'A100'}) versus {ctx.host_line}. "
            + (f"Differences: {headline} " if headline else "Software versions and protocol differ. ")
            + (f"{', '.join(ENGINE_STYLE[e]['label'] if e in ENGINE_STYLE else e for e in study['engines_only_in_h100_campaign'])} was not run in that study. " if study.get("engines_only_in_h100_campaign") else "")
            + "CPU engines are not compared. The earlier study is one session per cell, so the bars show round-to-round variation on the "
            f"{host} side only.")
    lines = textwrap.wrap(note, int(((SINGLE if single else DOUBLE) - 0.2) / 0.052))
    foot_h = 0.115 * len(lines) + 0.10
    P = Panels(single, len(suites), 1.55 if single else 1.65, title, legend_rows=1, extra_bottom=0.16 + foot_h, hgap=0.86)
    lo, hi = bridge_limits([a100["ratio"][e] for e in engines])
    for ax, suite in zip(P.axes, suites):
        for k, engine in enumerate(engines):
            style = ENGINE_STYLE[engine]
            bridge_panel(ax, ctx, a100["ratio"][engine], suite, style["color"], style["marker"], f"{host} host faster", "A100 host faster",
                         shift=(k - (len(engines) - 1) / 2) * 0.16)
        ax.set_ylim(lo, hi)
        ratio_ticks(ax, "y", lo, hi)
        panel_title(ax, SUITE_TITLE[suite], f"A100 time / {host} time; open = single queries")
    P.ylabel(f"Time ratio A100 / {host} (log)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(1) + foot_h)
    P.legend(engine_handles(engines), ncol_double=len(engines), ncol_single=len(engines), y_in=foot_h)
    P.fig.text(0.012, 0.03 / P.height, "\n".join(lines), ha="left", va="bottom", fontsize=5.6, color=INK2, linespacing=1.25)
    finish(P.fig, ctx, "f09_a100_vs_h100" + P.tag(), f"{label} versus {host}, GPU engines",
           f"Ratio of GPU-engine times in the {label} (saved evidence of a previous study, not the A100 campaign) over this campaign's {host} host. Above 1 the {host} host was faster. "
           "The hosts differ in GPU, vCPU count, RAM and software versions, so the ratio mixes hardware, stack and protocol changes"
           + (f" ({headline.rstrip('.')})" if headline else "") + ". Only GPU engines are compared; CPU engines are never compared across "
           f"hosts. The earlier study has one session per cell, so the intervals reflect {host} round variation only.",
           ["a100_vs_h100.ratio.*", "a100_vs_h100.meta", "tables/t18_a100_vs_h100.csv"], width_class=P.width_class(),
           alt=f"A100 over {host} time ratio for GPU engines against scale factor.")


# ------------------------------------------------------------------ F10: headline absolute times

HEADLINE_MARKER = 6.6          # data marker size in F10 (points); triangles are drawn 20% larger because they read smaller than discs
HEADLINE_STRIP = 0.70          # inches of the "no timing" strip under each F10 panel


def fig_headline(ctx, single=False):
    stats = ctx.stats
    engines = [e for e in ENGINES if e in stats["campaign"]["engines"]]
    panels = []
    for suite in ctx.suites:
        scale = ctx.headline_scale if ctx.headline_scale in ctx.scales(suite) else ctx.scales(suite)[-1]
        panels.append((suite, scale))
    title = "Headline: per-query time by engine at the largest scale (lower is faster)"
    legend_rows = 3 if single else 1
    rotate = any(len(q) > 5 for suite, _ in panels for q in stats["campaign"]["queries"][suite])   # one style for every panel
    P = Panels(single, len(panels), (1.75 if single else 1.85) + HEADLINE_STRIP, title, legend_rows=legend_rows, extra_bottom=0.22 if rotate else 0.10, hgap=0.92 if rotate else 0.8)
    values = [c["geomean"] for suite, scale in panels for by_engine in stats["medians"][suite][scale].values() for c in by_engine.values() if c["geomean"]]
    lo, hi = min(values) / 2.2, max(values) * 1.5
    strip_frac = HEADLINE_STRIP / P.height
    strips = []
    for ax in P.axes:        # the plot area keeps its height; a strip for the queries without a timing sits between it and the tick labels
        box = ax.get_position()
        strip = P.fig.add_axes([box.x0, box.y0, box.width, strip_frac], sharex=ax)
        ax.set_position([box.x0, box.y0 + strip_frac, box.width, box.height - strip_frac])
        strips.append(strip)
    reasons = {"unsupported_operation": "not supported", "out_of_memory": "OOM", "unstable_coverage": "unstable"}
    for ax, strip, (suite, scale) in zip(P.axes, strips, panels):
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_y=True)
        queries = stats["campaign"]["queries"][suite]
        missing = 0
        for qi, query in enumerate(queries):
            for k, engine in enumerate(engines):
                cell = stats["medians"][suite][scale][query][engine]
                x = qi + (k - (len(engines) - 1) / 2) * 0.17
                style = ENGINE_STYLE[engine]
                size = HEADLINE_MARKER * (1.2 if style["marker"] == "^" else 1.0)
                edge = mix(style["color"], "#000000", 0.45)
                if cell["geomean"] is None:
                    # no timing: nothing is drawn at a y value of the time axis; the engine's own marker and the recorded reason sit in the strip below
                    missing += 1
                    strip.plot([x], [0.88], ls="none", marker=style["marker"], ms=size * 0.8, mfc=SURFACE, mec=edge, mew=1.0, zorder=4, clip_on=False)
                    strip.annotate(reasons.get(cell["reason_class"], "failed"), xy=(x, 0.76), rotation=90, fontsize=5.5, color=INK2, ha="center", va="top")
                    continue
                ax.plot([x, x], [cell["min"], cell["max"]], color=edge, lw=1.3, alpha=0.95, zorder=3, solid_capstyle="butt")
                ax.plot([x], [cell["min"]], ls="none", marker="_", ms=3.4, mew=1.1, color=edge, zorder=3)
                ax.plot([x], [cell["max"]], ls="none", marker="_", ms=3.4, mew=1.1, color=edge, zorder=3)
                ax.plot([x], [cell["geomean"]], ls="none", marker=style["marker"], ms=size, mfc=style["color"], mec=edge, mew=0.8, zorder=4)
        ax.set_xlim(-0.6, len(queries) - 0.4)
        ax.set_ylim(lo, hi)
        ax.tick_params(axis="x", labelbottom=False, length=0)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        strip.set_ylim(0, 1)
        strip.set_facecolor(SURFACE)
        strip.patch.set_hatch("////")
        strip.patch.set_edgecolor(GRID)
        for spine in ("top", "right", "left"):
            strip.spines[spine].set_visible(False)
        strip.spines["bottom"].set_color(AXIS)
        strip.set_yticks([])
        strip.set_xticks(range(len(queries)))
        strip.set_xticklabels(queries, rotation=32 if rotate else 0, ha="right" if rotate else "center", fontsize=5.8 if single else 6.0)
        strip.tick_params(axis="x", length=2, color=AXIS)
        strip.annotate("no timing", xy=(0.0, 1.0), xycoords="axes fraction", xytext=(2, -2), textcoords="offset points", fontsize=5.5, color=INK2, ha="left", va="top",
                       bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1.0})
        if not missing:
            strip.annotate("every engine ran every query", xy=(0.5, 0.45), xycoords="axes fraction", fontsize=5.5, color=INK2, ha="center", va="center",
                           bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1.5})
        for qi in range(len(queries) - 1):
            ax.axvline(qi + 0.5, color=GRID, lw=0.4, zorder=1)
            strip.axvline(qi + 0.5, color=GRID, lw=0.4, zorder=1)
        panel_title(ax, f"{SUITE_TITLE[suite]}, {scale_label(scale)}", "geometric mean over rounds; bar = min–max round")
    P.ylabel("Query time (s, log)")
    P.legend([*engine_handles(engines, marker_size=HEADLINE_MARKER, outline=True),
              Patch(facecolor=SURFACE, edgecolor=GRID, hatch="////", label="no timing (reason in the strip)")], ncol_double=5, ncol_single=2)
    finish(P.fig, ctx, "f10_headline_times" + P.tag(), "Per-query absolute times at the largest scale",
           "Absolute query times (seconds, logarithmic axis) for the four engines at the largest scale of each suite: geometric mean over rounds of the per-round "
           "median of three measured trials; vertical bars (with end caps) span the fastest to slowest round and are hidden behind the marker where the rounds agree "
           "closely. A query an engine could not run has no time: it is not placed on the time axis but listed in the hatched strip under the panel, with the engine's "
           "hollow marker and the recorded reason. Lower is faster.",
           [f"medians.{s}.{sc}.*.*" for s, sc in panels] + ["tables/t03_median_times.csv"], width_class=P.width_class(),
           alt="Dot plot of per-query time on a log axis for four engines in both suites at the largest scale, with a strip listing the queries an engine could not run.")


# ------------------------------------------------------------------ F11: GPU-generation ladder (cross-host, amendments A2/A3)

GENERATION = {"A100": "Ampere", "H100": "Hopper", "GH200": "Grace Hopper", "B200": "Blackwell"}


def grid_axes(width, ncols, nrows, panel_h, *, left, right, top, bottom, wgap, hgap):
    """Row-major inch-based grid of axes; returns (figure, axes, height)."""
    height = top + bottom + nrows * panel_h + (nrows - 1) * hgap
    fig = plt.figure(figsize=(width, height))
    pw = (width - left - right - (ncols - 1) * wgap) / ncols
    axes = []
    for r in range(nrows):
        for c in range(ncols):
            axes.append(fig.add_axes([(left + c * (pw + wgap)) / width, (bottom + (nrows - 1 - r) * (panel_h + hgap)) / height, pw / width, panel_h / height]))
    return fig, axes, height


def scale_shade(color, rank, count):
    """Engine hue darkened with the scale factor (rank 0 keeps the engine colour, the last is 55% towards black)."""
    return mix(color, "#000000", 0.55 * rank / max(count - 1, 1))


def fig_ladder(ctx, single=False):
    import textwrap
    cross = ctx.cross
    if not cross or len(cross.get("hosts", [])) < 2:
        return "no cross-host comparison (analyze_campaign.py --host LABEL=PATH ... or --synthetic writes cross_host.json)"
    hosts, ref = cross["hosts"], cross["reference"]
    ratios = {h: cross["ratio"].get(h, {}) for h in hosts if h != ref}
    engines = [e for e in ENGINES if any(e in ratios[h] for h in ratios)]
    suites = [s for s in AC.SUITES if any(s in ratios[h].get(e, {}) for h in ratios for e in engines)]
    if not engines:
        return "cross-host file has no ratios"
    scales_of = {s: sorted({sc for h in ratios for e in engines for sc in ratios[h].get(e, {}).get(s, {})}, key=AC.scale_sort_key) for s in suites}
    every_scale = sorted({sc for s in suites for sc in scales_of[s]}, key=AC.scale_sort_key)
    panels = [(s, e) for s in suites for e in engines]
    ncols = 2 if single else len(engines)
    nrows = math.ceil(len(panels) / ncols)
    width = SINGLE if single else DOUBLE
    missing = [h for h in (cross.get("hosts_not_included") or [])]
    title = f"GPU-generation ladder: suite-level time relative to {ref} by engine and host"
    if missing:
        title += f" (PARTIAL: {', '.join(missing)} not included)"
    present = [h for h in hosts if h in GENERATION]
    note = (f"Time on the host divided by time on {ref} (geometric mean of round medians over the queries timed in every round on both hosts); below 1 the host is faster. "
            f"Bars: Welch-type 95% interval, rounds unpaired. Ladder order: " + ", ".join(f"{h} ({GENERATION[h]})" for h in present) + ". "
            "Each host is a separate campaign; CPU, memory, driver and (GH200) architecture differ, so GPU-engine differences are not GPU-only effects "
            "and CPU-engine differences reflect the host CPU.")
    if missing:
        note += f" Partial ladder: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not included."
    lines = textwrap.wrap(note, int((width - 0.2) / 0.052))
    legend_rows = 3 if single else 2
    foot_h = 0.115 * len(lines) + 0.08
    panel_h = 1.05 if single else 1.42
    top = 0.25 + 0.135 * len(wrap_title(title, width)) + 0.42
    bottom = 0.52 + 0.19 * legend_rows + foot_h
    left, right = 0.56, 0.06
    fig, axes, height = grid_axes(width, ncols, nrows, panel_h, left=left, right=right, top=top, bottom=bottom, wgap=0.11, hgap=0.56 if single else 0.62)
    values = [1.0]
    for h in ratios:
        for e in engines:
            for by_scale in ratios[h].get(e, {}).values():
                for r in by_scale.values():
                    values += [r["estimate"], r["ci_low"], r["ci_high"]]
    lo, hi = with_parity(*padded_limits(values, 1.08))
    x_of = {h: i for i, h in enumerate(hosts)}
    for k, (ax, (suite, engine)) in enumerate(zip(axes, panels)):
        style = ENGINE_STYLE[engine]
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_y=True)
        reference_line(ax, "y")
        scales = scales_of[suite]
        max_queries = {sc: max([ratios[h].get(engine, {}).get(suite, {}).get(sc, {}).get("n_queries", 0) for h in ratios] or [0]) for sc in scales}
        for si, sc in enumerate(scales):
            color = scale_shade(style["color"], every_scale.index(sc), len(every_scale))
            dodge = (si - (len(scales) - 1) / 2) * 0.085        # within a host, markers run from the smallest to the largest scale
            xs, ys = [], []
            for h in hosts:
                record = ({"estimate": 1.0, "ci_low": None, "ci_high": None, "n_queries": max_queries[sc]} if h == ref
                          else ratios[h].get(engine, {}).get(suite, {}).get(sc))
                if not record or record["estimate"] is None:
                    xs.append(np.nan)
                    ys.append(np.nan)
                    continue
                x = x_of[h] + dodge
                xs.append(x)
                ys.append(record["estimate"])
                if record["ci_low"] is not None:
                    ax.plot([x, x], [record["ci_low"], record["ci_high"]], color=color, lw=0.9, zorder=3, solid_capstyle="butt")
                hollow = h != ref and record["n_queries"] < max_queries[sc]
                ax.plot([x], [record["estimate"]], ls="none", marker=style["marker"], ms=3.5, mfc=SURFACE if hollow else color, mec=color,
                        mew=0.9 if hollow else 0.4, zorder=4)
            ax.plot(xs, ys, color=color, lw=0.7, zorder=3)
        ax.set_xlim(-0.45, len(hosts) - 0.55)
        ax.set_ylim(lo, hi)
        ax.set_xticks(range(len(hosts)))
        ax.set_xticklabels(hosts, fontsize=5.8)
        for tick, h in zip(ax.get_xticklabels(), hosts):
            if h == ref:
                tick.set_fontweight("bold")
        ratio_ticks(ax, "y", lo, hi)
        if k % ncols:
            ax.tick_params(axis="y", labelleft=False)
        panel_title(ax, style["label"], f"{suite} suite, " + ("GPU engine" if engine in AC.GPU_ENGINES else "CPU engine"))
    for r in range(nrows):
        axes[r * ncols].set_ylabel(f"Time relative to {ref} (log)")
    fig.text((left + (width - left - right) / 2) / width, (foot_h + 0.19 * legend_rows + 0.09) / height, "Host, in GPU-generation order", fontsize=6.8, color=INK2, ha="center")
    scale_text = f"{scale_label(every_scale[0])} to {scale_label(every_scale[-1])}"
    handles = [Line2D([0], [0], color=INK2, lw=0.7, marker="o", ms=3.5, mfc=INK2, mec=INK2, label=f"markers: {scale_text}, left to right" if single else f"one marker per scale factor, {scale_text} left to right (lighter to darker)"),
               Line2D([0], [0], color=INK2, lw=0, marker="o", ms=3.8, mfc=SURFACE, mec=INK2, mew=0.9, label="open: fewer queries than at the other hosts"),
               Line2D([0], [0], color=INK2, lw=1.0, label="95% interval (Welch-type)")]
    fig.legend(handles=handles, loc="lower center", ncol=1 if single else 2, bbox_to_anchor=(0.5, foot_h / height), columnspacing=1.6, handlelength=2.0)
    fig.text(0.012, 0.03 / height, "\n".join(lines), ha="left", va="bottom", fontsize=5.6, color=INK2, linespacing=1.25)
    figure_title(fig, title)
    sources = ["ratio.*.*.*.*", "identity_differences.*", "host_info.*", "cross_host/tables/x02_suite_ratios.csv", "cross_host/tables/x01_hosts.csv"]
    finish(fig, ctx, "f11_gpu_ladder" + ("_1col" if single else ""), f"GPU-generation ladder relative to {ref}",
           f"Suite-level time of each engine on each host relative to {ref} (host / {ref}; geometric mean of round medians over the queries timed in every round on both hosts), "
           "hosts in GPU-generation order. Within a host, markers run from the smallest to the largest scale factor (lighter to darker), with 95% Welch-type intervals on the "
           "log scale (rounds unpaired across hosts). Open markers have fewer queries in the suite estimate than at the other hosts (cells that ran out of memory or are "
           "unsupported there). Each host is its own campaign; CPU model, architecture, host memory and driver differ, so a GPU-engine ratio is not a pure GPU-generation "
           "effect, and CPU-engine ratios reflect the host CPU."
           + (f" Partial ladder: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not included." if missing else ""),
           sources, width_class="single" if single else "double",
           alt=f"Lines of suite-level time relative to {ref} across hosts for each engine and suite.")


# ------------------------------------------------------------------ F12: GPU-idle fraction (descriptive telemetry), per host

HOST_LINE = {"A100": ":", "H100": "-", "GH200": "--", "B200": "-."}       # host identity by line style and shade; the marker stays the engine's


def fig_gpu_idle(ctx, single=False):
    import textwrap
    cross = ctx.cross
    telemetry = (cross or {}).get("gpu_telemetry") or {}
    hosts = [h for h in (cross or {}).get("hosts", []) if telemetry.get(h, {}).get("available")]
    if not hosts:
        return "no GPU telemetry (needs gpu.csv in the analysed campaigns and a cross_host.json that carries gpu_telemetry)"
    engines = [e for e in ENGINES if e in AC.GPU_ENGINES]
    suites = [s for s in AC.SUITES if any(s in telemetry[h]["cells"] for h in hosts)]
    panels = [(s, e) for s in suites for e in engines]
    label = next(iter(telemetry.values()))["label"]
    title = f"GPU-idle fraction during measured actions by scale and host ({label})"
    note = ("GPU-idle fraction: share of nvidia-smi samples (about every 200 ms) with utilization.gpu below 10% inside measured actions of at least 1 s; shorter actions "
            "(most SF1 actions and the short Polars GPU actions at small scales) are left out, so a missing point means no action of that cell reached 1 s. nvidia-smi's "
            "utilization.gpu is the share of the sample period in which a kernel was executing, not SM occupancy. Hosts are separate campaigns with different CPUs, drivers and "
            "memory; descriptive, no causal reading.")
    lines = textwrap.wrap(note, int(((SINGLE if single else DOUBLE) - 0.2) / 0.052))
    foot_h = 0.115 * len(lines) + 0.10
    P = Panels(single, len(panels), 1.50 if single else 1.65, title, legend_rows=2 if not single else 3, extra_bottom=0.16 + foot_h, grid=2, hgap=0.86 if single else 0.8)
    scales_of = {s: sorted({sc for h in hosts for sc in telemetry[h]["cells"].get(s, {})}, key=AC.scale_sort_key) for s in suites}      # from the cross-host file alone
    xs_all = [scale_number(sc) for sc in sorted({sc for v in scales_of.values() for sc in v}, key=AC.scale_sort_key)]
    peak = 100.0 * max([c[e]["idle_fraction"] for h in hosts for by_scale in telemetry[h]["cells"].values() for c in by_scale.values() for e in c
                        if c[e]["idle_fraction"] is not None] or [0.1])
    top = max(5.0, math.ceil(peak * 1.15 / 5.0) * 5.0)            # one shared range sized to the data, not to 100%
    for ax, (suite, engine) in zip(P.axes, panels):
        style = ENGINE_STYLE[engine]
        ax.set_xscale("log")
        style_axes(ax, grid="y", log_x=True)
        for k, host in enumerate(hosts):
            cells = telemetry[host]["cells"].get(suite, {})
            points = [(scale_number(sc), cells[sc][engine]["idle_fraction"]) for sc in scales_of[suite] if engine in cells.get(sc, {})
                      and cells[sc][engine]["idle_fraction"] is not None]
            if not points:
                continue
            color = scale_shade(style["color"], k, len(hosts))
            x, y = np.array([p[0] for p in points]), 100.0 * np.array([p[1] for p in points])
            ax.plot(x, y, ls=HOST_LINE.get(host, "-"), color=color, lw=1.1, zorder=3)
            ax.plot(x, y, ls="none", marker=style["marker"], ms=3.8, mfc=color, mec=SURFACE, mew=0.6, zorder=4)
        ax.set_xlim(min(xs_all) / 1.35, max(xs_all) * 1.35)
        ax.set_ylim(-0.04 * top, top)
        ax.xaxis.set_major_locator(ticker.FixedLocator(xs_all))
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
        panel_title(ax, style["label"], f"{suite} suite")
    P.ylabel("GPU-idle fraction (utilization < 10%)")
    P.xlabel("Scale factor (log scale)", legend_xlabel_y(2 if not single else 3) + foot_h)
    handles = [Line2D([0], [0], color=INK2, ls=HOST_LINE.get(h, "-"), lw=1.2, label=f"{h} ({GENERATION.get(h, 'host')})") for h in hosts]
    P.legend(handles, ncol_double=len(hosts), ncol_single=2, y_in=foot_h, handlelength=2.6)
    P.fig.text(0.012, 0.03 / P.height, "\n".join(lines), ha="left", va="bottom", fontsize=5.6, color=INK2, linespacing=1.25)
    finish(P.fig, ctx, "f12_gpu_idle_fraction" + P.tag(), "GPU-idle fraction by scale and host",
           "Share of nvidia-smi samples with utilization.gpu below 10% inside measured actions of at least one second, by scale factor, one panel per GPU engine and suite, one line per host "
           f"(line style and shade by GPU generation; the marker is the engine's). {label}. Samples come every ~200 ms; actions shorter than 1 s are left out and a scale with no action of "
           "at least 1 s has no point. utilization.gpu is the share of the sample period in which a kernel was executing, not SM occupancy. The hosts differ in CPU, memory and driver, "
           "so a difference between lines is not attributable to the GPU alone.",
           ["gpu_telemetry.*.cells.*.*.*.idle_fraction", "gpu_telemetry.*.meta", "cross_host/tables/x08_gpu_telemetry.csv"], width_class=P.width_class(),
           alt="GPU-idle fraction against scale factor for each GPU engine and suite, one line per host.")


# ------------------------------------------------------------------ paper 2 figures (exploratory, post-hoc): drawn from paper2.json, not part of the standard set

PAPER2_TAG = "exploratory (post-hoc)"
HOST_NAME = {"a100": "A100", "h100": "H100", "gh200": "GH200", "b200": "B200"}


class Paper2Context(Context):
    """A Context for figures drawn from paper2.json alone."""

    def __init__(self, paper2, out_dir, formats=FORMATS):
        super().__init__({"synthetic": bool(paper2["meta"].get("synthetic")), "campaign": {"suites": [], "scales": {}, "engines": [], "n_rounds": 0}}, out_dir, formats)
        self.paper2 = paper2


def p2_scale_sort(names):
    return sorted(names, key=AC.scale_sort_key)


def fig_baseline_sensitivity(ctx, single=False):
    import textwrap
    p2 = ctx.paper2
    hosts = p2["meta"]["hosts"]
    suites = [s for s in AC.SUITES if any(s in p2["sensitivity"].get(h, {}) for h in hosts)]
    if not suites:
        return "paper2.json has no C1 data"
    label = p2["meta"]["label"]
    title = f"Polars GPU speedup against two CPU baselines, by host: {PAPER2_TAG}; E1 is not in the locked design §9"
    note = ("C1 (solid, filled): Polars CPU 1.44.2 / Polars GPU, the locked-design contrast of the host's own campaign, 95% t-interval over rounds. E1 (dashed, hollow): Polars CPU 2.0.0 "
            "(side-study sessions) / Polars GPU (main rounds), unpaired sessions so a Welch-type 95% interval on the log scale. Above 1 the GPU is faster. A column is empty when the "
            "host's side study (the Polars 2.0.0 bridge) is not available. E1 is " + label + ". Under each host: the --cpus threads and the physical cores they can occupy (lscpu).")
    width = SINGLE if single else DOUBLE
    lines = textwrap.wrap(note, int((width - 0.2) / 0.052))
    foot_h = 0.115 * len(lines) + 0.10
    ncols, nrows = (len(suites), len(hosts)) if single else (len(hosts), len(suites))
    legend_rows = 2 if single else 1
    top = 0.25 + 0.135 * len(wrap_title(title, width)) + 0.42
    bottom = 0.52 + 0.19 * legend_rows + foot_h
    panel_h = 1.12 if single else 1.45
    fig, axes, height = grid_axes(width, ncols, nrows, panel_h, left=0.56, right=0.10, top=top, bottom=bottom, wgap=0.12, hgap=0.62 if single else 0.55)
    cells = {(s, h): [(sc, r) for sc, r in sorted(p2["sensitivity"].get(h, {}).get(s, {}).items(), key=lambda kv: AC.scale_sort_key(kv[0]))] for s in suites for h in hosts}
    ranges = {}
    for s in suites:
        vals = [v for h in hosts if p2["hosts"][h]["bridge_available"] for _, r in cells[(s, h)] for k in ("c1", "e1") if r.get(k) for v in (r[k]["estimate"], r[k]["ci_low"], r[k]["ci_high"])]
        ranges[s] = with_parity(*padded_limits(vals, 1.12))
    xs_all = [scale_number(sc) for sc in p2_scale_sort({sc for s in suites for h in hosts for sc, _ in cells[(s, h)]})]
    style = ENGINE_STYLE["polars_gpu"]
    color = style["color"]
    for index, ax in enumerate(axes):
        r, c = divmod(index, ncols)
        suite, host = (suites[c], hosts[r]) if single else (suites[r], hosts[c])
        info = p2["hosts"][host]
        ax.set_xscale("log")
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_x=True, log_y=True)
        reference_line(ax, "y")
        lo, hi = ranges[suite]
        ax.set_ylim(lo, hi)
        ax.set_xlim(min(xs_all) / 1.35, max(xs_all) * 1.35)
        ax.xaxis.set_major_locator(ticker.FixedLocator(xs_all))
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ratio_ticks(ax, "y", lo, hi)
        panel_title(ax, HOST_NAME.get(host, host.upper()) + (f"  {SUITE_TITLE[suite]}" if single else ""), (info.get("cores_text") or "") if not single else f"{info.get('cores_text') or ''}")
        if not info["bridge_available"]:
            ax.annotate("bridge not available", xy=(0.5, 0.5), xycoords="axes fraction", ha="center", va="center", fontsize=6.4, color=INK2)
            continue
        for key, ls, filled, band_alpha, hatch in (("c1", "-", True, 0.18, None), ("e1", "--", False, 0.10, "////")):
            pts = [(scale_number(sc), rec[key]) for sc, rec in cells[(suite, host)] if rec.get(key) and rec[key]["estimate"] is not None]
            if not pts:
                continue
            x, est = np.array([p[0] for p in pts]), np.array([p[1]["estimate"] for p in pts])
            if all(p[1]["ci_low"] is not None for p in pts):
                ax.fill_between(x, [p[1]["ci_low"] for p in pts], [p[1]["ci_high"] for p in pts], color=color, alpha=band_alpha, lw=0, zorder=2, hatch=hatch, edgecolor=color if hatch else None)
            ax.plot(x, est, ls=ls, color=color, lw=1.2, zorder=3)
            ax.plot(x, est, ls="none", marker=style["marker"], ms=4.4, mfc=color if filled else SURFACE, mec=color if not filled else SURFACE, mew=1.0 if not filled else 0.7, zorder=4)
    for index, ax in enumerate(axes):
        r, c = divmod(index, ncols)
        if c:
            ax.tick_params(axis="y", labelleft=False)
    for r in range(nrows):
        axes[r * ncols].set_ylabel("Polars GPU speedup (log)")
    fig.text((0.56 + (width - 0.66) / 2) / width, (foot_h + 0.19 * legend_rows + 0.09) / height, "Scale factor (log scale)", fontsize=6.8, color=INK2, ha="center")
    handles = [Line2D([0], [0], color=color, ls="-", lw=1.4, marker=style["marker"], mfc=color, mec=SURFACE, ms=5, label="C1: vs Polars CPU 1.44.2 (locked design)"),
               Line2D([0], [0], color=color, ls="--", lw=1.4, marker=style["marker"], mfc=SURFACE, mec=color, mew=1.0, ms=5, label="E1: vs Polars CPU 2.0.0 (exploratory, post-hoc)")]
    fig.legend(handles=handles, loc="lower center", ncol=1 if single else 2, bbox_to_anchor=(0.5, foot_h / height), columnspacing=1.6, handlelength=3.0)
    fig.text(0.012, 0.03 / height, "\n".join(lines), ha="left", va="bottom", fontsize=5.6, color=INK2, linespacing=1.25)
    figure_title(fig, title)
    finish(fig, ctx, "f20_baseline_sensitivity" + ("_1col" if single else ""), f"Polars GPU speedup against two CPU baselines ({PAPER2_TAG})",
           "Speedup of Polars GPU over the Polars CPU baseline, by host, suite and scale factor. The solid line is C1, the locked-design contrast: Polars CPU 1.44.2 over Polars GPU, with the 95% "
           "t-interval over rounds. The dashed line is E1, an " + label + " analysis: the CPU side is Polars 2.0.0 from the side-study sessions, the GPU side the main rounds; the sessions are "
           "unpaired, so its band is a Welch-type 95% interval on the log scale. Above 1 the GPU is faster; the horizontal line marks parity and the y axis is shared within a suite. A host "
           "column is empty when its side study is not available. Each host's panel title gives the --cpus threads and the physical cores they can occupy (lscpu).",
           ["sensitivity.*.*.*.c1", "sensitivity.*.*.*.e1", "hosts.*.cores_text", "hosts.*.bridge_available"], width_class="single" if single else "double",
           alt="Polars GPU speedup against scale factor for two CPU baselines, one panel per host and suite.")


def fig_speedup_decomposition(ctx, single=False):
    import textwrap
    p2 = ctx.paper2
    hosts = [h for h in p2["meta"]["hosts"] if h in p2["decomp"] and p2["decomp"][h]]
    reference = p2["meta"]["reference"]
    if not hosts:
        return "paper2.json has no decomposition (needs a host besides the reference)"
    suites = [s for s in AC.SUITES if any(s in p2["decomp"][h].get(c, {}) for h in hosts for c in ("c1", "c2"))]
    contrasts = [("c1", "Polars", "polars", "polars_gpu"), ("c2", "Spark", "spark_cpu", "spark_gpu")]
    ref_name = HOST_NAME.get(reference, reference.upper())
    title = f"Cross-host speedup decomposition against {ref_name}: {PAPER2_TAG}"
    note = (f"Filled cross: speedup of the host divided by the speedup of {ref_name} (C_host / C_{ref_name}). Open markers: the CPU engine's and the GPU engine's time on the host divided by "
            f"its time on {ref_name}, over the same queries (engine colours and markers as elsewhere). log(speedup ratio) = log(CPU-engine ratio) - log(GPU-engine ratio). Below 1 on the "
            "CPU-engine ratio means the host's CPU engine was faster than on the reference, which lowers that host's speedup. Welch-type 95% intervals on the log scale (separate campaigns, "
            f"unpaired rounds). {ref_name}: {p2['hosts'][reference].get('cores_text')}. " + p2["meta"]["label"].capitalize() + ".")
    width = SINGLE if single else DOUBLE
    lines = textwrap.wrap(note, int((width - 0.2) / 0.052))
    foot_h = 0.115 * len(lines) + 0.10
    ncols = 1 if single else len(contrasts)
    panels = [(s, c) for s in suites for c in contrasts] if not single else [(s, c) for c in contrasts for s in suites]
    nrows = math.ceil(len(panels) / ncols)
    legend_rows = 2
    top = 0.25 + 0.135 * len(wrap_title(title, width)) + 0.42
    bottom = 0.86 + 0.19 * legend_rows + foot_h
    panel_h = 1.30 if single else 1.55
    fig, axes, height = grid_axes(width, ncols, nrows, panel_h, left=0.56, right=0.10, top=top, bottom=bottom, wgap=0.50, hgap=0.86)
    vals = [v for h in hosts for c, *_ in contrasts for su in p2["decomp"][h].get(c, {}).values() for rec in su.values()
            for k in ("speedup_ratio", "cpu_side", "gpu_side") for v in (rec[k]["estimate"], rec[k]["ci_low"], rec[k]["ci_high"])]
    lo, hi = with_parity(*padded_limits(vals, 1.1))
    for ax, (suite, (c, name, cpu_engine, gpu_engine)) in zip(axes, panels):
        ax.set_yscale("log")
        style_axes(ax, grid="y", log_y=True)
        reference_line(ax, "y")
        ax.set_ylim(lo, hi)
        ratio_ticks(ax, "y", lo, hi)
        scales = p2_scale_sort({sc for h in hosts for sc in p2["decomp"][h].get(c, {}).get(suite, {})})
        ticks, labels, centres, cursor = [], [], [], 0.0
        for host in hosts:
            records = p2["decomp"][host].get(c, {}).get(suite, {})
            start = cursor
            for j, sc in enumerate(scales):
                x0 = cursor + j
                if sc in records:
                    rec = records[sc]
                    for dx, key, engine, marker, filled in ((-0.26, "cpu_side", cpu_engine, ENGINE_STYLE[cpu_engine]["marker"], False),
                                                             (0.0, "speedup_ratio", None, "P", True), (0.26, "gpu_side", gpu_engine, ENGINE_STYLE[gpu_engine]["marker"], False)):
                        col = ENGINE_STYLE[engine]["color"] if engine else INK
                        r_ = rec[key]
                        if r_["ci_low"] is not None:
                            ax.plot([x0 + dx, x0 + dx], [r_["ci_low"], r_["ci_high"]], color=col, lw=0.9, zorder=3, solid_capstyle="butt")
                        ax.plot([x0 + dx], [r_["estimate"]], ls="none", marker=marker, ms=4.6 if marker == "P" else 4.2, mfc=col if filled else SURFACE, mec=col if not filled else SURFACE,
                                mew=1.0 if not filled else 0.6, zorder=4)
                ticks.append(x0)
                labels.append(scale_label(sc))
            centres.append((host, start + (len(scales) - 1) / 2))
            if host != hosts[-1]:
                ax.axvline(cursor + len(scales) - 0.5 + 0.3, color=GRID, lw=0.5, zorder=1)
            cursor += len(scales) + 0.6
        ax.set_xlim(-0.7, cursor - 0.6 + 0.0)
        ax.set_xticks(ticks)
        ax.set_xticklabels([f"{scale_number(sc):g}" for sc in [l for l in scales] * len(hosts)], fontsize=5.6)
        for host, centre in centres:
            ax.annotate(f"{HOST_NAME.get(host, host.upper())}\n{(p2['hosts'][host].get('cores_text') or '').replace(' on ', ' on' + chr(10))}", xy=(centre, 0.0), xycoords=("data", "axes fraction"), xytext=(0, -14),
                        textcoords="offset points", fontsize=5.6, color=INK2, ha="center", va="top", annotation_clip=False, linespacing=1.15)
        panel_title(ax, f"{name}: {c.upper()} ({ENGINE_STYLE[cpu_engine]['label']} / {ENGINE_STYLE[gpu_engine]['label']})", f"{suite} suite")
    for index, ax in enumerate(axes):
        if index % ncols == 0:
            ax.set_ylabel(f"Host / {ref_name} (log)")
    handles = [Line2D([0], [0], marker="P", color=INK, mfc=INK, mec=SURFACE, ls="none", ms=5.2, label=f"speedup ratio C_host / C_{ref_name}")]
    for e in ("polars", "polars_gpu", "spark_cpu", "spark_gpu"):
        st = ENGINE_STYLE[e]
        handles.append(Line2D([0], [0], marker=st["marker"], color=st["color"], mfc=SURFACE, mec=st["color"], mew=1.0, ls="none", ms=5.0, label=f"{st['label']} time, host / {ref_name}"))
    fig.legend(handles=handles, loc="lower center", ncol=2 if single else 3, bbox_to_anchor=(0.5, foot_h / height), columnspacing=1.4, handlelength=1.6)
    fig.text(0.012, 0.03 / height, "\n".join(lines), ha="left", va="bottom", fontsize=5.6, color=INK2, linespacing=1.25)
    figure_title(fig, title)
    finish(fig, ctx, "f21_speedup_decomposition" + ("_1col" if single else ""), f"Cross-host speedup decomposition against {ref_name} ({PAPER2_TAG})",
           f"How much of each host's difference in GPU speedup from {ref_name} comes from the CPU engine and how much from the GPU engine, for the Polars contrast C1 and the Spark contrast C2, "
           "by suite and scale factor, hosts in GPU-generation order. The filled cross is the host's speedup divided by the reference speedup; the open markers are the CPU engine's and the "
           f"GPU engine's time on the host divided by its time on {ref_name}, over the same queries, so that log(speedup ratio) = log(CPU-engine ratio) - log(GPU-engine ratio). A CPU-engine "
           "ratio below 1 means the host's CPU engine was faster than on the reference, which lowers that host's speedup. Intervals are Welch-type 95% on the log scale (separate campaigns). "
           "Under each host: the --cpus threads and the physical cores they can occupy (lscpu). " + p2["meta"]["label"].capitalize() + ".",
           ["decomp.*.c1.*.*", "decomp.*.c2.*.*", "hosts.*.cores_text"], width_class="single" if single else "double",
           alt="Host over reference ratios of speedup, CPU-engine time and GPU-engine time for the Polars and Spark contrasts, by scale factor.")


PAPER2_FIGURES = {"f20": (fig_baseline_sensitivity, True), "f21": (fig_speedup_decomposition, True)}


def build_paper2_figures(paper2, out_dir, only=None, formats=FORMATS, widths=("double", "single")):
    """Draw f20 and f21 from paper2.json; returns the Context (manifest in ctx.manifest, also written to figures.json)."""
    configure_matplotlib()
    ctx = Paper2Context(paper2, out_dir, formats)
    skipped = []
    for key, (draw, has_single) in PAPER2_FIGURES.items():
        if only and key not in only:
            continue
        for width in widths:
            if width == "single" and not has_single:
                continue
            reason = draw(ctx, width == "single")
            if isinstance(reason, str):
                skipped.append({"id": key, "width": width, "reason": reason})
    ctx.manifest.sort(key=lambda m: m["id"])
    manifest = {"synthetic": ctx.synthetic, "watermark": WATERMARK if ctx.synthetic else None, "generated_from": ["paper2.json"],
                "schema_version": paper2["meta"].get("schema_version"), "figures": ctx.manifest, "skipped": skipped}
    (ctx.out_dir / "figures.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return ctx


# ------------------------------------------------------------------ driver

# figure id -> (draw function, has a one-column variant)
FIGURES = {
    "f01": (lambda ctx, single: [fig_forest(ctx, s) for s in ctx.all_scales], False),
    "f02": (fig_suite_speedup, True),
    "f02x": (fig_exploratory, True),
    "f03": (lambda ctx, single: [fig_heatmap(ctx, s) for s in ctx.suites], False),
    "f04": (fig_coverage, True),
    "f05": (fig_variability, True),
    "f06": (fig_order, True),
    "f07": (fig_gpu_memory, True),
    "f08": (fig_version_bridge, True),
    "f09": (fig_a100, True),
    "f10": (fig_headline, True),
    "f11": (fig_ladder, True),
    "f12": (fig_gpu_idle, True),
}
CROSS_ONLY = ("f11", "f12")   # figures drawn from cross_host.json alone (no per-host stats.json needed)


def load_stats(path):
    path = Path(path)
    path = path / "stats.json" if path.is_dir() else path
    return json.loads(path.read_text()), path.parent


def load_cross(path):
    """cross_host.json from a file, a directory holding it, or None when the path has none."""
    path = Path(path)
    path = path / "cross_host.json" if path.is_dir() else path
    return json.loads(path.read_text()) if path.is_file() else None


def find_cross(base):
    """The cross-host comparison next to a stats.json: <dir>/cross_host.json or <dir>/cross_host/cross_host.json."""
    for candidate in (Path(base) / "cross_host.json", Path(base) / "cross_host" / "cross_host.json"):
        if candidate.is_file():
            return load_cross(candidate)
    return None


def build_figures(stats, out_dir, only=None, formats=FORMATS, headline_scale=None, widths=("double", "single"), cross=None):
    """Draw every figure the inputs allow. stats may be None when only cross-host figures are wanted."""
    configure_matplotlib()
    stub = {"synthetic": bool((cross or {}).get("synthetic")), "campaign": {"suites": [], "scales": {}, "engines": [], "n_rounds": 0}}
    ctx = Context(stats or stub, out_dir, formats, headline_scale, cross)
    skipped = []
    for key, (draw, has_single) in FIGURES.items():
        if only and key not in only:
            continue
        if stats is None and key not in CROSS_ONLY:
            continue
        for width in widths:
            if width == "single" and not has_single:
                continue
            reason = draw(ctx, width == "single")
            if isinstance(reason, str):
                skipped.append({"id": key, "width": width, "reason": reason})
    ctx.manifest.sort(key=lambda m: m["id"])
    manifest = {"synthetic": ctx.synthetic, "watermark": WATERMARK if ctx.synthetic else None,
                "generated_from": ["stats.json"] * (stats is not None) + ["cross_host.json"] * (cross is not None),
                "schema_version": (stats or {}).get("schema_version") or (cross or {}).get("schema_version"), "figures": ctx.manifest, "skipped": skipped}
    (ctx.out_dir / "figures.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return ctx


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", type=Path, help="analysis directory (or stats.json) written by analyze_campaign.py")
    parser.add_argument("--cross-host", type=Path, help="cross_host.json (or its directory) for F11; default: next to --stats (cross_host.json or cross_host/)")
    parser.add_argument("--output", type=Path, help="figure directory (default: <stats dir>/figures; with --synthetic: the analysis root)")
    parser.add_argument("--only", nargs="*", help="figure ids to draw, e.g. f01 f02 (default all)")
    parser.add_argument("--formats", nargs="+", default=list(FORMATS), choices=FORMATS)
    parser.add_argument("--headline-scale", help="scale key for f10 (default: largest scale of each suite)")
    parser.add_argument("--width", choices=("double", "single", "both"), default="both",
                        help="two-column (7.0 in) figures, one-column (3.4 in, files *_1col) variants where a layout exists, or both")
    parser.add_argument("--paper2", type=Path, help="paper2.json (or its directory) written by analyze_campaign.py --paper2: draws f20_baseline_sensitivity and f21_speedup_decomposition only")
    parser.add_argument("--synthetic", action="store_true", help="generate fake campaigns (four hosts), analyze them, and draw watermarked figures")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    if args.paper2 is not None:
        path = args.paper2 / "paper2.json" if args.paper2.is_dir() else args.paper2
        paper2 = json.loads(path.read_text())
        out_dir = args.output or path.parent / "figs"
        widths = ("double", "single") if args.width == "both" else (args.width,)
        ctx = build_paper2_figures(paper2, out_dir, args.only, args.formats, widths)
        print(f"wrote {len(ctx.manifest)} figures ({', '.join(ctx.formats)}) to {out_dir}")
        return 0
    cross = None
    if args.synthetic:
        if args.output is None:
            parser.error("--output is required with --synthetic")
        code = AC.main(["--synthetic", "--seed", str(args.seed), "--output", str(args.output)])
        if code:
            return code
        stats, base = load_stats(args.output)
        cross = find_cross(args.output)
    elif args.stats is not None:
        stats, base = load_stats(args.stats)
        cross = find_cross(base)
    elif args.cross_host is not None:
        stats, base = None, args.cross_host if args.cross_host.is_dir() else args.cross_host.parent
    else:
        parser.error("--stats or --cross-host is required unless --synthetic is given")
    if args.cross_host is not None:
        cross = load_cross(args.cross_host)
        if cross is None:
            parser.error(f"no cross_host.json at {args.cross_host}")
    out_dir = (args.output / "figures") if args.synthetic else (args.output or base / "figures")
    widths = ("double", "single") if args.width == "both" else (args.width,)
    ctx = build_figures(stats, out_dir, args.only, args.formats, args.headline_scale, widths, cross)
    print(f"wrote {len(ctx.manifest)} figures ({', '.join(ctx.formats)}) to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

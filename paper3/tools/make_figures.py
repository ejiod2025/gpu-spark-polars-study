#!/usr/bin/env python3
"""Paper 3 figures from analyze_paper3.py's stats.json. Display only: it reads values, computes no estimate.

  make_figures.py --stats OUT/stats.json [--output paper3/figures]

f01_energy_forest.pdf (full width): X1 (Polars CPU / Polars GPU), X2 (Spark CPU / Spark GPU) and X3 (best CPU engine / best GPU
  engine) energy ratios rho = reference / engine at the measured scopes (S2-pinned on C-GCP over S1 on the GPU host), per GPU host
  and suite (columns) and per scale (rows of each panel); top row total energy, bottom row above-idle energy.
f02_time_vs_price.pdf (column width): the X3 time ratio t_C / t_G (time against C-GCP, the time factor of B1) per scale, per GPU
  host (rows) and suite (columns), against the B1 price ratio p_G / p_C of that host under P-NEW (solid line) and P-OLD (dashed),
  both read from stats.json (price_ratios). Points above a line are cheaper on the GPU host under that price table.

Display rules, fixed on 2026-11-24 before any campaign data existed (the lead's list); they are not tuned to values:
  1. Ratio axes are logarithmic, with a reference line at 1 and a shaded +-10% band [1/1.10, 1.10].
  2. A 95% interval is drawn as whiskers. A cell that is not estimable is drawn as a grey cross on the reference line and has
     a legend entry; no cell is dropped silently. A host absent from stats.json is drawn as an empty panel marked "not run".
  3. One colour per contrast, the same in every figure: X1 (Polars) #2a78d6, X2 (Spark) #eb6834, X3 (best engines) #1baf7a,
     the first three slots of the dataviz reference palette, validated all-pairs for print (light surface): worst CVD
     Delta E 9.2, worst normal-vision Delta E 24.0. X3's aqua is below 3:1 contrast, so identity is never colour alone: every
     figure has a labelled legend and class markers, and the paper gives the same values as tables.
  4. The class is shown by marker shape as well as colour: triangle right = GPU better, triangle left = CPU better,
     diamond = no difference, circle = inconclusive (the analysis's class words, design 5.3).
  5. A value from one round (an estimate without an interval) is drawn hollow, without whiskers.
  6. Vector PDF, 7.0 in (full width) or 3.33 in (sigconf column) wide, TrueType fonts embedded, no creation date: the same
     stats.json gives byte-identical files.
  7. No title inside a figure (the caption carries it); panel labels name the host and suite, axis labels the quantity.
  8. Axis range by a fixed rule, never by hand: symmetric in log space around 1, covering every plotted estimate, interval end
     and reference value and the +-10% band, plus a 10% margin; one range for all panels of a figure.
  9. A stats.json from a synthetic tree (_meta.synthetic) is watermarked "SYNTHETIC - NOT RESULTS" on every figure.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

PAPER = Path(__file__).resolve().parent.parent
GPU_HOSTS = (("h100", "G-H100"), ("a100", "G-A100"))
SUITES = (("baseline", ("1", "10", "30", "100", "300")), ("complex", ("30", "100", "300")))
CONTRASTS = (("x1", "X1 Polars CPU / GPU", "#2a78d6"), ("x2", "X2 Spark CPU / GPU", "#eb6834"),
             ("x3", "X3 best CPU / best GPU", "#1baf7a"))
MARKERS = {"gpu_better": ">", "cpu_better": "<", "no_difference": "D", "inconclusive": "o"}
CLASS_LABELS = (("gpu_better", "GPU better"), ("cpu_better", "CPU better"), ("no_difference", "no difference"),
                ("inconclusive", "inconclusive"))
INK, MUTED, GRID, BAND, NEUTRAL = "#0b0b0b", "#52514e", "#d9d8d4", "#ecebe7", "#8a8984"
MARGIN, BAND_EDGE = 1.10, 1.10
COLUMN_IN, FULL_IN = 3.33, 7.0
TICKS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 6.5, "axes.labelsize": 6.5, "xtick.labelsize": 6, "ytick.labelsize": 6,
    "legend.fontsize": 6, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "pdf.fonttype": 42,
    "svg.hashsalt": "paper3", "path.simplify": False,
})


def record(stats: dict, *path):
    node = stats
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


def finite(value):
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def log_range(values) -> tuple:
    """Rule 8: symmetric in log space around 1, covering every value and the +-10% band, plus a 10% margin."""
    half = max([abs(math.log(v)) for v in values if finite(v)] + [math.log(BAND_EDGE)])
    half *= MARGIN
    return math.exp(-half), math.exp(half)


def set_log_axis(ax, limits, axis: str):
    lo, hi = limits
    ticks = [t for t in TICKS if lo <= t <= hi]
    labels = [f"{t:g}" for t in ticks]
    if axis == "x":
        ax.set_xscale("log"); ax.set_xlim(lo, hi); ax.set_xticks(ticks, labels); ax.minorticks_off()
        ax.axvspan(1 / BAND_EDGE, BAND_EDGE, color=BAND, zorder=0, lw=0)
        ax.axvline(1, color=MUTED, lw=0.6, zorder=1)
    else:
        ax.set_yscale("log"); ax.set_ylim(lo, hi); ax.set_yticks(ticks, labels); ax.minorticks_off()
        ax.axhspan(1 / BAND_EDGE, BAND_EDGE, color=BAND, zorder=0, lw=0)
        ax.axhline(1, color=MUTED, lw=0.6, zorder=1)


def draw_point(ax, rec, other, colour, vertical: bool):
    """One cell: whiskers for the interval, a marker by class, hollow for one round, a grey cross if not estimable."""
    est = rec.get("estimate") if rec else None
    if not finite(est):
        xy = (other, 1) if vertical else (1, other)
        ax.plot(*xy, marker="x", ms=4, mew=0.9, color=NEUTRAL, zorder=4, ls="none")
        return
    lo, hi = rec.get("ci_low"), rec.get("ci_high")
    marker = MARKERS.get(rec.get("class"), "o")
    one_round = not (finite(lo) and finite(hi))
    if not one_round:
        if vertical:
            ax.plot([other, other], [lo, hi], color=colour, lw=0.9, zorder=2, solid_capstyle="butt")
        else:
            ax.plot([lo, hi], [other, other], color=colour, lw=0.9, zorder=2, solid_capstyle="butt")
    xy = (other, est) if vertical else (est, other)
    ax.plot(*xy, marker=marker, ms=4.2, mew=0.8, color=colour, mfc="none" if one_round else colour, mec=colour,
            zorder=3, ls="none")


def legend_handles(contrasts):
    handles = [Line2D([], [], color=c, lw=0.9, marker="s", ms=3.5, label=label) for _, label, c in contrasts]
    handles += [Line2D([], [], color=MUTED, marker=MARKERS[k], ms=4, ls="none", label=label) for k, label in CLASS_LABELS]
    handles += [Line2D([], [], color=MUTED, marker="o", mfc="none", ms=4, ls="none", label="one round, no interval"),
                Line2D([], [], color=NEUTRAL, marker="x", ms=4, mew=0.9, ls="none", label="not estimable"),
                Patch(facecolor=BAND, edgecolor="none", label="±10% band")]
    return handles


def watermark(fig, synthetic: bool):
    if synthetic:
        fig.text(0.5, 0.5, "SYNTHETIC - NOT RESULTS", ha="center", va="center", rotation=25, fontsize=22,
                 color="#e34948", alpha=0.25, zorder=10)


def save(fig, path: Path):
    fig.savefig(path, format="pdf", metadata={"CreationDate": None, "ModDate": None, "Producer": None,
                                             "Creator": "paper3/tools/make_figures.py"})
    plt.close(fig)


def f01_energy_forest(stats: dict, out: Path) -> Path:
    metrics = (("energy_total", "total energy"), ("energy_above_idle", "above-idle energy"))
    cols = [(h, hl, s, scales) for h, hl in GPU_HOSTS for s, scales in SUITES]
    values = []
    for cx, _, _ in CONTRASTS:
        for h, _, s, scales in cols:
            for m, _ in metrics:
                for sf in scales:
                    rec = record(stats, cx, h, s, "sf" + sf, m) or {}
                    values += [rec.get("estimate"), rec.get("ci_low"), rec.get("ci_high")]
    limits = log_range(values)
    fig, axes = plt.subplots(len(metrics), len(cols), figsize=(FULL_IN, 3.4), sharex=True,
                             gridspec_kw={"width_ratios": [1] * len(cols), "height_ratios": [1, 1]})
    offsets = {"x1": -0.25, "x2": 0.0, "x3": 0.25}
    for r, (m, mlabel) in enumerate(metrics):
        for c, (h, hlabel, s, scales) in enumerate(cols):
            ax = axes[r][c]
            set_log_axis(ax, limits, "x")
            ax.set_ylim(len(scales) - 0.5, -0.5)
            ax.set_yticks(range(len(scales)), [f"SF{sf}" for sf in scales])
            ax.grid(axis="x", color=GRID, lw=0.4, zorder=0)
            ax.spines[["top", "right"]].set_visible(False)
            if r == 0:
                ax.set_title(f"{hlabel}, {s}", fontsize=6.5, color=INK, pad=3)
            if c == 0:
                ax.set_ylabel(mlabel)
            if h not in (stats.get("x3") or {}):
                ax.text(0.5, 0.5, "not run", transform=ax.transAxes, ha="center", va="center", color=MUTED)
                continue
            for cx, _, colour in CONTRASTS:
                for i, sf in enumerate(scales):
                    draw_point(ax, record(stats, cx, h, s, "sf" + sf, m), i + offsets[cx], colour, vertical=False)
    fig.supxlabel("ρ = CPU-side / GPU-side energy at the measured scopes (log scale; > 1: the GPU side used less)",
                  fontsize=6.5, color=INK, y=0.105)
    fig.legend(handles=legend_handles(CONTRASTS), loc="lower center", ncol=5, frameon=False, bbox_to_anchor=(0.5, 0.0))
    fig.subplots_adjust(left=0.075, right=0.99, top=0.93, bottom=0.2, wspace=0.28, hspace=0.18)
    watermark(fig, bool((stats.get("_meta") or {}).get("synthetic")))
    path = out / "f01_energy_forest.pdf"
    save(fig, path)
    return path


def f02_time_vs_price(stats: dict, out: Path) -> Path:
    colour = dict((cx, c) for cx, _, c in CONTRASTS)["x3"]
    values, prices = [], {}
    for h, _ in GPU_HOSTS:
        for table in ("p_new", "p_old"):
            node = record(stats, "price_ratios", h, table, "b1")
            prices[(h, table)] = node.get("p_gpu_over_p_cpu") if node else None
            values.append(prices[(h, table)])
        for s, scales in SUITES:
            for sf in scales:
                rec = record(stats, "x3", h, s, "sf" + sf, "time_vs_cgcp") or {}
                values += [rec.get("estimate"), rec.get("ci_low"), rec.get("ci_high")]
    limits = log_range(values)
    fig, axes = plt.subplots(len(GPU_HOSTS), len(SUITES), figsize=(COLUMN_IN, 3.5), sharey=True,
                             gridspec_kw={"width_ratios": [len(sc) for _, sc in SUITES]})
    for r, (h, hlabel) in enumerate(GPU_HOSTS):
        for c, (s, scales) in enumerate(SUITES):
            ax = axes[r][c]
            set_log_axis(ax, limits, "y")
            ax.set_xlim(-0.6, len(scales) - 0.4)
            ax.set_xticks(range(len(scales)), [f"SF{sf}" for sf in scales])
            ax.grid(axis="y", color=GRID, lw=0.4, zorder=0)
            ax.spines[["top", "right"]].set_visible(False)
            ax.set_title(f"{hlabel}, {s}", fontsize=6.5, color=INK, pad=3)
            if h not in (stats.get("x3") or {}):
                ax.text(0.5, 0.5, "not run", transform=ax.transAxes, ha="center", va="center", color=MUTED)
                continue
            for table, style in (("p_new", "-"), ("p_old", "--")):
                p = prices.get((h, table))
                if finite(p):
                    ax.axhline(p, color=INK, lw=0.7, ls=style, zorder=1)
            for i, sf in enumerate(scales):
                draw_point(ax, record(stats, "x3", h, s, "sf" + sf, "time_vs_cgcp"), i, colour, vertical=True)
            if c == 0:
                ax.set_ylabel("t$_C$ / t$_G$ (log)")
    handles = [Line2D([], [], color=INK, lw=0.7, ls="-", label="price ratio P-NEW"),
               Line2D([], [], color=INK, lw=0.7, ls="--", label="price ratio P-OLD")]
    handles += legend_handles([c for c in CONTRASTS if c[0] == "x3"])
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 0.0),
               handlelength=1.6, columnspacing=1.2)
    fig.subplots_adjust(left=0.15, right=0.98, top=0.94, bottom=0.34, wspace=0.12, hspace=0.45)
    watermark(fig, bool((stats.get("_meta") or {}).get("synthetic")))
    path = out / "f02_time_vs_price.pdf"
    save(fig, path)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", type=Path, required=True, help="analyze_paper3.py OUT/stats.json")
    parser.add_argument("--output", type=Path, default=PAPER / "figures")
    args = parser.parse_args(argv)
    stats = json.loads(args.stats.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    for path in (f01_energy_forest(stats, args.output), f02_time_vs_price(stats, args.output)):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

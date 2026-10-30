#!/usr/bin/env python3
"""Copy the campaign figures into paper/figures/.

H100 figures (F1-F10) come from the H100 plot output (--plots). F11, the GPU-generation
ladder (cross-host comparison, amendments A2/A3), comes from --cross-host-plots (default:
--plots) and is required only when at least one additional host ran. Every additional
host's outcome must be given: --host a100=ran|not-run --host gh200=... --host b200=....
For each stem the script copies <stem>.pdf
(preferred by LaTeX) and/or <stem>.png. Use --map NAME=STEM if the plotter's stem
differs. Exit status 1 when a required figure is missing, unless --allow-missing.
"""

import argparse
import shutil
import sys
from pathlib import Path

PAPER = Path(__file__).resolve().parent.parent
H100_FIGURES = {
    "F1": ["f01_forest_sf300", "f01_forest_sf1", "f01_forest_sf10", "f01_forest_sf30", "f01_forest_sf100"],
    "F2": ["f02_suite_speedup_vs_scale"],
    "F2x": ["f02x_c1_e1_exploratory"],  # exploratory (post-hoc), shown only in that subsection
    "F3": ["f03_heatmap_baseline", "f03_heatmap_complex"],
    "F4": ["f04_gpu_coverage"],
    "F5": ["f05_variability"],
    "F6": ["f06_order_effects"],
    "F7": ["f07_gpu_memory"],
    "F8": ["f08_polars_version_bridge"],
    "F9": ["f09_a100_vs_h100"],
    "F10": ["f10_headline_times"],
}
CROSS_HOST_FIGURES = {"F11": ["f11_gpu_ladder"],  # the stem plot_campaign.py writes (pinned by its tests)
                      "F12": ["f12_gpu_idle_fraction"]}  # descriptive GPU telemetry
# Fallback source stems, tried with a warning when the paper name is absent (an earlier
# plot_campaign.py name).
SOURCE_ALIASES = {"f11_gpu_ladder": ["f11_gpu_generation_ladder"]}
HOSTS = ("a100", "gh200", "b200")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plots", type=Path, required=True, help="H100 plot output directory")
    parser.add_argument("--cross-host-plots", type=Path, help="directory holding F11 (default: --plots)")
    parser.add_argument("--host", action="append", default=[], metavar="HOST=ran|not-run",
                        help="outcome of each additional host campaign (all of a100, gh200, b200)")
    parser.add_argument("--output", type=Path, default=PAPER / "figures")
    parser.add_argument("--map", action="append", default=[], metavar="NAME=STEM",
                        help="source stem for a paper figure name")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args(argv)

    outcome = {}
    for item in args.host:
        host, _, state = item.partition("=")
        if host not in HOSTS or state not in ("ran", "not-run") or host in outcome:
            parser.error(f"--host {item!r}: expected HOST=ran|not-run once per HOST in {', '.join(HOSTS)}")
        outcome[host] = state
    if set(outcome) != set(HOSTS):
        parser.error("state every additional host: --host " + " --host ".join(f"{h}=ran|not-run" for h in HOSTS))

    groups = [(args.plots, H100_FIGURES)]
    if "ran" in outcome.values():
        groups.append((args.cross_host_plots or args.plots, CROSS_HOST_FIGURES))
    names = {n for _, figs in groups for ns in figs.values() for n in ns}
    stems = {n: n for n in names}
    for item in args.map:
        name, _, stem = item.partition("=")
        if name not in stems or not stem:
            parser.error(f"--map {item!r}: expected one of {sorted(stems)}=STEM")
        stems[name] = stem

    args.output.mkdir(parents=True, exist_ok=True)
    missing = []
    for source_dir, figures in groups:
        for fig_id, fig_names in figures.items():
            for name in fig_names:
                copied = []
                candidates = [stems[name]] + ([] if stems[name] != name else SOURCE_ALIASES.get(name, []))
                for stem in candidates:
                    for ext in (".pdf", ".png"):
                        source = source_dir / (stem + ext)
                        if source.is_file():
                            shutil.copy2(source, args.output / (name + ext))
                            copied.append(stem + ext)
                    if copied:
                        if stem != stems[name]:
                            print(f"WARNING: {fig_id} {name} not found; used fallback stem {stem}",
                                  file=sys.stderr)
                        break
                if copied:
                    print(f"{fig_id:>3} {name}: {', '.join(copied)}")
                else:
                    missing.append(f"{fig_id} {name} (looked for {stems[name]}.pdf/.png in {source_dir})")
    if missing:
        print(("WARNING" if args.allow_missing else "ERROR") + ": missing figures:", file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        return 0 if args.allow_missing else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

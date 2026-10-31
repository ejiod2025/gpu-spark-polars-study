#!/usr/bin/env python3
"""Copy Paper 2's figures from the analysis outputs into paper2/figures/.

Figures are never drawn here; they come from the analysis (plot_campaign.py output in
each host's figs/, the cross-host figs/, and the Paper 2 analysis figs/). Which figures
are required follows generated/conditions.json, written by tools/stats_to_tex.py, so
run `make stats` first.

  target stem              source                                           required when
  f08_bridge_<host>        <host>/figs/f08_polars_version_bridge            bridge_<host> (else not copied)
  f02x_<host>              <host>/figs/f02x_c1_e1_exploratory               bridge_<host> (else not copied)
  f11_gpu_ladder           xhost/figs/f11_gpu_ladder                        always
  f20_baseline_sensitivity extra/figs/f20_baseline_sensitivity              optional (fallback: f02x)
  f21_speedup_decomposition extra/figs/f21_speedup_decomposition            decomp

Usage:
  python3 paper2/tools/sync_figures.py --host h100=DIR --host a100=DIR --host gh200=DIR \\
      --host b200=DIR --xhost DIR [--extra DIR]
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

PAPER2 = Path(__file__).resolve().parent.parent
HOSTS = ("a100", "h100", "gh200", "b200")
EXTS = ("pdf", "png")


def plan(dirs, xhost, extra, case):
    """[(target, source_dir, source_stem, required)]"""
    items = []
    for host in HOSTS:
        if case.get(f"bridge_{host}"):  # without a bridge the host's F8/F2x are not used
            items.append((f"f08_bridge_{host}", dirs[host] / "figs", "f08_polars_version_bridge", True))
            items.append((f"f02x_{host}", dirs[host] / "figs", "f02x_c1_e1_exploratory", True))
    items.append(("f11_gpu_ladder", xhost / "figs", "f11_gpu_ladder", True))
    if extra:
        items.append(("f20_baseline_sensitivity", extra / "figs", "f20_baseline_sensitivity", False))
        items.append(("f21_speedup_decomposition", extra / "figs", "f21_speedup_decomposition",
                      bool(case.get("decomp"))))
    elif case.get("decomp"):
        raise SystemExit("ERROR: decomp keys are loaded but --extra (their figure directory) is not given")
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", action="append", default=[], metavar="HOST=DIR")
    ap.add_argument("--xhost", type=Path, required=True)
    ap.add_argument("--extra", type=Path)
    ap.add_argument("--out", type=Path, default=PAPER2 / "figures")
    ap.add_argument("--conditions", type=Path, default=PAPER2 / "generated" / "conditions.json")
    args = ap.parse_args(argv)
    dirs = {}
    for item in args.host:
        host, _, path = item.partition("=")
        if host not in HOSTS or not path:
            ap.error(f"--host {item!r}: expected HOST=DIR with HOST in {', '.join(HOSTS)}")
        dirs[host] = Path(path)
    if set(dirs) != set(HOSTS):
        ap.error("give --host for each of " + ", ".join(HOSTS))
    if not args.conditions.exists():
        print(f"ERROR: {args.conditions} missing; run tools/stats_to_tex.py (make stats) first", file=sys.stderr)
        return 1
    case = json.loads(args.conditions.read_text())
    extra = args.extra if args.extra and args.extra.exists() else None
    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*"):
        old.unlink()  # never keep a figure from an earlier input set
    missing, copied = [], []
    for target, src_dir, stem, required in plan(dirs, args.xhost, extra, case):
        found = False
        for ext in EXTS:
            src = src_dir / f"{stem}.{ext}"
            if src.exists():
                shutil.copy2(src, args.out / f"{target}.{ext}")
                found = True
        if found:
            copied.append(target)
        elif required:
            missing.append(f"{target} (from {src_dir / stem}.pdf)")
    manifest = {"copied": copied, "conditions": case}
    (args.out / "SOURCES.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if missing:
        print("ERROR: required figure(s) missing:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    print(f"copied {len(copied)} figure(s) to {args.out}: {', '.join(copied)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

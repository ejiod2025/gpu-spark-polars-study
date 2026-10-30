#!/usr/bin/env python3
"""Convert the campaign stats.json file(s) into paper/generated/stats.tex.

Every number in the paper is a \\stat{key}, \\ratioci{prefix} or \\ratiotext{prefix}
macro (or a host-conditional \\hoststat / \\hostratioci / \\xhostratioci). This script
scans the paper sources for those keys, checks that the stats files provide all of
them, and writes one \\defstat{key}{value} per statistic plus \\statsfinaltrue, which
turns any key still missing at LaTeX time into an error.

Files (amendments A2, A3: one campaign per host, never pooled):
  --stats PATH               primary H100 campaign (required, first)
  --stats a100=PATH          A100 ladder campaign, keys namespaced "a100."
  --stats gh200=PATH         GH200 ladder campaign, keys namespaced "gh200."
  --stats b200=PATH          B200 ladder campaign, keys namespaced "b200."
  --stats xhost_<host>=PATH  cross-host comparison for one host (analyze_campaign.py's
                             xhost_<HOST>.json), keys namespaced "xhost.<host>."
                             (xhost.<host>.<engine>.<suite>.<scale>.*, ratio t_host / t_H100)
The final paper must state every additional host's outcome: pass HOST=PATH, or
--not-run HOST=REASON (repeatable). Host regions of main.tex (% BEGIN-RAN:<host> ...,
% BEGIN-NOTRUN:<host> ...) need keys only in the matching case.

Exit status 1 (and nothing written) when a required key is missing or a host's
outcome is undecided, unless --allow-missing (draft mode: missing keys print as
boxed placeholders in the PDF).

Examples:
  python3 paper/tools/stats_to_tex.py --stats h100-report/stats.json \\
      --stats a100=a100-report/stats.json --stats b200=b200-report/stats.json \\
      --not-run "gh200=no aarch64 build of the pinned plugin jar" \\
      --stats xhost_a100=ladder/xhost_A100.json --stats xhost_b200=ladder/xhost_B200.json
  python3 paper/tools/stats_to_tex.py --list-keys [--case a100=ran,gh200=not-run,b200=ran]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statskeys  # noqa: E402

PAPER = Path(__file__).resolve().parent.parent
TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                "^": r"\textasciicircum{}"}


def tex_escape(text):
    return "".join(TEX_SPECIALS.get(ch, ch) for ch in text)


FLAG = {"a100": "aonehundred", "gh200": "ghtwohundred", "b200": "btwohundred"}  # \\if<flag>ran in macros.tex


def parse_case(text):
    """--case a100=ran,gh200=not-run,... -> status dict for --list-keys (unnamed hosts: both cases)."""
    status = {h: None for h in statskeys.HOSTS}
    for item in filter(None, (text or "").split(",")):
        host, _, state = item.partition("=")
        if host not in status or state not in ("ran", "not-run"):
            raise statskeys.StatsError(f"--case {item!r}: expected HOST=ran|not-run, HOST in "
                                       + ", ".join(statskeys.HOSTS))
        status[host] = state == "ran"
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", action="append", default=[], metavar="[PREFIX=]PATH",
                        help="stats.json; first = primary H100, then a100=, gh200=, b200=, xhost_<host>=PATH")
    parser.add_argument("--not-run", action="append", default=[], metavar="HOST=REASON",
                        help="HOST's campaign was not run; REASON is printed in the paper (repeatable)")
    parser.add_argument("--output", type=Path, default=PAPER / "generated" / "stats.tex")
    parser.add_argument("--tex", type=Path, nargs="+", default=[PAPER / "main.tex"],
                        help="LaTeX sources to scan for required keys")
    parser.add_argument("--allow-missing", action="store_true",
                        help="write a draft-mode file even if keys are missing or hosts undecided")
    parser.add_argument("--allow-manual-overrides", action="store_true",
                        help="accept _display values from non-synthetic stats files; the PDF then "
                             "carries a MANUAL VALUES watermark")
    parser.add_argument("--list-keys", action="store_true", help="print required keys and exit")
    parser.add_argument("--case", metavar="HOST=ran|not-run,...",
                        help="with --list-keys/--template: which host outcomes to list (default: all cases)")
    parser.add_argument("--template", type=Path, help="write a JSON template of required keys and exit")
    args = parser.parse_args(argv)

    try:
        if args.list_keys or args.template:
            required = statskeys.tex_required_keys(args.tex, parse_case(args.case))
            if args.list_keys:
                print("\n".join(required))
            else:
                args.template.write_text(json.dumps({k: None for k in required}, indent=2) + "\n")
                print(f"wrote {len(required)} keys to {args.template}")
            return 0
        if not args.stats:
            parser.error("--stats is required (or use --list-keys / --template)")

        stats, display, formats, sources, provenance = statskeys.load_many(args.stats)
        not_run = statskeys.parse_not_run(args.not_run)
        status = statskeys.host_status({name for name, _ in sources}, not_run)
        display.update(statskeys.status_display(status, not_run))
        for key, value in statskeys.host_summaries(stats, status).items():
            # numbers go through the normal formatting rules; text is printed verbatim
            (stats if statskeys._number(value) else display)[key] = value
        problems = [f"{h} outcome undecided: pass --stats {h}=PATH or --not-run {h}=REASON"
                    for h in statskeys.HOSTS if status[h] is None]
        required = statskeys.tex_required_keys(args.tex, status)
        missing = statskeys.missing_keys(required, stats, display)
        if (missing or problems) and not args.allow_missing:
            for problem in problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            if missing:
                print(f"ERROR: {len(missing)} key(s) used by the paper are missing from the stats files:",
                      file=sys.stderr)
                for key in missing:
                    print(f"  {key}", file=sys.stderr)
            return 1
        draft = bool(missing or problems)
        manual, synthetic = statskeys.manual_values(provenance, args.allow_manual_overrides, draft)
        lines = ["% generated by paper/tools/stats_to_tex.py -- do not edit"]
        for name, path in sources:
            lines.append(f"% {name}: {path} sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}")
        lines.append("\\statsfinalfalse" if draft else "\\statsfinaltrue")
        if manual:
            lines += [f"% manual values (_display) -- {item}" for item in manual] + ["\\manualvaluestrue"]
        if synthetic:
            lines += [f"% synthetic stats file: {', '.join(synthetic)}", "\\syntheticstatstrue"]
        for host in statskeys.HOSTS:
            if status[host] is False:
                lines.append(f"\\{FLAG[host]}ranfalse")
        if statskeys.host_state("any", status) is False:
            lines.append("\\ladderranfalse")
        nulls = []
        for key in sorted(set(stats) | set(display)):
            if key in display:
                text, markup = str(display[key]), False
            else:
                if stats[key] is None:
                    nulls.append(key)
                if isinstance(stats[key], list):
                    continue  # lists are never printable; only an error if a macro asks for one
                text, markup = statskeys.format_value(key, stats[key], formats, "tex")
            lines.append(f"\\defstat{{{key}}}{{{text if markup else tex_escape(text)}}}")
        listed = sorted(k for k in required if isinstance(stats.get(k), list))
        if listed:
            raise statskeys.StatsError("required key(s) hold lists, not printable values: " + ", ".join(listed))
    except statskeys.StatsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n")
    used_nulls = sorted(set(nulls) & set(required))
    if used_nulls:
        print(f"WARNING: {len(used_nulls)} required key(s) are null and print as 'n/a': "
              + ", ".join(used_nulls), file=sys.stderr)
    if manual:
        print("WARNING: hand-filled _display values used (PDF watermarked MANUAL VALUES):\n  "
              + "\n  ".join(manual), file=sys.stderr)
    if synthetic:
        print(f"WARNING: synthetic stats ({', '.join(synthetic)}); PDF watermarked SYNTHETIC", file=sys.stderr)
    if draft:
        print(f"WARNING: draft mode, {len(missing)} key(s) missing (shown as placeholders)"
              + (f"; undecided: {', '.join(h for h in statskeys.HOSTS if status[h] is None)}" if problems else ""),
              file=sys.stderr)
    words = {True: "ran", False: "not run", None: "undecided"}
    outcome = ", ".join(f"{h} {words[status[h]]}" for h in statskeys.HOSTS)
    print(f"wrote {args.output}: {len(stats) + len(display)} statistics, "
          f"{len(required)} required by the paper ({outcome})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

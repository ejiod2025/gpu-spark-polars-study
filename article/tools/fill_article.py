#!/usr/bin/env python3
"""Fill article/ARTICLE.md placeholders from the analysis stats.json file(s).

Placeholders:
  {{key}}        one statistic, formatted exactly as in the paper
  {{ci:prefix}}  "<estimate>× (95% CI <ci_low>–<ci_high>)" from prefix.estimate etc.
Host blocks (amendments A2, A3): <!-- IF:<host> --> ... <!-- ELSE:<host> --> ... <!-- ENDIF:<host> -->
with <host> one of a100, gh200, b200, or "any" (at least one of them ran). A block keeps
its IF part when --stats <host>=PATH is given and its ELSE part with --not-run <host>=REASON.
Marker lines are removed with their line break, so blocks can wrap Markdown table rows.

Exit status 1 (nothing written) if any placeholder key is missing or any host's
outcome is undecided, unless --allow-missing, which renders missing keys as
**[key]** (and undecided hosts as ran) for review drafts. Formatting and host
handling are shared with the paper (paper/tools/statskeys.py).

  python3 article/tools/fill_article.py --stats h100/stats.json --stats a100=a100/stats.json \\
      --stats b200=b200/stats.json --not-run "gh200=reason" \\
      --stats xhost_a100=ladder/xhost_A100.json --stats xhost_b200=ladder/xhost_B200.json
  python3 article/tools/fill_article.py --list-keys [--case a100=ran,gh200=not-run,b200=ran]
"""

import argparse
import sys
from pathlib import Path

ARTICLE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ARTICLE.parent / "paper" / "tools"))
import statskeys  # noqa: E402


def render(text, stats, display, formats):
    missing = []

    def value(key):
        if key in display:
            return str(display[key])
        if key not in stats:
            missing.append(key)
            return f"**[{key}]**"
        return statskeys.format_value(key, stats[key], formats, "md")[0]

    def replace(match):
        is_ci, key = match.group(1), match.group(2)
        if is_ci:
            est, low, high = (value(f"{key}.{part}") for part in statskeys.RATIO_PARTS)
            return f"{est}× (95% CI {low}–{high})"
        return value(key)

    out = statskeys.md_substitute(text, replace)
    return out, sorted(set(missing))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", action="append", default=[], metavar="[PREFIX=]PATH")
    parser.add_argument("--not-run", action="append", default=[], metavar="HOST=REASON",
                        help="HOST's campaign was not run; REASON is printed (repeatable)")
    parser.add_argument("--case", metavar="HOST=ran|not-run,...", help="with --list-keys: which case to list")
    parser.add_argument("--input", type=Path, default=ARTICLE / "ARTICLE.md")
    parser.add_argument("--output", type=Path, default=ARTICLE / "ARTICLE.filled.md")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--allow-manual-overrides", action="store_true",
                        help="accept _display values from non-synthetic stats files; the output then "
                             "starts with a MANUAL VALUES banner")
    parser.add_argument("--list-keys", action="store_true", help="print required keys and exit")
    args = parser.parse_args(argv)

    if args.list_keys:
        status = {h: None for h in statskeys.HOSTS}
        for item in filter(None, (args.case or "").split(",")):
            host, _, state = item.partition("=")
            if host not in status or state not in ("ran", "not-run"):
                parser.error(f"--case {item!r}: expected HOST=ran|not-run")
            status[host] = state == "ran"
        print("\n".join(statskeys.md_required_keys([args.input], status)))
        return 0
    if not args.stats:
        parser.error("--stats is required (or use --list-keys)")
    if args.output.resolve() == args.input.resolve():
        parser.error("--output must differ from --input; the template keeps its placeholders")

    try:
        stats, display, formats, sources, provenance = statskeys.load_many(args.stats)
        not_run = statskeys.parse_not_run(args.not_run)
        status = statskeys.host_status({name for name, _ in sources}, not_run)
        display.update(statskeys.status_display(status, not_run))
        for key, value in statskeys.host_summaries(stats, status).items():
            # numbers go through the normal formatting rules; text is printed verbatim
            (stats if statskeys._number(value) else display)[key] = value
        text = statskeys.md_select(args.input.read_text(), status, undecided="if")
        filled, missing = render(text, stats, display, formats)
    except statskeys.StatsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    problems = [f"{h} outcome undecided: pass --stats {h}=PATH or --not-run {h}=REASON"
                for h in statskeys.HOSTS if status[h] is None]
    try:
        manual, synthetic = statskeys.manual_values(provenance, args.allow_manual_overrides,
                                                    bool(missing or problems) and args.allow_missing)
    except statskeys.StatsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    banner = [label for flag, label in ((manual, "MANUAL VALUES"), (synthetic, "SYNTHETIC — NOT RESULTS")) if flag]
    if banner:
        filled = f"> **{' · '.join(banner)}**\n\n" + filled
    if (missing or problems) and not args.allow_missing:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        if missing:
            print(f"ERROR: {len(missing)} placeholder key(s) missing from the stats files:", file=sys.stderr)
            for key in missing:
                print(f"  {key}", file=sys.stderr)
        return 1
    args.output.write_text(filled)
    note = f" ({len(missing)} missing, rendered as **[key]**)" if missing else ""
    print(f"wrote {args.output}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

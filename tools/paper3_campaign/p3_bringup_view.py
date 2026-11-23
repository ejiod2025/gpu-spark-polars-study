#!/usr/bin/env python3
"""The operator-facing view of a bring-up log (design 7.3: nobody looks at a timing before the campaign ends).

The bring-up stage prints whatever its steps print: the SF1 smoke preflight writes `[PASS    ] run.baseline: run_experiment.py exit 0 in 123s`
(the wall time of a whole suite), the input generators print their own seconds, pip and apt print theirs. The full log is copied off the host and
archived unread. The driver shows the operator only what this module lets through, by an allow-list, never a block-list:

  * the result of every bring-up step: `STEP <name> ok`, `STEP <name> skipped` (a resumed bring-up) or `STEP <name> FAILED rc=N` (the log's timestamp is dropped, so step durations
    cannot be read off the driver's output);
  * every `[FAIL ...]` line of the preflight, with any time or timing value redacted (`<time>`, `<value>`, `<number>`);
  * the last `VERDICT:` line (the preflight's own verdict, `pass` or `fail`).

Every other line is dropped: `[PASS ...]`, `[WARN ...]`, `[INFO ...]`, `[SKIP ...]`, `report:`, gate lines, tool output. Usage: p3_bringup_view.py LOG
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_FAIL_LINES = 40
STEP = re.compile(r"^\S+\s+STEP (?P<name>[A-Za-z0-9_.-]+) (?P<what>ok|skipped|FAILED rc=\d+)\b.*$")
FAIL = re.compile(r"^\[FAIL\s*\]\s")
VERDICT = re.compile(r"^VERDICT: (?P<verdict>pass|fail)\s*$")

NUMBER = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
UNIT = r"(?:ms|us|s|secs?|seconds?|mins?|minutes?|h|hrs?|hours?)"
KEYS = r"\w*(?:seconds|secs|time|duration|elapsed|latency|lag|energy|joules|power|watts)\w*"
REDACTIONS = (
    (re.compile(rf"(?<![\w.]){NUMBER}\s*{UNIT}\b", re.I), "<time>"),                          # 123s, 12.5 s, 3 min
    (re.compile(rf"\b({KEYS})(\W{{0,6}}){NUMBER}", re.I), r"\1\2<value>"),                       # window_seconds 12.3, energy_j: 4.5e3
    (re.compile(r"(?<![\d.])\d+\.\d{3,}(?![\d.])"), "<number>"),                                  # any other long decimal is a measured value
)


def redact(line: str) -> str:
    for pattern, replacement in REDACTIONS:
        line = pattern.sub(replacement, line)
    return line


def view(text: str) -> list:
    """The lines the operator may see, in log order, VERDICT last."""
    out, verdict, hidden = [], None, 0
    for raw in text.splitlines():
        line = raw.rstrip()
        step = STEP.match(line)
        if step:
            out.append(f"STEP {step['name']} {step['what']}")
        elif FAIL.match(line):
            if sum(1 for o in out if o.startswith("[FAIL")) < MAX_FAIL_LINES:
                out.append(redact(line))
            else:
                hidden += 1
        elif VERDICT.match(line):
            verdict = line.strip()
    if hidden:
        out.append(f"... {hidden} more [FAIL] line(s) in the archived log")
    out.append(verdict or "no VERDICT line: the preflight did not reach its end (see the STEP lines)")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        text = Path(argv[0]).read_text(errors="replace")
    except OSError as error:
        print(f"no bring-up log to show ({error.__class__.__name__}); it stays on the host", file=sys.stderr)
        return 1
    print("\n".join(view(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

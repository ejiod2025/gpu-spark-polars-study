#!/usr/bin/env python3
"""The run_experiment.py command line of the smoke test: the one run_campaign.py builds for round 1 of the h100 host class, with
only the workload and the paths swapped.

The readiness test must exercise the flags a campaign passes (scope board, window 10 s, the pulses, the idle windows, the engine
order of round 1, 26 CPUs, Spark and cudf-polars settings), so they are not retyped here: the command comes from
run_campaign.job_command() for the first job of the h100 launch-1 plan. What changes is exactly this:

  --suite baseline            -> --suite smoke, plus --synthetic-smoke (the explicit flag no campaign can pass)
  --data-root/--output/--jar  -> the smoke paths
  --scales                    -> 1 (the synthetic dataset's only scale)
  the interpreter             -> --python

  smoke_command.py --python P --data-root D --output O --jar J [--runs N] [--print | --exec]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys

REPO = Path(__file__).resolve().parents[2]
LOCK_DATE = "20261116"          # any lock date: it only seeds the engine order, which the smoke test takes from round 1


def replace_flag(command: list, flag: str, values: list) -> list:
    """The command with the values of `flag` (everything up to the next --option) replaced."""
    start = command.index(flag)
    end = start + 1
    while end < len(command) and not command[end].startswith("--"):
        end += 1
    return command[:start + 1] + list(values) + command[end:]


def build(python: str, data_root: str, output: str, jar: str, host: str = "h100", runs: int | None = None) -> list:
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import run_campaign as rc

    args = rc.parse_args(["--root", "/smoke", "--data-root", "/smoke-data", "--jar", "/smoke.jar",
                          "--paper3-host", host, "--paper3-launch", "1", "--paper3-lock-date", LOCK_DATE])
    cfg = rc.resolve_config(args)
    job = rc.build_units(cfg)[0].jobs[0]
    if job.suite != "baseline":
        raise SystemExit(f"expected the first job of round 1 to be the baseline suite, got {job.suite}")
    command = rc.job_command(job, Path(output), cfg)
    command[0] = python
    for flag, values in (("--suite", ["smoke"]), ("--scales", ["1"]), ("--data-root", [data_root]), ("--output", [output]), ("--jar", [jar])):
        command = replace_flag(command, flag, values)
    if runs is not None:
        command = replace_flag(command, "--runs", [str(runs)])
    command.append("--synthetic-smoke")
    return command


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--jar", required=True)
    parser.add_argument("--host", default="h100")
    parser.add_argument("--runs", type=int, help="measured trials per query (default: the campaign's 3)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", help="print the command as a shell line (default)")
    mode.add_argument("--json", action="store_true", help="print the command as a JSON list")
    mode.add_argument("--exec", action="store_true", help="replace this process with the command")
    args = parser.parse_args(argv)
    command = build(args.python, args.data_root, args.output, args.jar, args.host, args.runs)
    if args.exec:
        os.execv(command[0], command)
    print(json.dumps(command) if args.json else " ".join(shlex.quote(part) for part in command))
    return 0


if __name__ == "__main__":
    sys.exit(main())

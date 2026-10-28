# Scheduler-only handoff for the separate contention experiment

The owner explicitly chose concurrent sessions on this H100 as a separate
contention experiment. The existing fixed 400-session campaign remains an
isolated comparison. This helper changes no frozen source, campaign, journal,
session record, timing value or formal 24-record execution-profile log.
The prospective interlude addendum must be committed before any signal or job.

`contention_handoff.py` has one operating mode: hold the **exact existing C84
Python campaign scheduler**, let its already launched runner finish unpaused,
run the separately named driver, then resume that same scheduler. It never
signals the original supervisor, runner, worker or their process groups.
There is no original campaign restart, replacement attempt or retry.

## Prepare exact identities and source

Use fresh read-only process evidence for scheduler PID, creation time, parent,
complete command and C84 working directory. The scheduler must have exactly one
first-attempt running session, owned by the original recovery supervisor, and
must still hold both original campaign locks. The helper checks that the child
is an actual `run_experiment.py` process, not a pre-launch gated Python child.
If this boundary is uncertain or changes during the hold, it refuses contention
and returns the same scheduler to its original execution.

Deploy the helper and unchanged `jvm_execution_profile.py` to the separately
named staging directory. The manager and detached guardian must inherit the
original sourced environment. Clear the four banned global/launcher Java option
variables and retain the fixed `SPARK_SUBMIT_OPTS`; no environment file is edited.

The driver specification is an explicit JSON file, never a shell command:

```json
{
  "schema_version": "h100-contention-driver-spec-v1",
  "command": ["/absolute/python", "/absolute/contention_runner.py", "..."],
  "cwd": "/home/ubuntu/study-h100-research-c84ef6a",
  "files_sha256": {
    "/absolute/contention_runner.py": "actual SHA256",
    "/absolute/contention_worker.py": "actual SHA256"
  }
}
```

Replace every placeholder, include all actual driver/wrapper sources, and bind
the actual protocol/configuration in that driver's own evidence. The helper
checks the driver script and every listed source before holding, and again
before releasing its launch gate. No credentials belong in this file or argv.
Keep contention output outside the original source and recovery evidence roots.

## Detached manager launch

The example requires current observed identity values; the original scheduler
PID changes at each block. Do not reuse a stale PID or infer ownership from its
number. The supervisor is currently PID 92406, but its command/parent relation
must still match the live scheduler.

```sh
/home/ubuntu/study/.venv/bin/python -B \
  /home/ubuntu/study-h100-contention-staging-20261028/contention_handoff.py \
  --scheduler-pid ACTUAL_PID --scheduler-created ACTUAL_CREATION_TIME \
  --supervisor-pid 92406 \
  --campaign /home/ubuntu/study-h100-research-evidence-20261027-gclocker100/campaign.json \
  --source /home/ubuntu/study-h100-research-c84ef6a \
  --profile /home/ubuntu/study-h100-research-evidence-20261027-gclocker100/EXECUTION_PROFILE.json \
  --driver-spec /absolute/ACTUAL_DRIVER_SPEC.json \
  --output /home/ubuntu/study-h100-contention-handoff-20261028 \
  --drain-timeout 2700 --driver-timeout 14400 --total-timeout 14400
```

Root launches this through its usual `setsid`/`nohup` detached wrapper under the
same environment. The helper starts its own independent guardian before it
sends positive-PID `SIGSTOP`. Neither command uses `killpg` for the scheduler.
Drain deadline is at most 45 minutes. A pre-contention timeout or failure resumes
the scheduler without hashing while its original worker is still active.
The total four-hour deadline includes drain and triggers owned contention
cleanup; cleanup and safe verification may extend beyond the deadline. The
driver must enforce its own predeclared per-wave limits and preserve failures.

## Actual handoff and evidence

After the original runner exits, Linux's retained zombie exit status must be
zero, its schedule and worker results complete, and the host's original engines
idle. The helper saves exact schedule/results hashes and the entire finished
attempt's file inventory. Only then does it run a separate full original-byte,
source, JAR, Python and JVM-profile verification and release the contention
driver. The original journal remains byte-identical while the scheduler is held.

The driver is PID/creation-time/group bound before its gated launch. The manager
and guardian retain observed descendant identities. Cleanup signals only those
owned contention PIDs. It never kills an unproven process to manufacture idle.
Before resume, all recorded contention groups must have no live members, the
independent `contention_*.py` process scan must be clear, and the original frozen
idle probe must find no benchmark workers or GPU clients. An orphan CPU wrapper
therefore blocks resume even when it escaped an ownership polling interval.

After contention, the helper checks the original source/JAR/runtime/profile and
all 1,654 physical input hashes again, and checks the original finished attempt
inventory and held journal unchanged. These are separate interlude receipts;
they never append to `EXECUTION_PROFILE_GATES.jsonl`. Only then may the same
stopped scheduler receive positive-PID `SIGCONT`.

`CONFIG.json`, `STATE.json`, `EVENTS.jsonl`, driver/guardian logs, actual before/
after profile checks, original attempt/exit evidence and `RESUMED.json` retain
the operational provenance. `COMPLETE_RESUMED` establishes only the handoff,
not scientific success, completed 400 sessions or a publication gate. A nonzero
driver result remains a failure even when the original scheduler safely resumes.

If the manager dies, its detached guardian retains and cleans proven contention
ownership, then repeats the safe-resume checks. Unknown remaining workload or
changed original evidence produces `GUARDIAN_BLOCKED.json`; the original
scheduler stays held for diagnosis. A failed-closed hold must never be repaired
by blindly sending SIGCONT. No helper can promise bounded continuation while
an unowned process still occupies the machine.

The paused scheduler sets the original attempt's `ended_at_utc` and
`orchestrator_wall_seconds` only after it resumes. That outer envelope can include
the contention interlude. The already finished worker's action timestamps,
action/common-result seconds, runner wall duration, cache observations and
telemetry remain unchanged. Report this scheduling deviation in the paper;
never subtract pause time by editing original timestamps or call the interlude
part of an isolated query measurement.

## Verification and references

Focused tests are fabricated local boundaries with mocked signals and process
observations; they never touch the VM or perform a real handoff:

```sh
/tmp/h100-analysis-audit-venv/bin/python -B -m pytest -q \
  operations/tests/test_contention_handoff.py
```

The [Linux man-pages proc_pid_stat reference](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)
defines stopped/zombie state and field 52's wait status. Access must be available
for the owned same-user runner. Actual kernel/PID evidence is required at run
time; successful mechanism tests are not VM execution evidence.

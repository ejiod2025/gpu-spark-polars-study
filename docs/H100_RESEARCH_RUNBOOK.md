# Prospective H100 campaign

The [protocol](H100_CONFIRMATORY_PROTOCOL.md) defines the sample, endpoints,
coverage policy and analysis. The October 26 pilot remains separate. These
commands require the provisioned H100 host and its existing validated data.

Use an isolated checkout of the committed `H100_main_dev` revision. Freeze code
before planning; do not edit the timed checkout. Full pre/post Parquet hashing
and all oracle work occur outside measurements.

## Prepare and validate

```bash
source /home/ubuntu/study/rerun/h100-env.sh
bash setup_research_control.sh
export RESEARCH_CPU200=/home/ubuntu/study/.venv-research-cpu200/bin/python
```

The script checks the exact common CPython executable/build and package pins.
It validates existing environments without repairing or upgrading them in place.
Preserve its log. Run Linux tests in a separate test environment, then run all
five configurations against independent SF1 oracles:

```bash
"$PYTHON_GPU_ENV" research_readiness.py \
  --data-root "$DATA_ROOT" --output /home/ubuntu/research-evidence/readiness \
  --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" \
  --polars-cpu-200-python "$RESEARCH_CPU200"
```

`READINESS.json` must say `PASS`. This smoke is not part of the ten blocks.
Preserve failures and use a newly named directory after a fix. Run the independent
full-scale oracles before the campaign, with a dedicated spill directory:

```bash
mkdir -p /home/ubuntu/research-evidence/oracles
mkdir -p /home/ubuntu/research-evidence/oracle-spill
for scale in 1 10 30 100 300; do
  "$PYTHON_GPU_ENV" research_oracle.py --suite baseline \
    --data "$DATA_ROOT/sf$scale" \
    --output "/home/ubuntu/research-evidence/oracles/baseline-sf$scale.json" \
    --threads 26 --memory-limit 128GB \
    --temp-directory /home/ubuntu/research-evidence/oracle-spill
done
for scale in 30 100 300; do
  "$PYTHON_GPU_ENV" research_oracle.py --suite complex \
    --data "$DATA_ROOT/sf$scale" \
    --output "/home/ubuntu/research-evidence/oracles/complex-sf$scale.json" \
    --threads 26 --memory-limit 128GB \
    --temp-directory /home/ubuntu/research-evidence/oracle-spill
done
```

Keep full/partial outputs and logs. Oracle execution time is diagnostic, outside
competitor scores. Run the separate adversarial semantic fixture checks on
the engine environments, retaining unsupported cases and plans.

## Freeze and execute

After readiness, oracles and semantic checks pass, freeze the entire schedule:

```bash
"$PYTHON_GPU_ENV" research_campaign.py \
  --plan /home/ubuntu/research-evidence/campaign.json \
  --data-root "$DATA_ROOT" \
  --output-root /home/ubuntu/research-evidence/campaign-results \
  --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" \
  --polars-cpu-200-python "$RESEARCH_CPU200" --seed 20261026 \
  --oracle-root /home/ubuntu/research-evidence/oracles \
  --readiness /home/ubuntu/research-evidence/readiness/READINESS.json
```

Archive `campaign.json`, its `.sha256` and source snapshot before the first
session; copy them to the private repository. Defaults specify ten blocks,
three measured actions, 26 vCPUs and the fixed Spark configuration. The manifest
binds source revision, protocol, readiness and oracles. Run through a persistent
remote supervisor or shell with a PID/log/exit-status file:

```bash
"$PYTHON_GPU_ENV" research_campaign.py \
  --run /home/ubuntu/research-evidence/campaign.json
```

The executor journals each launch, PID/create time, command, attempt, finish,
artifact hash and failure. It holds a host lock and cleans owned process groups
on interruption. Before accepting each session it checks independent oracle
agreement and coverage. Process completion alone does not establish correctness.
`--max-sessions N` pauses after N new sessions without changing the schedule.
A normal resume uses the same command and never repeats a success.

Recovery/retry flags explicitly preserve extra attempts as protocol deviations.
The default final validator rejects replacement attempts pending a separately
documented deviation analysis. Do not erase a journal to get a clean-looking run.

## Validate and analyze

After all 400 sessions and final integrity gates finish:

```bash
"$PYTHON_GPU_ENV" research_validate.py \
  --campaign /home/ubuntu/research-evidence/campaign.json \
  --oracles /home/ubuntu/research-evidence/oracles \
  --output /home/ubuntu/research-evidence/validation.json
```

For monitoring, `--partial` audits completed sessions and labels the report
`PARTIAL`; it cannot supply the final analysis gate. Keep each audit under a
new timestamped name. Install `requirements-research-analysis.txt` in a separate
analysis environment and use `research_analysis.py --help` for the committed
replay interface. It requires a complete campaign and `PASS` validation tied
to the exact result hashes. No GPU or Java is required for saved-data analysis.

For local replay, copy `campaign-results/`, immutable plan/sidecar, oracles and
validation without changing their bytes. Validator and analyzer accept
`--artifacts-root /absolute/local/campaign-results` to remap access paths while
preserving identity metadata. Do not rewrite paths inside hashed JSON. Archive
large raw telemetry/log/event data separately if needed, retaining cryptographic
manifests and retrieval instructions.

Before calling the study publication ready, reproduce its tables/figures from
the archive, audit every claim against results/coverage, and complete the
manuscript and notices. Development remains private.

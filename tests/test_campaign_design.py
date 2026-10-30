"""Independent verification of the section 5 order design (and section 3/4/10 shape) of the H100 campaign.

The expectations below are written from docs/H100_EXPERIMENT_DESIGN.md, not imported from run_campaign.py or
preflight.py, and are checked against: the design document's own table, `run_campaign.py --dry-run --verbose`
(parsed as text), the commands run_campaign.py would launch, and campaign.json (a freshly created one, or the real
one on the host: `CAMPAIGN_JSON=/path/to/campaign.json pytest tests/test_campaign_design.py`).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DESIGN_DOC = REPO / "docs" / "H100_EXPERIMENT_DESIGN.md"
RUN_CAMPAIGN = REPO / "run_campaign.py"

SEED = 20261029
ENGINES = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]
LETTERS = {"A": "spark_cpu", "B": "polars_gpu", "C": "polars", "D": "spark_gpu"}
ROWS = ["ABDC", "BCAD", "CDBA", "DACB"]
ROW_ENGINES = {
    1: ["spark_cpu", "polars_gpu", "spark_gpu", "polars"],
    2: ["polars_gpu", "polars", "spark_cpu", "spark_gpu"],
    3: ["polars", "spark_gpu", "polars_gpu", "spark_cpu"],
    4: ["spark_gpu", "spark_cpu", "polars", "polars_gpu"],
}
BASELINE_SCALES = ["1", "10", "30", "100", "300"]
COMPLEX_SCALES = ["30", "100", "300"]
SUITE_SCALES = {"baseline": BASELINE_SCALES, "complex": COMPLEX_SCALES}
SUITE_FIRST = {1: "baseline", 2: "complex", 3: "baseline", 4: "complex"}
SIDE_ROWS = ["AB", "BA", "BA", "AB"]
SIDE_ARMS = {"A": "polars-1.44.2", "B": "polars-2.0.0"}


# ---- the properties, as functions so that they can be shown to detect violations ---------------------------------

def engines_once_per_position(orders: list[list[str]]) -> bool:
    n = len(orders[0])
    return len(orders) == n and all(sorted(order[p] for order in orders) == sorted(orders[0]) for p in range(n)) \
        and all(sorted(order) == sorted(orders[0]) for order in orders)


def adjacent_pairs_exactly_once(orders: list[list[str]]) -> bool:
    pairs = [(a, b) for order in orders for a, b in zip(order, order[1:])]
    return sorted(pairs) == sorted(itertools.permutations(orders[0], 2))


def williams_row(n: int, shift: int) -> list[int]:
    """Williams (1949) design for even n: first row 0, 1, n-1, 2, n-2, ...; other rows are cyclic shifts."""
    row, low, high = [0], 1, n - 1
    while len(row) < n:
        row.append(low)
        low += 1
        if len(row) < n:
            row.append(high)
            high -= 1
    return [(x + shift) % n for x in row]


# ---- the design document ---------------------------------------------------------------------------------------------

def test_seeded_shuffle_gives_the_registered_letters():
    engines = list(ENGINES)
    random.Random(SEED).shuffle(engines)
    assert dict(zip("ABCD", engines)) == LETTERS


def test_design_document_states_the_same_letters_and_table():
    text = DESIGN_DOC.read_text()
    assert f"Seed **{SEED}**" in text
    assert "**A = spark_cpu, B = polars_gpu, C = polars, D = spark_gpu**" in text
    table = re.findall(r"^\| (\d) \| ([A-D] [A-D] [A-D] [A-D]) \| ([a-z_]+(?: → [a-z_]+){3}) \|$", text, re.M)
    assert [(int(r), seq.replace(" ", ""), order.split(" → ")) for r, seq, order in table] == \
        [(r, ROWS[r - 1], ROW_ENGINES[r]) for r in (1, 2, 3, 4)]
    assert "rounds 1 and 3 run baseline first; rounds 2 and 4\nrun complex first" in text
    assert "Scales run in ascending order within a suite" in text
    assert "orders AB, BA, BA, AB" in text


def test_rows_are_the_letters_mapped_to_engines():
    for number, row in enumerate(ROWS, 1):
        assert [LETTERS[c] for c in row] == ROW_ENGINES[number]


def test_table_is_a_williams_square_for_n_4():
    orders = [ROW_ENGINES[r] for r in (1, 2, 3, 4)]
    assert engines_once_per_position(orders)
    assert adjacent_pairs_exactly_once(orders)
    letters = sorted(LETTERS)
    # the registered rows are exactly the cyclic shifts of the Williams first row 0,1,3,2 (A,B,D,C)
    assert ROWS == ["".join(letters[i] for i in williams_row(4, shift)) for shift in range(4)]


def test_checkers_detect_a_broken_design():
    orders = [list(ROW_ENGINES[r]) for r in (1, 2, 3, 4)]
    swapped = [list(o) for o in orders]
    swapped[2][0], swapped[2][1] = swapped[2][1], swapped[2][0]
    assert not engines_once_per_position(swapped) or not adjacent_pairs_exactly_once(swapped)
    repeated = [list(o) for o in orders]
    repeated[3] = list(orders[0])
    assert not engines_once_per_position(repeated)
    latin_not_williams = [["A", "B", "C", "D"], ["B", "C", "D", "A"], ["C", "D", "A", "B"], ["D", "A", "B", "C"]]
    assert engines_once_per_position(latin_not_williams) and not adjacent_pairs_exactly_once(latin_not_williams)


def test_every_engine_follows_every_other_engine_once_and_leads_once():
    orders = [ROW_ENGINES[r] for r in (1, 2, 3, 4)]
    for engine in ENGINES:
        predecessors = sorted(o[i - 1] if i else "<first>" for o in orders for i, e in enumerate(o) if e == engine)
        assert predecessors == sorted(["<first>", *[e for e in ENGINES if e != engine]])


def test_side_study_arms_are_balanced_ab_ba_ba_ab():
    first = [row[0] for row in SIDE_ROWS]
    assert sorted(first) == ["A", "A", "B", "B"] and SIDE_ROWS == ["AB", "BA", "BA", "AB"]


# ---- run_campaign.py --dry-run ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dry_run() -> str:
    if not RUN_CAMPAIGN.is_file():
        pytest.fail(f"{RUN_CAMPAIGN} does not exist yet; the design cannot be verified against it")
    result = subprocess.run([sys.executable, str(RUN_CAMPAIGN), "--dry-run", "--verbose"], cwd=REPO, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout


def parse_dry_run(text: str) -> dict:
    """{'main': {round: {'sequence', 'sessions', 'jobs': [{'suite', 'arm', 'scales', 'order', 'per_scale', 'sessions'}]}}, 'side': ...}"""
    out: dict[str, dict] = {"main": {}, "side": {}}
    section, current, job = None, None, None
    for line in text.splitlines():
        if line.startswith("MAIN ROUNDS"):
            section = "main"
        elif line.startswith("SIDE STUDY"):
            section = "side"
        elif line.startswith("TOTAL:"):
            out["total"] = tuple(int(x) for x in re.findall(r"\d+", line))
            section = None
        elif section and (m := re.match(r"^  round (\d)  ([A-D]{2,4})  (\S+)  \((\d+) sessions\)$", line)):
            current = out[section].setdefault(int(m.group(1)), {"sequence": m.group(2), "path": m.group(3),
                                                                "sessions": int(m.group(4)), "jobs": []})
        elif section and current and (m := re.match(r"^    \d+\. (?:\[(\S+)\] )?(baseline|complex): SF ([\d, ]+) x engines (.+?)  = (\d+) sessions$", line)):
            job = {"arm": m.group(1), "suite": m.group(2), "scales": [s.strip() for s in m.group(3).split(",")],
                   "order": m.group(4).split(" -> "), "sessions": int(m.group(5)), "per_scale": {}}
            current["jobs"].append(job)
        elif section and job and (m := re.match(r"^         SF(\d+): (.+)$", line)):
            job["per_scale"][m.group(1)] = m.group(2).split(" -> ")
    return out


def test_dry_run_header_states_the_registered_design(dry_run):
    assert f"seed {SEED}" in dry_run
    assert str(LETTERS) in dry_run.splitlines()[0]
    assert str(ROWS) in dry_run.splitlines()[0]
    assert "formal: True" in dry_run


def test_dry_run_main_rounds_follow_the_latin_square(dry_run):
    plan = parse_dry_run(dry_run)["main"]
    assert sorted(plan) == [1, 2, 3, 4]
    for number in (1, 2, 3, 4):
        round_plan = plan[number]
        assert round_plan["sequence"] == ROWS[number - 1]
        assert round_plan["sessions"] == 32 and len(round_plan["jobs"]) == 2
        for job in round_plan["jobs"]:
            assert job["arm"] is None
            assert job["order"] == ROW_ENGINES[number]
            assert job["scales"] == SUITE_SCALES[job["suite"]]
            assert job["sessions"] == len(job["scales"]) * 4
            assert sorted(job["per_scale"]) == sorted(job["scales"], key=int) or sorted(job["per_scale"], key=int) == job["scales"]
            for scale in job["scales"]:
                assert job["per_scale"][scale] == ROW_ENGINES[number], (number, job["suite"], scale)


def test_dry_run_suites_alternate_and_scales_ascend(dry_run):
    plan = parse_dry_run(dry_run)["main"]
    for number in (1, 2, 3, 4):
        suites = [job["suite"] for job in plan[number]["jobs"]]
        assert suites == [SUITE_FIRST[number], "complex" if SUITE_FIRST[number] == "baseline" else "baseline"], number
        for job in plan[number]["jobs"]:
            assert [int(s) for s in job["scales"]] == sorted(int(s) for s in job["scales"])


def test_dry_run_enumerates_every_session_exactly_once_and_balanced(dry_run):
    plan = parse_dry_run(dry_run)["main"]
    sessions = [(number, job["suite"], scale, engine) for number in plan for job in plan[number]["jobs"]
                for scale in job["scales"] for engine in job["per_scale"][scale]]
    expected = {(r, s, sc, e) for r in (1, 2, 3, 4) for s, scales in SUITE_SCALES.items() for sc in scales for e in ENGINES}
    assert len(sessions) == len(set(sessions)) == len(expected) == 128 and set(sessions) == expected
    for suite, scales in SUITE_SCALES.items():
        for scale in scales:  # in every (suite, scale) the four rounds form a Williams square
            orders = [[e for (r, s, sc, e) in sessions if (r, s, sc) == (rnd, suite, scale)] for rnd in (1, 2, 3, 4)]
            assert engines_once_per_position(orders) and adjacent_pairs_exactly_once(orders), (suite, scale)


def test_dry_run_side_study_matches_section_10(dry_run):
    parsed = parse_dry_run(dry_run)
    side = parsed["side"]
    assert sorted(side) == [1, 2, 3, 4] and [side[r]["sequence"] for r in (1, 2, 3, 4)] == SIDE_ROWS
    for number in (1, 2, 3, 4):
        jobs = side[number]["jobs"]
        assert side[number]["sessions"] == 16 and len(jobs) == 4
        assert {j["order"][0] for j in jobs} == {"polars"} and all(j["order"] == ["polars"] for j in jobs)
        for suite_jobs in (jobs[:2], jobs[2:]):  # the two arms run back to back within a suite, in the round's order
            assert len({j["suite"] for j in suite_jobs}) == 1
            assert [j["arm"] for j in suite_jobs] == [SIDE_ARMS[c] for c in SIDE_ROWS[number - 1]]
        assert [j["suite"] for j in jobs[::2]] == [SUITE_FIRST[number], "complex" if SUITE_FIRST[number] == "baseline" else "baseline"]
        assert all(j["scales"] == SUITE_SCALES[j["suite"]] for j in jobs)
    main_sessions, side_sessions, total = parsed["total"]
    assert (main_sessions, side_sessions, total) == (128, 64, 192)


def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "campaign"
    result = subprocess.run([sys.executable, str(RUN_CAMPAIGN), "--dry-run", "--root", str(root)], cwd=tmp_path, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert result.returncode == 0 and not root.exists() and list(tmp_path.iterdir()) == []


# ---- the commands run_campaign.py would launch --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def campaign_api():
    try:
        import run_campaign
    except Exception as error:  # pragma: no cover
        pytest.fail(f"run_campaign.py cannot be imported: {error}")
    return run_campaign


def formal_config(api, tmp_path_factory):
    root = tmp_path_factory.mktemp("campaign")
    data = root / "data"
    for scale in ("1", "10", "30", "100", "300"):
        (data / f"sf{scale}").mkdir(parents=True)
        (data / f"sf{scale}" / "manifest.json").write_text(f'{{"scale": {scale}}}')
    jar = root / "rapids.jar"
    jar.write_bytes(b"jar")
    args = api.parse_args(["--root", str(root / "root"), "--data-root", str(data), "--jar", str(jar)])
    return args, api.resolve_config(args)


@pytest.fixture(scope="module")
def formal(campaign_api, tmp_path_factory):
    args, cfg = formal_config(campaign_api, tmp_path_factory)
    units = campaign_api.build_units(cfg)
    return args, cfg, units, campaign_api.design_dict(cfg, units)


def option(command: list[str], flag: str) -> list[str]:
    start = command.index(flag) + 1
    end = next((i for i in range(start, len(command)) if command[i].startswith("--")), len(command))
    return command[start:end]


def test_main_job_commands_pin_the_protocol(campaign_api, formal):
    _, cfg, units, _ = formal
    for unit in (u for u in units if u.kind == "main"):
        for job in unit.jobs:
            command = campaign_api.job_command(job, Path("/out") / job.out_rel, cfg)
            assert option(command, "--suite") == [job.suite]
            assert option(command, "--scales") == SUITE_SCALES[job.suite]
            assert option(command, "--engine-order") == ROW_ENGINES[unit.round]
            assert sorted(option(command, "--engines")) == sorted(ENGINES)
            assert option(command, "--runs") == ["3"] and option(command, "--cpus") == ["26"]
            assert option(command, "--driver-memory") == ["96g"] and "--keep-going" in command
            assert option(command, "--spark-gpu-profile") == ["a100-parity"]
            assert option(command, "--polars-gpu-executor") == ["streaming"]
            if job.suite == "complex":
                assert option(command, "--query-cleanup") == ["verified_gc"]
                assert option(command, "--cleanup-timeout-seconds") == ["60"]
            else:
                assert option(command, "--query-cleanup") == ["none"]
            assert "--engine-timeout-seconds" not in command  # the design has no timeout: a hung session must not be silently cut short


def test_side_study_commands_use_the_right_interpreters(campaign_api, formal):
    _, cfg, units, _ = formal
    for unit in (u for u in units if u.kind == "side"):
        for job in unit.jobs:
            command = campaign_api.job_command(job, Path("/out") / job.out_rel, cfg)
            assert option(command, "--engines") == ["polars"] and option(command, "--engine-order") == ["polars"]
            assert (command[0] == cfg.main_python) == (job.arm == "polars-1.44.2")


def test_the_gate_command_is_accepted_by_preflight(campaign_api, formal, tmp_path):
    import preflight
    _, cfg, units, _ = formal
    unit = next(u for u in units if u.kind == "main" and u.round == 2)
    command = campaign_api.gate_command(unit, tmp_path / "round-2", cfg, tmp_path / "round-2" / "preflight")
    args = preflight.build_parser().parse_args(command[2:])
    preflight.resolve_args(preflight.build_parser(), args)
    assert args.formal and args.round == 2 and args.skip_run and args.error_policy == "classified"
    assert args.suite_scales == SUITE_SCALES and args.runs == 3 and args.expect_cpus == 26
    assert Path(args.baseline_dir) == tmp_path / "round-2" / "baseline" and Path(args.complex_dir) == tmp_path / "round-2" / "complex"


def test_preflight_carries_an_identical_copy_of_the_design():
    import preflight
    assert preflight.CAMPAIGN_SEED == SEED and list(preflight.ROUND_ROWS) == ROWS
    assert preflight.campaign_letters() == LETTERS
    for number in (1, 2, 3, 4):
        assert preflight.round_engine_order(number) == ROW_ENGINES[number]
    assert {k: list(v) for k, v in preflight.FORMAL["scales"].items()} == SUITE_SCALES
    assert preflight.FORMAL["runs"] == 3 and preflight.FORMAL["cpus"] == 26
    assert sorted(preflight.FORMAL["expect_gpu_unsupported"]) == ["cx_q13", "cx_window"]
    assert list(preflight.FORMAL["engines"]) == ENGINES


# ---- campaign.json ----------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def campaign_json(campaign_api, formal, tmp_path_factory):
    """The real campaign.json when CAMPAIGN_JSON is set, else one created by run_campaign.py's own code path."""
    real = os.environ.get("CAMPAIGN_JSON")
    if real:
        return json.loads(Path(real).read_text()), True
    args, cfg, units, design = formal
    cfg.root.mkdir(parents=True, exist_ok=True)
    prov = campaign_api.provenance(cfg, units)
    campaign_api.start_campaign_file(cfg, design, prov, args)
    return json.loads((cfg.root / "campaign.json").read_text()), False


def test_campaign_json_records_the_registered_design(campaign_json):
    data, _ = campaign_json
    design = data["design"]
    assert design["seed"] == SEED and design["letters"] == LETTERS and design["sequences"] == ROWS
    assert design["formal"] is True and design["test_flags"] == []
    assert design["engines"] == ENGINES
    assert [r["engine_order"] for r in design["main_rounds"]] == [ROW_ENGINES[r] for r in (1, 2, 3, 4)]
    assert [r["sequence"] for r in design["main_rounds"]] == ROWS
    assert [r["suite_order"][0] for r in design["main_rounds"]] == [SUITE_FIRST[r] for r in (1, 2, 3, 4)]
    assert design["suites"]["baseline"]["scales"] == BASELINE_SCALES and design["suites"]["complex"]["scales"] == COMPLEX_SCALES
    assert design["suites"]["baseline"]["query_cleanup"] == "none" and design["suites"]["complex"]["query_cleanup"] == "verified_gc"
    flags = design["flags"]
    assert (flags["cpus"], flags["driver_memory"], flags["runs"], flags["keep_going"]) == (26, "96g", 3, True)
    assert flags["spark_gpu_profile"] == "a100-parity" and flags["polars_gpu_executor"] == "streaming"
    assert sorted(design["expect_gpu_unsupported"]) == ["cx_q13", "cx_window"]
    assert design["totals"] == {"main_sessions": 128, "side_sessions": 64, "sessions": 192}
    assert design["side_study"]["sequences"] == SIDE_ROWS
    assert [r["arm_order"] for r in design["side_study"]["rounds"]] == [[SIDE_ARMS[c] for c in row] for row in SIDE_ROWS]
    assert data["design_sha256"] == hashlib.sha256(json.dumps(design, sort_keys=True).encode()).hexdigest()


def test_campaign_json_pins_the_documents_and_inputs(campaign_json):
    data, real = campaign_json
    prov = data["provenance"]
    assert prov["design_doc_sha256"] == hashlib.sha256(DESIGN_DOC.read_bytes()).hexdigest()
    assert prov["source_sha256"]["docs/H100_EXPERIMENT_DESIGN.md"] == prov["design_doc_sha256"]
    for name in ("run_campaign.py", "preflight.py", "run_experiment.py", "worker.py"):
        assert re.fullmatch(r"[0-9a-f]{64}", prov["source_sha256"][name]), name
    assert prov["git"]["commit"] is None or re.fullmatch(r"[0-9a-f]{40}", prov["git"]["commit"])
    if real:  # the host's campaign: inputs and environments must be recorded, not null
        assert all(prov["inputs"]["manifest_sha256"].get(f"sf{s}") for s in BASELINE_SCALES)
        assert prov["inputs"]["rapids_jar_sha256"]
        assert prov["environments"]["main"]["polars"] == "1.44.2"
        assert prov["environments"]["side_a100compat"]["polars"] == "2.0.0"
        assert prov["host"]["gpu"]


def test_a_second_invocation_cannot_change_the_registered_design(campaign_api, formal, campaign_json):
    args, cfg, units, design = formal
    changed = json.loads(json.dumps(design))
    changed["main_rounds"][0]["engine_order"] = list(reversed(changed["main_rounds"][0]["engine_order"]))
    prov = campaign_api.provenance(cfg, units)
    with pytest.raises(campaign_api.CampaignError):
        campaign_api.start_campaign_file(cfg, changed, prov, args)

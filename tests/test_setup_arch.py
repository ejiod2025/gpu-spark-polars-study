"""setup_h100.sh architecture handling (x86_64 H100 host vs aarch64 GH200 host, amendment A3).

No GPU, network or root needed: the script runs with --dry-run in a scratch copy of the repo, with
`uname` and `nvidia-smi` shims first on PATH, and the printed plan is asserted. Pin-file tests check that
the aarch64 pins cannot drift from the x86_64 ones. The last section covers preflight.py's env.jar check, which must
expect the arm64 jar on aarch64 and stay unchanged on x86_64.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT_FILES = ("setup_h100.sh", "requirements-gpu.txt", "requirements-gpu-aarch64.txt",
                "evidence/requirements.lock.txt", "evidence/DEPENDENCY_SHA256.txt")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

X86_JAR = "rapids-4-spark_2.12-26.08.1.jar"
X86_JAR_SHA = "773a2fe5a6eeaf61a8ce3ec66b55e0ac9424ed6152d356a2524498c15373dd68"
ARM_JAR = "rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar"
ARM_JAR_SHA = "44ac76f748b2d1091dcb057eb8effa624967622cdf648ff8d0940f7f19dd4735"  # Maven Central, downloaded and hashed 2026-10-29
MAVEN = "https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/"
H100 = "NVIDIA H100 80GB HBM3, 580.105.08, 81559 MiB"
GH200 = "NVIDIA GH200 480GB, 580.105.08, 97871 MiB"


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = PIN.match(line.strip())
        if match:
            assert norm(match.group(1)) not in found, f"{path.name}: duplicate pin {match.group(1)}"
            found[norm(match.group(1))] = match.group(2)
    return found


def hashes(path: Path) -> dict[str, list[str]]:
    """name -> sha256 list from `name==ver \\` + `    --hash=sha256:...` continuation lines."""
    found: dict[str, list[str]] = {}
    current = None
    for line in path.read_text().splitlines():
        match = PIN.match(line.strip())
        if match:
            current = norm(match.group(1))
            found[current] = []
        elif line.strip().startswith("--hash=sha256:"):
            assert current is not None
            found[current].append(line.strip().removeprefix("--hash=sha256:").rstrip(" \\"))
        elif line.strip() and not line.strip().startswith("#"):
            raise AssertionError(f"unexpected line in {path.name}: {line!r}")
    return found


# ---- pin files ---------------------------------------------------------------------------------

def test_aarch64_pins_equal_x86_pins():
    assert pins(REPO / "requirements-gpu-aarch64.txt") == pins(REPO / "requirements-gpu.txt")


def test_aarch64_pins_all_carry_sha256():
    found = hashes(REPO / "requirements-gpu-aarch64.txt")
    assert found and set(found) == set(pins(REPO / "requirements-gpu.txt"))
    for name, digests in found.items():
        assert digests, f"{name} has no hash"
        assert all(SHA256.match(d) for d in digests), f"{name}: malformed sha256 {digests}"
        assert len(set(digests)) == len(digests)


def test_aarch64_file_keeps_the_polars_override_the_installer_looks_for():
    lines = (REPO / "requirements-gpu-aarch64.txt").read_text().splitlines()
    assert any(line.startswith("polars==") for line in lines)  # setup_h100.sh: grep -qE '^polars=='
    assert any(line.startswith("polars-runtime-32==") for line in lines)


def test_gpu_pins_do_not_overlap_lock_pins_in_aarch64_file():
    lock = pins(REPO / "evidence" / "requirements.lock.txt")
    overlap = set(hashes(REPO / "requirements-gpu-aarch64.txt")) & (set(lock) - {"polars", "polars-runtime-32"})
    assert not overlap, f"hashed here and unhashed in the lock: {overlap}"


def test_arm_jar_sha_is_not_the_x86_jar_sha():
    assert ARM_JAR_SHA != X86_JAR_SHA
    assert f"{X86_JAR_SHA}  jars/{X86_JAR}" in (REPO / "evidence" / "DEPENDENCY_SHA256.txt").read_text()


# ---- documentation record ------------------------------------------------------------------------

def gh200_section() -> str:
    text = (REPO / "docs" / "POLARS_GPU_COMPAT.md").read_text()
    match = re.search(r"^## [0-9]+\. GH200 / aarch64[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match, "docs/POLARS_GPU_COMPAT.md has no numbered 'GH200 / aarch64' section"
    return match.group(1)


def test_doc_records_jar_sha_and_url():
    section = gh200_section()
    assert ARM_JAR_SHA in section and MAVEN + ARM_JAR in section


def test_doc_tables_agree_with_pin_files_and_have_valid_sha256():
    lock = pins(REPO / "evidence" / "requirements.lock.txt")
    section = gh200_section()
    # rows: | `name==version` | Python | `artifact` | `sha256` |
    rows = re.findall(r"^\| `([A-Za-z0-9_.-]+)==([^`]+)` \| (3\.1[01]) \|.*`([0-9a-f]{64})` \|$", section, re.M)
    assert rows, "no `name==version` | Python | artifact | sha256 rows found in the GH200 section"
    tabulated: dict[str, set[str]] = {"3.10": set(), "3.11": set()}
    for name, version, python, digest in rows:
        assert SHA256.match(digest)
        assert lock[norm(name)] == version, f"{name}: doc says {version}, lock pins {lock[norm(name)]}"
        tabulated[python].add(norm(name))
    # a100compat (3.10) installs the whole lock; the gpu env (3.11) takes the lock minus Polars from it
    # (its Polars and all GPU-side pins are hashed in requirements-gpu-aarch64.txt instead)
    assert tabulated["3.10"] == set(lock)
    assert tabulated["3.11"] == set(lock) - {"polars", "polars-runtime-32"}


# ---- script behaviour (dry-run with shims) ----------------------------------------------------------

def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "evidence").mkdir(parents=True)
    for rel in SCRIPT_FILES:
        shutil.copy(REPO / rel, repo / rel)
    return repo


def make_shims(tmp_path: Path, machine: str, gpu_line: str) -> Path:
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    real_uname = shutil.which("uname")
    (shims / "uname").write_text(f'#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo {machine} ;; *) exec {real_uname} "$@" ;; esac\n')
    (shims / "nvidia-smi").write_text(f'#!/bin/sh\necho "{gpu_line}"\n')
    for shim in shims.iterdir():
        shim.chmod(0o755)
    return shims


def dry_run(tmp_path: Path, machine: str, gpu_line: str, *args: str, repo: Path | None = None) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = repo or make_repo(tmp_path)
    shims = make_shims(tmp_path, machine, gpu_line)
    env = {"PATH": f"{shims}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path / "home"), "TMPDIR": str(tmp_path)}
    result = subprocess.run(["bash", str(repo / "setup_h100.sh"), "--dry-run", "--envs", "both", "--min-free-gib", "0",
                             "--data-root", str(tmp_path / "data"), *args],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def would_fail(output: str) -> list[str]:
    return [line for line in output.splitlines() if "WOULD FAIL here:" in line]


def env_variables(output: str) -> set[str]:
    return set(re.findall(r"^\s+export ([A-Z0-9_]+)=", output, re.M))


def test_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(REPO / "setup_h100.sh")], check=True)


def test_aarch64_gh200_plan(tmp_path):
    out = dry_run(tmp_path, "aarch64", GH200)
    assert not [line for line in would_fail(out) if re.search(r"GPU|jar|checksum|architecture", line)], out
    assert "GH200 / aarch64 path" in out
    assert MAVEN + ARM_JAR in out
    assert f"sha256 == {ARM_JAR_SHA}" in out
    assert X86_JAR_SHA not in out
    assert 'export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-arm64"' in out
    assert f'export RAPIDS_JAR="{tmp_path}/repo/jars/{ARM_JAR}"' in out
    assert "lock layered under requirements-gpu-aarch64.txt" in out
    assert "-r " + str(tmp_path / "repo" / "requirements-gpu-aarch64.txt") in out
    assert "platform-artifacts-" in out


def test_x86_64_h100_plan_is_unchanged(tmp_path):
    out = dry_run(tmp_path, "x86_64", H100)
    assert not [line for line in would_fail(out) if re.search(r"GPU|jar|checksum|architecture", line)], out
    assert "GH200" not in out and "arm64" not in out and "aarch64" not in out
    assert MAVEN + X86_JAR in out
    assert f"sha256 == {X86_JAR_SHA}" in out
    assert 'export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"' in out
    assert f'export RAPIDS_JAR="{tmp_path}/repo/jars/{X86_JAR}"' in out
    assert "lock layered under requirements-gpu.txt" in out
    assert "platform-artifacts" not in out


def test_both_architectures_write_the_same_environment_variables(tmp_path):
    arm = dry_run(tmp_path / "arm", "aarch64", GH200)
    x86 = dry_run(tmp_path / "x86", "x86_64", H100)
    assert env_variables(arm) == env_variables(x86) == {
        "JAVA_HOME", "PATH", "PYTHON_GPU_ENV", "PYTHON_A100COMPAT_ENV", "DATA_ROOT", "RAPIDS_JAR"}


@pytest.mark.parametrize("machine,gpu,message", [
    ("aarch64", H100, "is not a GH200"),
    ("x86_64", GH200, "is not an H100"),
    ("x86_64", "NVIDIA A100-SXM4-40GB, 580.105.08, 40960 MiB", "is not an H100"),
])
def test_wrong_gpu_is_reported(tmp_path, machine, gpu, message):
    out = dry_run(tmp_path, machine, gpu)
    assert any(message in line for line in would_fail(out)), out
    assert "(use --allow-any-gpu for a rehearsal)" in out


def test_allow_any_gpu_downgrades_to_a_warning(tmp_path):
    out = dry_run(tmp_path, "aarch64", H100, "--allow-any-gpu")
    assert "WARNING: GPU is not a GH200 (--allow-any-gpu)" in out
    assert not [line for line in would_fail(out) if "GPU" in line]


def test_expect_gpu_overrides_the_architecture_default(tmp_path):
    for sub, machine, gpu, expected in (("arm", "aarch64", H100, "H100"), ("x86", "x86_64", GH200, "GH200")):
        out = dry_run(tmp_path / sub, machine, gpu, "--expect-gpu", expected)
        assert not [line for line in would_fail(out) if "GPU" in line], out


def test_aarch64_jar_sha_in_evidence_file_must_agree(tmp_path):
    repo = make_repo(tmp_path)
    sha_file = repo / "evidence" / "DEPENDENCY_SHA256.txt"
    original = sha_file.read_text()

    sha_file.write_text(original + f"{'0' * 64}  jars/{ARM_JAR}\n")
    bad = dry_run(tmp_path, "aarch64", GH200, repo=repo)
    assert any("this script records" in line and ARM_JAR in line for line in would_fail(bad)), bad

    sha_file.write_text(original + f"{ARM_JAR_SHA}  jars/{ARM_JAR}\n")
    good = dry_run(tmp_path, "aarch64", GH200, repo=repo)
    assert not [line for line in would_fail(good) if "jar" in line or "checksum" in line], good
    assert f"sha256 == {ARM_JAR_SHA}" in good


def test_unsupported_architecture_is_reported(tmp_path):
    out = dry_run(tmp_path, "riscv64", H100)
    assert any("unsupported architecture 'riscv64'" in line for line in would_fail(out)), out


def test_help_documents_the_new_option(tmp_path):
    out = subprocess.run(["bash", str(REPO / "setup_h100.sh"), "--help"], capture_output=True, text=True, check=True).stdout
    assert "--expect-gpu" in out and "requirements-gpu-aarch64.txt" in out


# ---- preflight.py env.jar ------------------------------------------------------------------------------

@pytest.fixture
def preflight():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import preflight as module
    return module


def test_preflight_jar_constants_match_the_setup_script(preflight):
    assert preflight.JAR_SHA256_AARCH64 == ARM_JAR_SHA
    assert preflight.JAR_RELATIVE_AARCH64 == f"jars/{ARM_JAR}" and preflight.JAR_RELATIVE == f"jars/{X86_JAR}"
    script = (REPO / "setup_h100.sh").read_text()
    assert f'JAR_SHA_BUILTIN="{preflight.JAR_SHA256_AARCH64}"' in script


@pytest.mark.parametrize("machine,jar", [("x86_64", X86_JAR), ("aarch64", ARM_JAR), ("arm64", ARM_JAR), ("AARCH64", ARM_JAR)])
def test_preflight_jar_follows_the_architecture(preflight, monkeypatch, machine, jar):
    assert preflight.jar_relative(machine) == f"jars/{jar}"
    monkeypatch.setattr(preflight.platform, "machine", lambda: machine)
    assert preflight.build_parser().parse_args([]).jar == REPO / "jars" / jar


def test_preflight_x86_expected_sha_is_unchanged(preflight):
    evidence = (REPO / "evidence" / "DEPENDENCY_SHA256.txt").read_text()
    assert preflight.expected_jar_sha256(evidence, "x86_64") == (X86_JAR_SHA, None)
    assert preflight.expected_jar_sha256("", "x86_64") == (None, None)
    # an arm64 entry never leaks into the x86_64 answer; the last x86_64 entry wins, as before
    assert preflight.expected_jar_sha256(evidence + f"{'1' * 64}  jars/{ARM_JAR}\n", "x86_64") == (X86_JAR_SHA, None)
    assert preflight.expected_jar_sha256(evidence + f"{'2' * 64}  jars/{X86_JAR}\n", "x86_64") == ("2" * 64, None)


@pytest.mark.parametrize("machine", ["aarch64", "arm64"])
def test_preflight_aarch64_expected_sha(preflight, machine):
    evidence = (REPO / "evidence" / "DEPENDENCY_SHA256.txt").read_text()  # lists only the x86_64 jar
    assert preflight.expected_jar_sha256(evidence, machine) == (ARM_JAR_SHA, None)
    assert preflight.expected_jar_sha256("", machine) == (ARM_JAR_SHA, None)
    assert preflight.expected_jar_sha256(evidence + f"{ARM_JAR_SHA}  jars/{ARM_JAR}\n", machine) == (ARM_JAR_SHA, None)
    expected, problem = preflight.expected_jar_sha256(evidence + f"{'0' * 64}  jars/{ARM_JAR}\n", machine)
    assert expected is None and "0" * 64 in problem and ARM_JAR_SHA in problem and ARM_JAR in problem


def env_jar_check(preflight, monkeypatch, tmp_path, machine, evidence_lines, jar_content=b"fake jar\n", write_jar=True,
                  arm_sha=None):
    """Run the real check_environment for spark_gpu only and return its env.jar item."""
    (tmp_path / "evidence").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "evidence" / "requirements.lock.txt", tmp_path / "evidence")
    shutil.copy(REPO / "requirements-gpu.txt", tmp_path)
    (tmp_path / "evidence" / "DEPENDENCY_SHA256.txt").write_text("".join(line + "\n" for line in evidence_lines))
    jar = tmp_path / "jar.bin"
    if write_jar:
        jar.write_bytes(jar_content)
    monkeypatch.setattr(preflight, "HERE", tmp_path)
    monkeypatch.setattr(preflight.platform, "machine", lambda: machine)
    monkeypatch.setattr(preflight, "JAR_SHA256_AARCH64", arm_sha or ARM_JAR_SHA)  # reset between calls in one test
    parser = preflight.build_parser()
    args = parser.parse_args(["--jar", str(jar), "--data-root", str(tmp_path), "--output", str(tmp_path / "out"),
                              "--engines", "spark_gpu"])
    preflight.resolve_args(parser, args)
    checks = preflight.Checks(quiet=True)
    preflight.check_environment(checks, args, ["spark_gpu"])
    return [item for item in checks.items if item["id"] == "env.jar"]


def test_preflight_env_jar_x86_64(preflight, monkeypatch, tmp_path):
    sha = hashlib.sha256(b"fake jar\n").hexdigest()
    (ok,) = env_jar_check(preflight, monkeypatch, tmp_path / "ok", "x86_64", [f"{sha}  jars/{X86_JAR}"])
    assert ok["status"] == "PASS" and ok["detail"] == "jar.bin sha256 matches"
    (bad,) = env_jar_check(preflight, monkeypatch, tmp_path / "bad", "x86_64", [f"{'0' * 64}  jars/{X86_JAR}"])
    assert bad["status"] == "FAIL" and bad["detail"] == f"jar.bin sha256 MISMATCH {sha}"
    (none,) = env_jar_check(preflight, monkeypatch, tmp_path / "none", "x86_64", [f"{ARM_JAR_SHA}  jars/{ARM_JAR}"])
    assert none["status"] == "FAIL" and none["detail"] == "no checksum for the jar in evidence/DEPENDENCY_SHA256.txt"
    (gone,) = env_jar_check(preflight, monkeypatch, tmp_path / "gone", "x86_64", [f"{sha}  jars/{X86_JAR}"], write_jar=False)
    assert gone["status"] == "FAIL" and gone["detail"].endswith("jar.bin not found")


def test_preflight_env_jar_aarch64(preflight, monkeypatch, tmp_path):
    sha = hashlib.sha256(b"fake jar\n").hexdigest()
    x86_only = [f"{X86_JAR_SHA}  jars/{X86_JAR}"]
    # the sha256 recorded in preflight.py is used when evidence/DEPENDENCY_SHA256.txt has no arm64 entry
    (ok,) = env_jar_check(preflight, monkeypatch, tmp_path / "ok", "aarch64", x86_only, arm_sha=sha)
    assert ok["status"] == "PASS" and ok["detail"] == "jar.bin sha256 matches"
    # the real recorded value does not match arbitrary bytes, and the x86_64 sha is never accepted
    (bad,) = env_jar_check(preflight, monkeypatch, tmp_path / "bad", "aarch64", x86_only)
    assert bad["status"] == "FAIL" and bad["detail"] == f"jar.bin sha256 MISMATCH {sha}"
    (x86,) = env_jar_check(preflight, monkeypatch, tmp_path / "x86", "aarch64", [f"{sha}  jars/{X86_JAR}"])
    assert x86["status"] == "FAIL" and "MISMATCH" in x86["detail"]
    # an arm64 entry in the evidence file is honoured when it agrees and fails the check when it does not
    (agree,) = env_jar_check(preflight, monkeypatch, tmp_path / "agree", "aarch64", x86_only + [f"{sha}  jars/{ARM_JAR}"], arm_sha=sha)
    assert agree["status"] == "PASS"
    (clash,) = env_jar_check(preflight, monkeypatch, tmp_path / "clash", "aarch64", x86_only + [f"{'0' * 64}  jars/{ARM_JAR}"], arm_sha=sha)
    assert clash["status"] == "FAIL" and "preflight.py records" in clash["detail"]

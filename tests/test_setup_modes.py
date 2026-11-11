"""setup_h100.sh --cpu-only and the optional power steps (--enable-rapl-read, --revert-rapl-read,
--install-power-tools, --power-only).

No GPU, network or root needed: --cpu-only is checked with --dry-run in a scratch copy of the repo (with
`uname` shimmed and no nvidia-smi on PATH), and the RAPL steps are also run for real against a fake
powercap tree with a `sudo` shim that just execs its arguments. tests/test_setup_arch.py covers the
default (GPU) behaviour; the last section here pins it so the new flags cannot leak into it.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT_FILES = ("setup_h100.sh", "requirements-gpu.txt", "requirements-gpu-aarch64.txt", "requirements-cpu.txt",
                "evidence/requirements.lock.txt", "evidence/DEPENDENCY_SHA256.txt")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)")
H100 = "NVIDIA H100 80GB HBM3, 580.105.08, 81559 MiB"
RULE_MARK = "managed by setup_h100.sh --enable-rapl-read"
CPU_PACKAGES = {"polars", "polars-runtime-32", "numpy", "packaging", "typing-extensions"}
GPU_ONLY = re.compile(r"cuda|cudf|cupy|kvikio|rapids|rmm|ucx|nvidia|nvtx|pylibcudf|nvcomp", re.I)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict[str, str]:
    found = {}
    for line in path.read_text().splitlines():
        match = PIN.match(line.strip())
        if match:
            found[norm(match.group(1))] = match.group(2)
    return found


def make_repo(tmp_path: Path, files=SCRIPT_FILES) -> Path:
    repo = tmp_path / "repo"
    (repo / "evidence").mkdir(parents=True, exist_ok=True)
    for rel in files:
        shutil.copy(REPO / rel, repo / rel)
    return repo


def make_shims(tmp_path: Path, machine: str, gpu_line: str | None, sudo: bool = False, nvidia_fails: bool = False) -> Path:
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    real_uname = shutil.which("uname")
    (shims / "uname").write_text(f'#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo {machine} ;; *) exec {real_uname} "$@" ;; esac\n')
    if gpu_line is not None:
        (shims / "nvidia-smi").write_text(f'#!/bin/sh\necho "{gpu_line}"\n')
    if nvidia_fails:
        (shims / "nvidia-smi").write_text("#!/bin/sh\necho 'NVIDIA-SMI has failed' >&2\nexit 9\n")
    if sudo:
        (shims / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
    for shim in shims.iterdir():
        shim.chmod(0o755)
    return shims


def run_script(tmp_path: Path, *args: str, machine="x86_64", gpu=None, dry=True, repo: Path | None = None, sudo=False,
               nvidia_fails=False, env_extra=None, check=True) -> subprocess.CompletedProcess:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = repo or make_repo(tmp_path)
    shims = make_shims(tmp_path, machine, gpu, sudo=sudo, nvidia_fails=nvidia_fails)
    env = {"PATH": f"{shims}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path / "home"), "TMPDIR": str(tmp_path),
           **(env_extra or {})}
    (tmp_path / "home").mkdir(exist_ok=True)
    cmd = ["bash", str(repo / "setup_h100.sh"), *(["--dry-run"] if dry else []), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def cpu_dry_run(tmp_path: Path, *args: str, **kwargs) -> str:
    result = run_script(tmp_path, "--cpu-only", "--envs", "both", "--min-free-gib", "0", "--data-root", str(tmp_path / "data"),
                        *args, **kwargs)
    return result.stdout + result.stderr


def would_fail(output: str) -> list[str]:
    return [line for line in output.splitlines() if "WOULD FAIL here:" in line]


def env_variables(output: str) -> set[str]:
    return set(re.findall(r"^\s+export ([A-Z0-9_]+)=", output, re.M))


# ---- requirements-cpu.txt ------------------------------------------------------------------------

def test_cpu_pins_are_the_gpu_pins_minus_gpu_packages():
    cpu, gpu = pins(REPO / "requirements-cpu.txt"), pins(REPO / "requirements-gpu.txt")
    assert set(cpu) == CPU_PACKAGES
    assert {name: gpu[name] for name in cpu} == cpu, "requirements-cpu.txt drifted from requirements-gpu.txt"


def test_cpu_pins_contain_no_gpu_package():
    assert not [name for name in pins(REPO / "requirements-cpu.txt") if GPU_ONLY.search(name)]


def test_cpu_file_keeps_the_polars_override_the_installer_looks_for():
    lines = (REPO / "requirements-cpu.txt").read_text().splitlines()
    assert any(line.startswith("polars==") for line in lines)        # setup_h100.sh: grep -qE '^polars=='
    assert any(line.startswith("polars-runtime-32==") for line in lines)


def test_cpu_pins_do_not_overlap_the_lock_apart_from_polars():
    lock = pins(REPO / "evidence" / "requirements.lock.txt")
    assert not (set(pins(REPO / "requirements-cpu.txt")) & (set(lock) - {"polars", "polars-runtime-32"}))


# ---- --cpu-only -----------------------------------------------------------------------------------

def test_cpu_only_plan_needs_no_gpu_and_builds_no_gpu_environment(tmp_path):
    out = cpu_dry_run(tmp_path)                                       # no nvidia-smi anywhere on PATH
    assert not would_fail(out), out
    assert "GPU check skipped (--cpu-only)" in out and "RAPIDS jar skipped (--cpu-only)" in out
    assert "[cpu] lock layered under requirements-cpu.txt" in out
    assert "-r " + str(tmp_path / "repo" / "requirements-cpu.txt") in out
    for forbidden in ("requirements-gpu", "RAPIDS_JAR", "PYTHON_GPU_ENV", "maven", "rapids-4-spark", "cudf_polars"):
        assert forbidden not in out, forbidden
    assert "plan: GPU: none (--cpu-only)" in out and "plan: env cpu:" in out
    assert "jar:" not in out.replace("RAPIDS jar skipped", "")


def test_cpu_only_environment_file(tmp_path):
    out = cpu_dry_run(tmp_path)
    assert f"would write {tmp_path}/repo/rerun/cpu-env.sh" in out and "h100-env.sh" not in out
    assert env_variables(out) == {"JAVA_HOME", "PATH", "PYTHON_CPU_ENV", "PYTHON_A100COMPAT_ENV", "DATA_ROOT"}
    assert 'export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"' in out
    assert "next:" not in out                                          # dry-run prints no summary hints


def test_cpu_only_on_aarch64_uses_the_arm_jdk_and_no_gpu_artifacts(tmp_path):
    out = cpu_dry_run(tmp_path, machine="aarch64")
    assert not would_fail(out), out
    assert "CPU-only path" in out and 'export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-arm64"' in out
    assert "GH200" not in out and "arm64.jar" not in out and "requirements-gpu-aarch64" not in out
    assert "-r " + str(tmp_path / "repo" / "requirements-cpu.txt") in out


def test_cpu_only_ignores_a_broken_nvidia_smi(tmp_path):
    out = cpu_dry_run(tmp_path, nvidia_fails=True)
    assert not would_fail(out), out


def test_cpu_only_needs_neither_the_gpu_pin_file_nor_the_jar_checksums(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "requirements-gpu.txt").unlink()
    (repo / "evidence" / "DEPENDENCY_SHA256.txt").unlink()
    out = cpu_dry_run(tmp_path, repo=repo)
    assert not would_fail(out), out


def test_cpu_only_reports_a_missing_cpu_pin_file(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "requirements-cpu.txt").unlink()
    assert any("missing" in line and "requirements-cpu.txt" in line for line in would_fail(cpu_dry_run(tmp_path, repo=repo)))


def test_cpu_only_honours_cpu_requirements(tmp_path):
    custom = tmp_path / "my-pins.txt"
    custom.write_text("polars==1.44.2\npolars-runtime-32==1.44.2\n")
    out = cpu_dry_run(tmp_path, "--cpu-requirements", str(custom))
    assert f"-r {custom}" in out and "[cpu] lock layered under my-pins.txt" in out


def test_cpu_only_a100compat_env_is_the_unmodified_lock(tmp_path):
    out = cpu_dry_run(tmp_path)
    assert "uv pip install --python " + str(tmp_path / "repo" / ".venv-a100compat" / "bin" / "python") in out


def test_cpu_only_keeps_data_volume_and_uv_steps(tmp_path):
    out = cpu_dry_run(tmp_path)
    assert "data root:" in out and "uv missing; installing uv==" in out and "openjdk-17-jre-headless" in out


# ---- default behaviour is untouched --------------------------------------------------------------------

def test_default_plan_mentions_no_new_option(tmp_path):
    out = run_script(tmp_path, "--envs", "both", "--min-free-gib", "0", "--data-root", str(tmp_path / "data"), gpu=H100)
    text = out.stdout + out.stderr
    for word in ("cpu-only", "RAPL", "rapl", "udev", "ipmitool", "linux-tools", "power", "cpu-env.sh", "requirements-cpu"):
        assert word not in text, word
    assert "RAPIDS jar skipped" not in text and "GPU check skipped" not in text
    assert "h100-env.sh" in text and "requirements-gpu.txt" in text


def test_help_documents_the_new_options():
    out = subprocess.run(["bash", str(REPO / "setup_h100.sh"), "--help"], capture_output=True, text=True, check=True).stdout
    for option in ("--cpu-only", "--cpu-requirements", "--enable-rapl-read", "--revert-rapl-read", "--install-power-tools",
                   "--power-only", "CVE-2020-8694", "--expect-gpu", "requirements-gpu-aarch64.txt"):
        assert option in out, option


def test_script_is_still_valid_bash():
    subprocess.run(["bash", "-n", str(REPO / "setup_h100.sh")], check=True)


# ---- power steps: dry-run ------------------------------------------------------------------------------

def test_enable_rapl_read_dry_run_warns_and_plans(tmp_path):
    powercap = tmp_path / "powercap"
    for zone in ("intel-rapl:0", "intel-rapl:0:0"):
        (powercap / zone).mkdir(parents=True)
        (powercap / zone / "energy_uj").write_text("1\n")
    out = run_script(tmp_path, "--power-only", "--enable-rapl-read", env_extra={"POWERCAP_DIR": str(powercap), "UDEV_RULES_DIR": str(tmp_path / "udev")})
    text = out.stdout + out.stderr
    assert "WARNING (--enable-rapl-read)" in text and "CVE-2020-8694" in text and "single-tenant" in text
    assert text.count("would run: sudo_cmd chmod a+r") == 2 and "intel-rapl:0:0/energy_uj" in text.replace("\\", "")
    assert f"would write {tmp_path}/udev/99-benchmark-rapl-read.rules" in text
    assert 'SUBSYSTEM=="powercap"' in text and "udevadm control --reload-rules" in text
    assert not (tmp_path / "udev").exists() and oct(stat.S_IMODE((powercap / "intel-rapl:0" / "energy_uj").stat().st_mode)) != "0o444"


def test_enable_rapl_read_is_part_of_a_normal_dry_run_after_apt(tmp_path):
    out = run_script(tmp_path, "--cpu-only", "--enable-rapl-read", "--install-power-tools", "--min-free-gib", "0",
                     "--data-root", str(tmp_path / "data"), env_extra={"POWERCAP_DIR": str(tmp_path / "none")})
    text = out.stdout + out.stderr
    assert text.index("openjdk-17-jre-headless") < text.index("ipmitool") < text.index("WARNING (--enable-rapl-read)")
    assert text.index("WARNING (--enable-rapl-read)") < text.index("uv missing")
    assert "no energy_uj under" in text


def test_install_power_tools_dry_run(tmp_path):
    text = (lambda r: r.stdout + r.stderr)(run_script(tmp_path, "--power-only", "--install-power-tools"))
    kernel = subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip()
    lines = [line for line in text.splitlines() if "would run" in line and "apt-get install" in line]
    assert len(lines) == 2                                            # apt is all-or-nothing: the kernel package gets its own call
    assert lines[0].endswith("ipmitool linux-tools-common") and lines[1].endswith(f"linux-tools-{kernel}")
    assert "kernel interfaces are not changed" in text


def test_install_power_tools_respects_skip_apt(tmp_path):
    text = (lambda r: r.stdout + r.stderr)(run_script(tmp_path, "--power-only", "--install-power-tools", "--skip-apt"))
    assert "power tools skipped (--skip-apt)" in text and "apt-get install" not in text


def test_power_only_needs_no_pin_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(REPO / "setup_h100.sh", repo / "setup_h100.sh")
    out = run_script(tmp_path, "--power-only", "--install-power-tools", repo=repo)
    assert not would_fail(out.stdout + out.stderr)


@pytest.mark.parametrize("args,message", [
    (["--power-only"], "--power-only needs"),
    (["--enable-rapl-read", "--revert-rapl-read"], "mutually exclusive"),
])
def test_bad_power_flag_combinations_exit_2(tmp_path, args, message):
    result = run_script(tmp_path, *args, check=False)
    assert result.returncode == 2 and message in result.stderr


def test_revert_implies_power_only(tmp_path):
    text = (lambda r: r.stdout + r.stderr)(run_script(tmp_path, "--revert-rapl-read", env_extra={"POWERCAP_DIR": str(tmp_path / "none")}))
    assert "power steps only (--power-only)" in text and "uv" not in text.replace("udev", "")


# ---- power steps: for real against a fake powercap tree -----------------------------------------------------

def fake_powercap(root: Path, mode: int = 0o400) -> list[Path]:
    files = []
    for zone in ("intel-rapl:0", "intel-rapl:0:0", "intel-rapl-mmio:0"):
        directory = root / zone
        directory.mkdir(parents=True)
        (directory / "name").write_text("package-0\n")
        energy = directory / "energy_uj"
        energy.write_text("123\n")
        energy.chmod(mode)
        files.append(energy)
    return files


def real_power_run(tmp_path: Path, *args: str, powercap: Path, udev: Path, **kwargs):
    return run_script(tmp_path, "--power-only", *args, dry=False, sudo=True,
                      env_extra={"POWERCAP_DIR": str(powercap), "UDEV_RULES_DIR": str(udev)}, **kwargs)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_enable_then_revert_rapl_read_for_real(tmp_path):
    powercap, udev = tmp_path / "powercap", tmp_path / "udev"
    files = fake_powercap(powercap)
    names = [powercap / z / "name" for z in ("intel-rapl:0", "intel-rapl:0:0")]
    name_modes = [mode_of(n) for n in names]

    result = real_power_run(tmp_path, "--enable-rapl-read", powercap=powercap, udev=udev)
    assert all(mode_of(f) & 0o444 == 0o444 for f in files), [oct(mode_of(f)) for f in files]
    assert [mode_of(n) for n in names] == name_modes                              # only energy_uj is touched
    rule = (udev / "99-benchmark-rapl-read.rules").read_text()
    assert RULE_MARK in rule and 'SUBSYSTEM=="powercap"' in rule and "chmod a+r /sys%p/energy_uj" in rule
    assert mode_of(udev / "99-benchmark-rapl-read.rules") == 0o644
    assert "CVE-2020-8694" in result.stdout and "OK -- power setup complete" in result.stdout

    again = real_power_run(tmp_path / "again", "--enable-rapl-read", powercap=powercap, udev=udev, repo=tmp_path / "repo")
    assert "udev rule already in place" in again.stdout and (udev / "99-benchmark-rapl-read.rules").read_text() == rule

    real_power_run(tmp_path / "revert", "--revert-rapl-read", powercap=powercap, udev=udev, repo=tmp_path / "repo")
    assert all(mode_of(f) == 0o400 for f in files)
    assert not (udev / "99-benchmark-rapl-read.rules").exists()


def test_enable_rapl_read_without_powercap_still_installs_the_rule(tmp_path):
    udev = tmp_path / "udev"
    result = real_power_run(tmp_path, "--enable-rapl-read", powercap=tmp_path / "missing", udev=udev)
    assert "nothing to chmod now" in result.stdout and RULE_MARK in (udev / "99-benchmark-rapl-read.rules").read_text()


def test_foreign_udev_rule_is_never_overwritten(tmp_path):
    powercap, udev = tmp_path / "powercap", tmp_path / "udev"
    files = fake_powercap(powercap)
    udev.mkdir()
    foreign = udev / "99-benchmark-rapl-read.rules"
    foreign.write_text("# somebody else's rule\n")
    result = real_power_run(tmp_path, "--enable-rapl-read", powercap=powercap, udev=udev, check=False)
    assert result.returncode == 1 and "is not ours; not overwriting it" in result.stderr + result.stdout
    assert foreign.read_text() == "# somebody else's rule\n"

    revert = real_power_run(tmp_path / "revert", "--revert-rapl-read", powercap=powercap, udev=udev, repo=tmp_path / "repo")
    assert "is not ours; left in place" in revert.stdout and foreign.exists()
    assert all(mode_of(f) == 0o400 for f in files)


def test_revert_without_anything_to_revert(tmp_path):
    result = real_power_run(tmp_path, "--revert-rapl-read", powercap=tmp_path / "missing", udev=tmp_path / "udev")
    assert "no udev rule at" in result.stdout and "restored to root-only (0 files)" in result.stdout

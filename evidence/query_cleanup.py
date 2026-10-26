"""Untimed, observable cleanup between independent benchmark actions.

Call cleanup only after the action function returns and releases its DataFrame.
Files are inspected by metadata only and are never deleted by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import gc
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time


_GONE = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_dir(path, *, dir_fd=None):
    try:
        return os.open(path, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in _GONE:
            return None
        raise


def discover_blockmgr_dirs(extra_roots=()) -> set[Path]:
    """Find immediate blockmgr-* directories without following symlinks.

    extra_roots supplies any spark.local.dir roots. SPARK_LOCAL_DIRS is a
    comma-separated Spark path list; the system temporary root is always added.
    Capture this set before/after Spark setup and pass only the difference on.
    """
    if isinstance(extra_roots, (str, os.PathLike)):
        extra_roots = [extra_roots]
    roots = [tempfile.gettempdir(), *extra_roots]
    roots.extend(p.strip() for p in os.environ.get("SPARK_LOCAL_DIRS", "").split(",")
                 if p.strip())
    found = set()
    for root in {Path(p).expanduser().absolute() for p in roots}:
        fd = _open_dir(root)
        if fd is None:
            continue
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    try:
                        if entry.name.startswith("blockmgr-") and entry.is_dir(follow_symlinks=False):
                            found.add(root / entry.name)
                    except FileNotFoundError:
                        continue
        finally:
            os.close(fd)
    return found


def _shuffle_usage(owned_dirs) -> tuple[int, int]:
    """Walk only owned directories via no-follow descriptors; tolerate removal."""
    count = size = 0

    def walk(fd):
        nonlocal count, size
        with os.scandir(fd) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = _open_dir(entry.name, dir_fd=fd)
                        if child is not None:
                            try:
                                walk(child)
                            finally:
                                os.close(child)
                    elif stat.S_ISREG(info.st_mode) and entry.name.startswith(
                        ("shuffle_", "temp_shuffle_")
                    ):
                        count += 1
                        size += info.st_size
                except FileNotFoundError:
                    continue

    for directory in owned_dirs:
        fd = _open_dir(directory)
        if fd is not None:
            try:
                walk(fd)
            finally:
                os.close(fd)
    return count, size


class CleanupTimeout(TimeoutError):
    """The action barrier failed; record remains suitable for results.json."""

    def __init__(self, record):
        self.record = record
        snapshot = record["after"]
        super().__init__(
            f"{record['engine']} cleanup timed out after {record['seconds']:.3f}s: "
            f"{snapshot['shuffle_files']} shuffle files / "
            f"{snapshot['shuffle_bytes']} bytes; active jobs {snapshot['active_job_ids']}"
        )


class CleanupBarrier:
    def __init__(self, engine, spark=None, owned_blockmgr_dirs=(),
                 timeout_seconds=60, poll_seconds=0.25):
        if engine not in ("polars", "spark_cpu", "spark_gpu"):
            raise ValueError(f"Unknown cleanup engine: {engine!r}")
        if engine != "polars" and spark is None:
            raise ValueError("Spark cleanup requires its SparkSession")
        if any(not math.isfinite(v) or v <= 0 for v in (timeout_seconds, poll_seconds)):
            raise ValueError("Cleanup timeout and poll interval must be finite and positive")
        self.engine, self.spark = engine, spark
        self.owned_dirs = (() if engine == "polars" else
                           tuple(sorted({Path(p).absolute() for p in owned_blockmgr_dirs})))
        self.timeout_seconds, self.poll_seconds = timeout_seconds, poll_seconds

    def _snapshot(self):
        # Polars does not inspect any Spark-owned directory, even if supplied.
        files, size = (0, 0) if self.engine == "polars" else _shuffle_usage(self.owned_dirs)
        jobs = [] if self.engine == "polars" else sorted(
            int(job) for job in self.spark.sparkContext.statusTracker().getActiveJobsIds()
        )
        return {"shuffle_files": files, "shuffle_bytes": size,
                "root_free_bytes": shutil.disk_usage(Path.cwd().anchor).free,
                "active_job_ids": jobs}

    def cleanup(self) -> dict:
        """Request GC and observe zero remaining owned shuffle files/jobs.

        JVM requests occur at most once every two seconds. A successful JVM GC
        call alone is not treated as proof that asynchronous deletion finished.
        """
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        record = {"engine": self.engine, "status": "running", "seconds": 0.0,
                  "started_at_utc": datetime.now(timezone.utc).isoformat(),
                  "owned_blockmgr_dirs": [str(p) for p in self.owned_dirs],
                  "spark_cleanup_applicable": self.engine != "polars",
                  "gc_requests": 0, "python_gc_runs": 0, "python_gc_collected": 0}
        record["before"] = self._snapshot()
        record["after"] = dict(record["before"])

        def finish(status):
            record.update(status=status, seconds=time.monotonic() - started,
                          ended_at_utc=datetime.now(timezone.utc).isoformat())
            return record

        def check_deadline():
            if time.monotonic() >= deadline:
                raise CleanupTimeout(finish("timeout"))

        # Wait for completed action jobs before requesting any collection.
        while record["after"]["active_job_ids"]:
            check_deadline()
            time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))
            record["after"] = self._snapshot()
        check_deadline()
        record["python_gc_collected"] = gc.collect()
        record["python_gc_runs"] = 1
        if self.engine == "polars":
            record["after"] = self._snapshot()
            check_deadline()
            return finish("passed")

        next_gc = 0.0
        while True:
            check_deadline()
            now = time.monotonic()
            if now >= next_gc and not record["after"]["active_job_ids"]:
                self.spark.sparkContext._jvm.java.lang.System.gc()
                record["gc_requests"] += 1
                next_gc = time.monotonic() + 2.0
            record["after"] = self._snapshot()
            check_deadline()
            if not record["after"]["active_job_ids"] and record["after"]["shuffle_files"] == 0:
                return finish("passed")
            time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))

"""Exercise the GPU session gate without CUDA or importing the real Polars engine."""
from __future__ import annotations

import contextlib
import dataclasses
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import worker


class ComputeError(Exception):
    pass


@dataclasses.dataclass
class ResolvedExecutor:
    name: str
    fallback_mode: str | None = None
    max_rows_per_partition: int = 1_000_000


@dataclasses.dataclass
class ResolvedOptions:
    executor: ResolvedExecutor
    raise_on_fail: bool = True


class FakeExpression:
    def sum(self):
        return self

    def alias(self, name):
        return self

    def map_elements(self, function, *, return_dtype):
        return self


class FakePolars:
    __version__ = "1.44.2"
    Int64 = object()

    def __init__(self, *, tiny_error=None, tiny_result=None, canary_error=None,
                 canary_succeeds=False):
        self.tiny_error = tiny_error
        self.tiny_result = tiny_result or {"k": [1, 2, 3], "total": [3.0, 7.0, 5.0]}
        self.canary_error = canary_error or ComputeError(
            "'cuda' conversion failed: NotImplementedError: Query execution with GPU "
            "not possible: unsupported operations: anonymousfunction")
        self.canary_succeeds = canary_succeeds
        self.engines = []
        self.collected = []

    def GPUEngine(self, **settings):
        engine = types.SimpleNamespace(**settings)
        self.engines.append(engine)
        return engine

    def col(self, name):
        return FakeExpression()

    def LazyFrame(self, data):
        return FakeFrame(self, canary="x" in data)


class FakeFrame:
    def __init__(self, pl, canary):
        self.pl = pl
        self.canary = canary

    def group_by(self, *args):
        return self

    def agg(self, *args):
        return self

    def sort(self, *args):
        return self

    def select(self, *args):
        return self

    def collect(self, *, engine):
        self.pl.collected.append((self.canary, engine))
        if self.canary:
            if not self.pl.canary_succeeds:
                raise self.pl.canary_error
        elif self.pl.tiny_error:
            raise self.pl.tiny_error
        return self

    def to_dict(self, *, as_series):
        return self.pl.tiny_result


@contextlib.contextmanager
def fake_cudf(*, change_options=None, missing_config=False):
    root = types.ModuleType("cudf_polars")
    root.__version__ = "26.10.2"
    root.__path__ = []
    utils = types.ModuleType("cudf_polars.utils")
    utils.__path__ = []
    config = types.ModuleType("cudf_polars.utils.config")

    class ConfigOptions:
        @classmethod
        def from_polars_engine(cls, engine):
            resolved = ResolvedOptions(
                ResolvedExecutor(
                    engine.executor,
                    getattr(engine, "executor_options", {}).get("fallback_mode"),
                    getattr(engine, "executor_options", {}).get("max_rows_per_partition", 1_000_000),
                ),
                engine.raise_on_fail,
            )
            if change_options:
                change_options(resolved)
            return resolved

    if not missing_config:
        config.ConfigOptions = ConfigOptions
    modules = {"cudf_polars": root, "cudf_polars.utils": utils, "cudf_polars.utils.config": config}
    with patch.dict(sys.modules, modules):
        yield


class PolarsGpuGuardTests(unittest.TestCase):
    def initialize(self, pl=None, *, executor="streaming", options=None, **fake_options):
        pl = pl or FakePolars()
        with fake_cudf(**fake_options):
            return worker.init_polars_gpu(pl, 0, executor, options or {})

    def test_default_warn_is_explicit_and_gpu_single_partition_is_permitted(self):
        supplied = {"max_rows_per_partition": 10}
        with patch.dict("os.environ", {"CUDF_POLARS__EXECUTOR__FALLBACK_MODE": "raise"}):
            engine, metadata = self.initialize(options=supplied)
        self.assertEqual(supplied, {"max_rows_per_partition": 10})
        self.assertEqual(engine.executor_options["fallback_mode"], "warn")
        self.assertEqual(metadata["cpu_fallback_policy"], "raise")
        self.assertEqual(metadata["streaming_fallback_policy"], "warn")
        self.assertEqual(metadata["effective_executor_options"]["fallback_mode"], "warn")
        self.assertEqual(metadata["effective_executor_options"]["max_rows_per_partition"], 10)
        guard = metadata["session_guard"]
        for flag in ("effective_config_verified", "tiny_query_passed", "unsupported_canary_raised",
                     "unsupported_canary_reason_verified"):
            self.assertIs(guard[flag], True)
        self.assertIs(guard["effective_config"]["raise_on_fail"], True)

    def test_explicit_streaming_raise_is_retained(self):
        engine, metadata = self.initialize(options={"fallback_mode": "raise"})
        self.assertEqual(engine.executor_options["fallback_mode"], "raise")
        self.assertEqual(metadata["streaming_fallback_policy"], "raise")

    def test_in_memory_does_not_receive_streaming_options(self):
        engine, metadata = self.initialize(executor="in-memory")
        self.assertFalse(hasattr(engine, "executor_options"))
        self.assertEqual(metadata["streaming_fallback_policy"], "not_applicable")

    def test_cannot_continue_without_effective_config(self):
        pl = FakePolars()
        with self.assertRaisesRegex(RuntimeError, "ConfigOptions unavailable"):
            self.initialize(pl, missing_config=True)
        self.assertEqual(pl.collected, [])

    def test_effective_cpu_fallback_must_be_disabled(self):
        pl = FakePolars()
        with self.assertRaisesRegex(RuntimeError, "raise_on_fail=False"):
            self.initialize(pl, change_options=lambda cfg: setattr(cfg, "raise_on_fail", False))
        self.assertEqual(pl.collected, [])

    def test_effective_executor_must_match_request(self):
        with self.assertRaisesRegex(RuntimeError, "resolved executor='in-memory'"):
            self.initialize(change_options=lambda cfg: setattr(cfg.executor, "name", "in-memory"))

    def test_effective_streaming_policy_must_match_request(self):
        with self.assertRaisesRegex(RuntimeError, "resolved streaming fallback_mode='silent'"):
            self.initialize(change_options=lambda cfg: setattr(cfg.executor, "fallback_mode", "silent"))

    def test_tiny_query_must_produce_correct_result(self):
        with self.assertRaisesRegex(RuntimeError, "tiny GPU query returned"):
            self.initialize(FakePolars(tiny_result={"k": [1], "total": [999]}))

    def test_tiny_query_environment_failure_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "tiny GPU query failed.*driver unavailable"):
            self.initialize(FakePolars(tiny_error=RuntimeError("driver unavailable")))

    def test_canary_success_cannot_be_mistaken_for_strict_gpu(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported query did not raise"):
            self.initialize(FakePolars(canary_succeeds=True))

    def test_canary_cuda_failure_does_not_prove_cpu_fallback_guard(self):
        with self.assertRaisesRegex(RuntimeError, "unrelated error.*CUDA out of memory"):
            self.initialize(FakePolars(canary_error=RuntimeError("CUDA out of memory")))

    def test_canary_unrelated_unsupported_operation_does_not_prove_udf_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "unrelated error"):
            self.initialize(FakePolars(canary_error=NotImplementedError("Binary datatype")))

    def test_wrapped_udf_translation_cause_is_accepted(self):
        error = ComputeError("CUDA conversion failed")
        error.__cause__ = NotImplementedError("map_elements Python UDF unsupported")
        _, metadata = self.initialize(FakePolars(canary_error=error))
        self.assertIs(metadata["session_guard"]["unsupported_canary_reason_verified"], True)
        self.assertIn("NotImplementedError: map_elements Python UDF unsupported",
                      metadata["session_guard"]["unsupported_canary_error"])


if __name__ == "__main__":
    unittest.main()

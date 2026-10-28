def audit_session(plan, session, item, oracle, artifacts_root=None):
    out = {"session_id": session["session_id"], "status": "FAIL", "errors": [],
           "coverage": {}, "comparisons": [], "plans": []}
    if item.get("status") != "succeeded":
        out["errors"].append(f"Session status is {item.get('status')}")
        return out
    try:
        attempts = item["attempts"]
        if len(attempts) != 1:
            raise ValueError("Replacement attempt requires a separately reviewed protocol-deviation analysis")
        attempt = attempts[0]
        frozen_run_root = Path(attempt["run_root"])
        run_root = remap_path(plan, frozen_run_root, artifacts_root)
        record = read_json(run_root.parent / "session.json")
        if record["session"] != session or record["attempt"] != attempt:
            raise ValueError("Session record differs from journal/plan")
        if record["campaign_sha256"] != plan["_sha256"]:
            raise ValueError("Session belongs to a different campaign")
        path = run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
        frozen_path = frozen_run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
        if (str(frozen_path) != attempt["artifacts"]["results_path"]
                or sha256(path) != attempt["artifacts"]["results_sha256"]
                or sha256(run_root / "schedule.json") != attempt["artifacts"]["schedule_sha256"]):
            raise ValueError("Successful artifact identity changed")
        value = read_json(path)
        module = importlib.import_module("workloads" if session["suite"] == "baseline" else "complex_workloads")
        if (value.get("suite") != session["suite"] or value.get("engine") != session["engine"]
                or value.get("runs") != 3 or value.get("query_seed") != session["query_seed"]
                or float(value.get("scale_factor", -1)) != float(session["scale"])
                or value.get("manifest_sha256") != oracle["manifest_sha256"]
                or value.get("suite_version") != getattr(module, "SUITE_VERSION", "baseline-v1")
                or value.get("queries") != module.QUERY_IDS or not value.get("completed_at_utc")
                or value.get("failure")):
            raise ValueError("Worker metadata differs from frozen session")
        version = {"polars_cpu_144": "1.44.2", "polars_cpu_200": "2.0.0", "polars_gpu": "1.44.2",
                   "spark_cpu": "3.5.9", "spark_gpu": "3.5.9"}[session["config_id"]]
        if value.get("version") != version:
            raise ValueError("Engine version differs")
        if session["engine"] == "polars_gpu":
            check_polars_gpu_metadata(value)
        records = value.get("records", [])
        keys = [(r["query"], r["phase"], r["trial"]) for r in records]
        if any(count != 1 for count in Counter(keys).values()):
            raise ValueError("Duplicate action records")
        if any(key[0] not in module.QUERY_IDS for key in keys):
            raise ValueError("Unknown query in records")
        for query in module.QUERY_IDS:
            expected = {(query, "warmup", 0), *((query, "measured", t) for t in (1, 2, 3))}
            observed = {key for key in keys if key[0] == query}
            failures = [error for error in value.get("query_errors", []) if error["query"] == query]
            if not observed and failures and session["engine"] == "polars_gpu" and query in EXPECTED_UNSUPPORTED:
                if len(failures) != 1 or failures[0].get("phase") != "warmup" or failures[0].get("trial") != 0:
                    raise ValueError(f"Invalid unsupported-case failure records: {query}")
                if not any(token in str(failures[0]).lower() for token in ("notimplementederror", "not supported", "unsupported")):
                    raise ValueError(f"Expected GPU rejection has a different cause: {query}")
                out["coverage"][query] = {"status": "unsupported", "errors": failures}
            elif observed != expected or failures:
                out["coverage"][query] = {"status": "failed", "errors": failures}
                out["errors"].append(f"Incomplete/failed query: {query}")
            else:
                out["coverage"][query] = {"status": "complete"}
        if any(error.get("query") not in module.QUERY_IDS for error in value.get("query_errors", [])):
            raise ValueError("Unknown query failure")
        for record in records:
            for endpoint in ("seconds", "host_result_seconds"):
                timing = record.get(endpoint)
                if isinstance(timing, bool) or not isinstance(timing, (float, int)) or not math.isfinite(timing) or timing <= 0:
                    raise ValueError(f"Invalid {endpoint}")
            if record["host_result_seconds"] < record["seconds"]:
                raise ValueError("Host-result endpoint precedes action endpoint")
            query = record["query"]
            check = compare_values(oracle["queries"][query]["rows"], record["rows"])
            out["comparisons"].append({"query": query, "phase": record["phase"], "trial": record["trial"], **check})
            if not check["passed"]:
                out["errors"].append(f"Oracle mismatch: {query}/{record['phase']}/{record['trial']}")
            plan_path = path.parent / record["plan"]
            if not plan_path.resolve().is_relative_to(path.parent.resolve()) or not plan_path.is_file():
                raise ValueError("Missing or unsafe plan path")
            plan_result = {"query": query, "phase": record["phase"], "trial": record["trial"], "sha256": sha256(plan_path)}
            if session["engine"] == "spark_gpu":
                observed_plan = inspect_gpu_plan(plan_path) if session["suite"] == "baseline" else inspect_plan(plan_path)
                if not observed_plan.get("gpu_operators"):
                    raise ValueError(f"GPU Spark plan has no GPU operators: {query}")
                plan_result.update(observed_plan)
            out["plans"].append(plan_result)
        if session["suite"] == "complex":
            cleaned = [inspect_cleanup(entry, session["engine"]) for entry in value.get("cleanup_records", [])]
            cleaned_keys = [(r["query"], r["phase"], r["trial"]) for r in cleaned]
            if Counter(cleaned_keys) != Counter(keys):
                raise ValueError("Successful complex cleanup barriers do not match actions")
            failed_keys = [(r["query"], r["phase"], r["trial"]) for r in value.get("query_errors", [])]
            failed_cleaned = [inspect_cleanup(entry, session["engine"]) for entry in value.get("failed_query_cleanup_records", [])]
            if Counter((r["query"], r["phase"], r["trial"]) for r in failed_cleaned) != Counter(failed_keys):
                raise ValueError("Failed complex action cleanup barriers do not match errors")
        out["results_sha256"] = sha256(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        out["errors"].append(f"{type(exc).__name__}: {exc}")
    out["status"] = "PASS" if not out["errors"] else "FAIL"
    return out

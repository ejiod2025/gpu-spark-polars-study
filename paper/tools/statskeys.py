"""Shared stats.json loading, key scanning and number formatting.

Used by paper/tools/stats_to_tex.py (LaTeX) and article/tools/fill_article.py
(Markdown) so the paper and the article print every statistic identically.

stats.json may be flat ({"c1.baseline.sf300.estimate": 2.1}) or nested
({"c1": {"baseline": {"sf300": {"estimate": 2.1}}}}); nested objects are
flattened with "." joins. Top-level keys starting with "_" are reserved:
  _display: {key: "verbatim text"}     overrides the formatted value of a key (a manual
                                       value: refused unless the file is synthetic, the build
                                       is a draft, or manual overrides are allowed explicitly,
                                       which watermarks the output; see manual_values)
  _format:  {key-or-glob: "{:.3f}"}     overrides the format rule for matching keys
Anything else beginning with "_" (for example _meta) is ignored.
"""

import fnmatch
import json
import math
import re
from pathlib import Path

KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

# (pattern on the full key, python format spec). First match wins.
FORMAT_RULES = [
    (re.compile(r"(^|\.)max_rel_diff$"), "sci"),
    (re.compile(r"^(c[1-4]|bridge|bridge_summary|a100_h100|xhost|exploratory|ladder)\..*\.(estimate|ci_low|ci_high|min|max)$"), "{:.2f}"),
    (re.compile(r"^(median|legacy)\."), "{:.3f}"),
    (re.compile(r"^startup\."), "{:.2f}"),
    (re.compile(r"^(cv|order)\."), "{:.1f}"),
    (re.compile(r"^(peak|task|pool)_gpu_mem_gib\."), "{:.1f}"),
    (re.compile(r"^gpu_tel\..*\.idle(\.min|\.max)?$"), "{:.1f}"),
    (re.compile(r"^gpu_tel\."), "{:.0f}"),
    (re.compile(r"^host\.(host_ram_gib|gpu_memory_gib)$"), "{:.1f}"),
]
DEFAULT_FLOAT = "{:.3g}"

# GPU-generation ladder (amendments A2, A3). The H100 campaign is primary and unprefixed; each
# additional host runs its own campaign, loaded under its host id as a namespace. The cross-host
# comparison comes as one file per host (analyze_campaign.py's xhost_<HOST>.json, keys
# {engine}.{suite}.{scale}.*, ratio t_host / t_H100), loaded with prefix xhost_<host> under the
# namespace xhost.<host>.
HOSTS = ("a100", "gh200", "b200")
LADDER = ("a100", "h100", "gh200", "b200")  # generation order, for lists printed in the paper
HOST_LABELS = {"a100": "A100", "h100": "H100", "gh200": "GH200", "b200": "B200"}
XHOST = {f"xhost_{h}": h for h in HOSTS}
PREFIXES = HOSTS + tuple(XHOST)


class StatsError(Exception):
    pass


def flatten(obj, prefix=""):
    out = {}
    for key, value in obj.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten(value, full))
        else:
            out[full] = value
    return out


def load_stats(path):
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise StatsError(f"stats file not found: {path}")
    except json.JSONDecodeError as exc:
        raise StatsError(f"{path} is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        raise StatsError(f"{path}: top level must be a JSON object")
    display = raw.get("_display", {}) or {}
    formats = raw.get("_format", {}) or {}
    flat = flatten({k: v for k, v in raw.items() if not str(k).startswith("_")})
    # Opaque analyzer objects can contain keys no macro could name; they are never printable.
    stats = {k: v for k, v in flat.items() if KEY_RE.match(k)}
    check_intervals(stats)
    for key, value in interval_verdicts(stats).items():
        stats.setdefault(key, value)
    for key, value in derive_paper_keys(stats).items():
        stats.setdefault(key, value)  # keys supplied directly in stats.json win
    synthetic = raw.get("synthetic") is True or (raw.get("_meta") or {}).get("synthetic") is True
    return stats, display, formats, synthetic


def manual_values(provenance, allow_manual, draft):
    """Hand-filled values (_display) are allowed only in synthetic files, draft builds, or with an
    explicit allow_manual, and are always disclosed. Returns (manual, synthetic): the sources
    with manual values as "name: key, ..." strings, and the names of synthetic sources."""
    manual = [f"{p['name']}: {', '.join(sorted(p['display']))}" for p in provenance
              if p["display"] and not p["synthetic"]]
    if manual and not (allow_manual or draft):
        raise StatsError("hand-filled _display values in non-synthetic stats file(s) are not allowed in a "
                         "final build (pass --allow-manual-overrides to accept them with a MANUAL VALUES "
                         "watermark):\n  " + "\n  ".join(manual))
    return manual, [p["name"] for p in provenance if p["synthetic"]]


def interval_verdicts(stats):
    """<prefix>.verdict for every numeric ci_low/ci_high pair: "above 1", "below 1" or "includes 1"
    (the claim strength the prose may use for that interval)."""
    out = {}
    for key, low in stats.items():
        if key.endswith(".ci_low"):
            prefix = key[: -len(".ci_low")]
            high = stats.get(prefix + ".ci_high")
            if _number(low) and _number(high):
                out[prefix + ".verdict"] = "above 1" if low > 1 else "below 1" if high < 1 else "includes 1"
    return out


def check_intervals(stats):
    """Every numeric prefix.{ci_low,estimate,ci_high} triple must be ordered."""
    bad = []
    for key, est in stats.items():
        if not key.endswith(".estimate"):
            continue
        prefix = key[: -len(".estimate")]
        low, high = stats.get(prefix + ".ci_low"), stats.get(prefix + ".ci_high")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (low, est, high)):
            if not low <= est <= high:
                bad.append(f"{prefix}: ci_low={low} estimate={est} ci_high={high}")
    if bad:
        raise StatsError("confidence intervals not ordered (ci_low <= estimate <= ci_high):\n  "
                         + "\n  ".join(bad))


def _float_format(key, formats):
    for pattern, spec in formats.items():
        if key == pattern or fnmatch.fnmatchcase(key, pattern):
            return spec
    bare = key.split(".", 1)[1] if key.split(".")[0] in HOSTS else key
    for regex, spec in FORMAT_RULES:
        if regex.search(bare):
            return spec
    return DEFAULT_FLOAT


def format_value(key, value, formats, target):
    """Return (text, is_markup). target is "tex" or "md"."""
    if value is None:
        return "n/a", False
    if isinstance(value, bool):
        return ("yes" if value else "no"), False
    if isinstance(value, int):
        text = f"{value:,}"
        return (text.replace(",", "{,}") if target == "tex" else text), target == "tex"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StatsError(f"{key}: non-finite value {value!r}")
        spec = _float_format(key, formats)
        if spec == "sci" and value == 0:
            return "0", False
        if spec == "sci":
            mantissa, exponent = f"{value:.2e}".split("e")
            exponent = int(exponent)
            if target == "tex":
                return f"${mantissa}\\times10^{{{exponent}}}$", True
            return f"{mantissa} × 10^{exponent}", False
        text = spec.format(value)
        if target == "tex" and text.startswith("-"):
            return "$-$" + text[1:], True
        return text, False
    if isinstance(value, str):
        return value, False
    if isinstance(value, list):
        raise StatsError(f"{key}: is a list, not a printable statistic")
    raise StatsError(f"{key}: unsupported value type {type(value).__name__}")


# ---------------------------------------------------------------- paper view of the analyzer schema
#
# analyze_campaign.py writes a detailed nested schema (see its STATS_SCHEMA.md). The paper and
# article use a smaller set of named keys; this maps one onto the other. Contrast keys
# ({c}.{suite}.{scale}.*) are identical in both and need no mapping. Derived keys are only added
# when their source keys exist, so a missing source still surfaces as a missing paper key.

ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
ENGINE_LABELS = {"polars": "Polars CPU", "polars_gpu": "Polars GPU", "spark_cpu": "Spark CPU", "spark_gpu": "Spark GPU"}
SCALES = {"baseline": ("sf1", "sf10", "sf30", "sf100", "sf300"), "complex": ("sf30", "sf100", "sf300")}


def _scale_order(scale):
    try:
        return int(str(scale).lower().lstrip("sf"))
    except ValueError:
        return 0
GPU_ENGINES = ("polars_gpu", "spark_gpu")
GIB = 2 ** 30


def _scale_label(value):
    if value is None:
        return "none observed"
    return str(value).upper() if str(value).lower().startswith("sf") else str(value)


def _median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bridge_summary(stats):
    """RQ5 side study (t_2.0.0 / t_1.44.2) per suite: bridge_summary.<suite>.{min,max} of the
    suite-level estimates over scales; .n_rounds (fewest rounds behind any scale); .direction /
    .direction_short from the intervals (every CI below 1: 2.0.0 faster; every CI above 1: slower;
    otherwise not resolved), or, when there are no intervals (a single round), from the point
    estimates and saying so; and the query with the most extreme per-query estimate,
    .extreme_query with its .extreme.{min,max} over scales."""
    out = {}
    suites = {}
    for key, value in stats.items():
        parts = key.split(".")
        if parts[:2] == ["side_study", "ratio"] and len(parts) == 5 and parts[4] == "estimate" and _number(value):
            suite, scale = parts[2], parts[3]
            pre = f"side_study.ratio.{suite}.{scale}"
            suites.setdefault(suite, []).append(
                (value, stats.get(pre + ".ci_low"), stats.get(pre + ".ci_high"), stats.get(pre + ".n_rounds")))
    for suite, cells in suites.items():
        estimates = [v for v, *_ in cells]
        rounds = [n for *_, n in cells if _number(n)]
        out[f"bridge_summary.{suite}.min"] = float(min(estimates))
        out[f"bridge_summary.{suite}.max"] = float(max(estimates))
        if rounds:
            out[f"bridge_summary.{suite}.n_rounds"] = int(min(rounds))
        intervals = all(_number(l) and _number(h) for _, l, h, _ in cells)
        if intervals and all(h < 1 for _, _, h, _ in cells):
            short, long = "faster", "faster than 1.44.2 at every scale (every 95% CI below 1)"
        elif intervals and all(l > 1 for _, l, _, _ in cells):
            short, long = "slower", "slower than 1.44.2 at every scale (every 95% CI above 1)"
        elif intervals:
            short, long = ("not consistently different",
                           "not clearly faster or slower than 1.44.2 at every scale (some 95% CIs include 1)")
        else:
            n = int(min(rounds)) if rounds else 0
            plural = "round" if n == 1 else "rounds"
            if all(v < 1 for v in estimates):
                short = "faster in point estimates"
                long = f"faster than 1.44.2 at every scale in point estimates (from {n} {plural}; no interval)"
            elif all(v > 1 for v in estimates):
                short = "slower in point estimates"
                long = f"slower than 1.44.2 at every scale in point estimates (from {n} {plural}; no interval)"
            else:
                short = "mixed in point estimates"
                long = f"faster at some scales and slower at others in point estimates (from {n} {plural}; no interval)"
        out[f"bridge_summary.{suite}.direction_short"] = short
        out[f"bridge_summary.{suite}.direction"] = long
        out[f"bridge_summary.{suite}.intervals"] = intervals
        per_query = {}
        for key, value in stats.items():
            parts = key.split(".")
            if (parts[:3] == ["side_study", "ratio", suite] and len(parts) == 7 and parts[4] == "q"
                    and parts[6] == "estimate" and _number(value)):
                per_query.setdefault(parts[5], []).append(value)
        if per_query:
            query = min(per_query, key=lambda q: min(per_query[q]))
            if max(per_query[query]) >= 1:  # most extreme in the other direction instead
                query = max(per_query, key=lambda q: max(per_query[q]))
            out[f"bridge_summary.{suite}.extreme_query"] = query
            out[f"bridge_summary.{suite}.extreme.min"] = float(min(per_query[query]))
            out[f"bridge_summary.{suite}.extreme.max"] = float(max(per_query[query]))
    if {"baseline", "complex"} <= set(suites):
        b, c = (f"bridge_summary.{su}" for su in ("baseline", "complex"))
        ranges = (f"time ratio {out[b + '.min']:.2f} to {out[b + '.max']:.2f} on the baseline suite and "
                  f"{out[c + '.min']:.2f} to {out[c + '.max']:.2f} on the complex suite")
        same = out[b + ".direction_short"] == out[c + ".direction_short"]
        if same and out[b + ".intervals"] and out[c + ".intervals"] and out[b + ".direction_short"] in ("faster", "slower"):
            word = out[b + ".direction_short"]
            side = "below" if word == "faster" else "above"
            out["bridge_summary.both"] = f"{word} than 1.44.2 at every scale in both suites (all 95% CIs {side} 1; {ranges})"
        else:
            out["bridge_summary.both"] = (f"{out[b + '.direction']} on the baseline suite and "
                                          f"{out[c + '.direction']} on the complex suite ({ranges})")
    for su in suites:
        out.pop(f"bridge_summary.{su}.intervals", None)
    return out


def _exploratory_summary(stats):
    """exploratory_summary.interval_note: how the E1/E4 intervals were formed, from the fewest
    side-study (n_cpu_rounds) and main (n_gpu_rounds) rounds behind any suite-level cell."""
    cpu, gpu = [], []
    for key, value in stats.items():
        parts = key.split(".")
        if parts[0] == "exploratory" and len(parts) == 5 and _number(value):
            (cpu if parts[4] == "n_cpu_rounds" else gpu if parts[4] == "n_gpu_rounds" else []).append(value)
    if not cpu or not gpu:
        return {}
    c, g = int(min(cpu)), int(min(gpu))
    if c < 2 or g < 2:
        note = (f"point estimates only (from {c} side-study round(s) and {g} main round(s)); "
                "no interval with fewer than two rounds on either side")
    else:
        note = (f"Welch-type 95% interval on the log scale from {c} side-study and {g} main rounds, "
                "because CPU and GPU times come from different, unpaired sessions "
                "(Welch-Satterthwaite df, not an integer)")
    return {"exploratory_summary.interval_note": note,
            "exploratory_summary.n_cpu_rounds": c, "exploratory_summary.n_gpu_rounds": g}


def derive_paper_keys(stats):
    out = {}
    split = [(k.split("."), v) for k, v in stats.items()]

    for parts, value in split:
        # side study and A100 bridge: same fields as the contrasts
        if parts[:2] == ["side_study", "ratio"] and len(parts) == 5:
            out[f"bridge.{parts[2]}.{parts[3]}.{parts[4]}"] = value
        if parts[:2] == ["side_study", "ratio"] and len(parts) == 7 and parts[4] == "q":
            out[f"bridge.{parts[2]}.{parts[3]}.q.{parts[5]}.{parts[6]}"] = value
        if parts[:2] == ["a100_vs_h100", "ratio"] and len(parts) == 6:
            out[f"a100_h100.{parts[2]}.{parts[3]}.{parts[4]}.{parts[5]}"] = value
        # the earlier A100 study's identity and differences (printable scalars only)
        if parts[:2] == ["a100_vs_h100", "study"] and not isinstance(value, (list, dict)):
            out["a100_h100.study." + ".".join(parts[2:])] = value
        # medians.{suite}.{scale}.{query}.{engine}.geomean -> median.{engine}.{suite}.{scale}.{query}
        if parts[0] == "medians" and len(parts) == 6 and parts[5] == "geomean":
            suite, scale, query, engine = parts[1:5]
            status = stats.get(f"medians.{suite}.{scale}.{query}.{engine}.status")
            reason = stats.get(f"medians.{suite}.{scale}.{query}.{engine}.reason_class")
            if value is None and status == "unstable":
                value = "unstable"  # amendment A1: excluded in all rounds, never an estimate
            elif value is None and reason == "unsupported_operation":
                value = "n/s"
            elif value is None:
                value = status or "n/a"
            out[f"median.{engine}.{suite}.{scale}.{query}"] = value
        # legacy.totals.{suite}.{scale}.{engine}.mean_total
        if parts[:2] == ["legacy", "totals"] and len(parts) == 6 and parts[5] == "mean_total":
            out[f"legacy.sum_medians.{parts[4]}.{parts[2]}.{parts[3]}"] = value
        # crossover.{c}.{suite}.first_gpu_faster_scale
        if parts[0] == "crossover" and len(parts) == 4 and parts[3] == "first_gpu_faster_scale":
            out[f"crossover.{parts[2]}.{parts[1]}.scale"] = _scale_label(value)
        # secondary.gpu_memory.summary.{suite}.{scale}.{engine}[.task_reported].max_bytes. The sampled
        # value means different things per engine (secondary.gpu_memory.measure): for polars_gpu the
        # RMM pool's high-water mark (peak_gpu_mem_gib); for spark_gpu the plugin's pool reservation
        # (pool_gpu_mem_gib), with the task-reported maximum allocation (task_gpu_mem_gib) as the
        # per-action measure.
        if parts[:3] == ["secondary", "gpu_memory", "summary"] and isinstance(value, (int, float)):
            suite, scale, engine = parts[3:6]
            if len(parts) == 7 and parts[6] == "max_bytes":
                kind = "pool" if engine == "spark_gpu" else "peak"
                out[f"{kind}_gpu_mem_gib.{engine}.{suite}.{scale}.max"] = value / GIB
            elif parts[6:] == ["task_reported", "max_bytes"]:
                out[f"task_gpu_mem_gib.{engine}.{suite}.{scale}.max"] = value / GIB
        # variability.by_engine.{engine}.{within_session|between_rounds}.median (fraction -> percent)
        if parts[:2] == ["variability", "by_engine"] and len(parts) == 5 and parts[4] == "median":
            kind = {"within_session": "within", "between_rounds": "between"}.get(parts[3])
            if kind and isinstance(value, (int, float)):
                out[f"cv.{kind}.{parts[2]}.median"] = 100.0 * value

    # output agreement with polars (stats.json output_agreement.{suite}.{scale}.{engine}.*): per engine,
    # the largest relative difference over all suite-scale cells and where it occurred, and how many
    # cells had no output outside the gate tolerance (failed == 0)
    agree = {}
    for parts, value in split:
        if parts[0] == "output_agreement" and len(parts) == 5 and parts[4] == "max_rel_diff":
            agree.setdefault(parts[3], []).append((parts[1], parts[2], value))
    for engine, cells in agree.items():
        failed = [stats.get(f"output_agreement.{su}.{sc}.{engine}.failed") for su, sc, _ in cells]
        out[f"agreement_cells.{engine}.total"] = len(cells)
        out[f"agreement_cells.{engine}.within_gate"] = sum(1 for f in failed if f == 0)
        out[f"agreement_cells.{engine}.failed_outputs"] = sum(f for f in failed if isinstance(f, int))
        numeric = [(v, su, sc) for su, sc, v in cells if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeric:
            value, suite, scale = max(numeric)
            query = stats.get(f"output_agreement.{suite}.{scale}.{engine}.max_rel_diff_query")
            out[f"agreement_max.{engine}.max_rel_diff"] = float(value)
            out[f"agreement_max.{engine}.where"] = (f"attained at {query}, {suite} {_scale_label(scale)}" if value > 0
                                                    else "no compared value differed")

    # Polars GPU sample behaviour within sessions (high_water_check): a high-water mark only where it
    # never falls; where it falls it is the pool size at the time of the action
    hw = "secondary.gpu_memory.high_water_check.polars_gpu"
    n, nd = stats.get(hw + ".sessions"), stats.get(hw + ".nondecreasing")
    if _number(n) and _number(nd):
        if nd == n:
            out["gpu_mem_note.polars_gpu"] = (
                f"the sample never fell within a session ({nd} of {n} sessions), so it is the pool's "
                "high-water mark, an upper bound on what a query needs rather than its working set")
        else:
            fall = stats.get(hw + ".largest_fall_bytes")
            by = f" (by up to {fall / GIB:.1f} GiB)" if _number(fall) else ""
            out["gpu_mem_note.polars_gpu"] = (
                f"the sample did not fall in {nd} of {n} sessions but fell in the other {n - nd}{by}; "
                "where it does not fall it is the pool's high-water mark, an upper bound on what a query "
                "needs, and where it falls it is the pool size at the time of the action")
    # Spark GPU sample behaviour (constant_check): constant in every session = consistent with the
    # plugin's pool reservation; otherwise report first-sample and later-minimum medians, no cause
    cc = "secondary.gpu_memory.constant_check.spark_gpu"
    n, c = stats.get(cc + ".sessions"), stats.get(cc + ".constant")
    if _number(n) and _number(c):
        if c == n:
            out["gpu_mem_note.spark_gpu"] = (
                f"the sample was constant within every session ({c} of {n}), consistent with the plugin's "
                "memory-pool reservation made at start-up rather than memory in use")
        else:
            first, later = stats.get(cc + ".first_sample_median_bytes"), stats.get(cc + ".later_minimum_median_bytes")
            medians = (f"; the median first sample was {first / GIB:.1f} GiB and the median later minimum "
                       f"{later / GIB:.1f} GiB" if _number(first) and _number(later) else "")
            out["gpu_mem_note.spark_gpu"] = (
                f"the sample was constant within a session in {c} of {n} sessions{medians}, so there it is "
                "not a fixed pool reservation (we do not infer a cause)")
    if stats.get("secondary.gpu_memory.measure.spark_gpu") == "pool_reservation_only":
        # no task accumulator in this campaign's event logs: there is no per-action Spark GPU value
        for parts, _ in split:
            if parts[:3] == ["secondary", "gpu_memory", "summary"] and len(parts) == 7 and parts[5:] == ["spark_gpu", "max_bytes"]:
                out.setdefault(f"task_gpu_mem_gib.spark_gpu.{parts[3]}.{parts[4]}.max", "not recorded")

    out.update(_bridge_summary(stats))
    out.update(_exploratory_summary(stats))

    # fastest engine per scale: fastest.<engine>.<suite>.won_text = "SF1 (6 of 6) and SF10 (4 of 6)"
    per_scale = {}
    for parts, value in split:
        if parts[0] == "fastest" and len(parts) == 5 and parts[4] == "winner" and value is not None:
            per_scale.setdefault((parts[1], parts[2]), []).append(value)
    for engine in ENGINES:
        for suite in sorted({su for su, _ in per_scale}):
            won = [(sc, cells.count(engine), len(cells)) for (su, sc), cells in sorted(
                per_scale.items(), key=lambda item: _scale_order(item[0][1])) if su == suite and engine in cells]
            out[f"fastest.{engine}.{suite}.won_text"] = (
                _join([f"{_scale_label(sc)} ({n} of {t})" for sc, n, t in won]) if won else "none")
    cells = stats.get("plan_variability.cells")
    if isinstance(cells, list):
        out["plan_variability.cells_text"] = _join([
            f"{c.get('query')} ({ENGINE_LABELS.get(c.get('engine'), c.get('engine'))}, {c.get('suite')} "
            f"{_scale_label(c.get('scale'))}; {c.get('n_plans')} final plans whose measured times "
            f"{'overlap' if c.get('measured_time_ranges_overlap') else 'do not overlap'})"
            for c in cells if isinstance(c, dict)]) or "none"

    # fastest engine counts over query-scale cells
    winners = [(p[1], v) for p, v in split if p[0] == "fastest" and len(p) == 5 and p[4] == "winner"]
    for suite in sorted({s for s, _ in winners}):
        cells = [w for s, w in winners if s == suite and w is not None]
        out[f"fastest.total.{suite}.count"] = len(cells)
        for engine in ENGINES:
            out[f"fastest.{engine}.{suite}.count"] = sum(1 for w in cells if w == engine)

    # GPU coverage over query-scale cells: coverage.matrix.{suite}.{query}.{scale}.{engine}.status.
    # Amendment A1: "partial" = an outcome in some rounds only; reason_class separates unsupported operations
    # from resource exhaustion (out_of_memory).
    matrix = [(p[2], p[3], p[4], p[5], v) for p, v in split
              if p[:2] == ["coverage", "matrix"] and len(p) == 7 and p[6] == "status"]
    if matrix:
        unsupported = set()
        for suite in sorted({m[0] for m in matrix}):
            for engine in GPU_ENGINES:
                cells = [(q, sc, st) for s_, q, sc, e, st in matrix if s_ == suite and e == engine]
                if not cells:
                    continue
                reason = {(q, sc): stats.get(f"coverage.matrix.{suite}.{q}.{sc}.{engine}.reason_class")
                          for q, sc, _ in cells}
                out[f"coverage.{engine}.{suite}.total"] = len(cells)
                out[f"coverage.{engine}.{suite}.supported"] = sum(1 for *_, st in cells if st == "supported")
                # A1 unstable coverage: "partial" cells flagged unstable (a partial cell caused by a
                # timeout is not unstable coverage); older analyzer output has no flag, so count partial.
                out[f"coverage.{engine}.{suite}.unstable"] = sum(
                    1 for q, sc, st in cells if st == "partial"
                    and stats.get(f"coverage.matrix.{suite}.{q}.{sc}.{engine}.unstable", True) is not False)
                out[f"coverage.{engine}.{suite}.resource_exhaustion"] = sum(
                    1 for q, sc, st in cells if st != "supported" and reason[(q, sc)] == "out_of_memory")
                bad = sorted({q for q, _, st in cells if st != "supported"})
                out[f"coverage.{engine}.{suite}.unsupported"] = len(bad)
                if engine == "polars_gpu":
                    unsupported.update(bad)
        out["coverage.polars_gpu.unsupported_list"] = ", ".join(sorted(unsupported)) or "none"
    discrepancies = stats.get("coverage.discrepancies")
    if isinstance(discrepancies, list):
        out["coverage.polars_gpu.unexpected_failures"] = sum(
            1 for d in discrepancies if "/polars_gpu:" in str(d) and "not pre-declared" in str(d))

    # order effects: largest |mean_pct| by position and by predecessor (excluding 'none')
    positions = [v for p, v in split if p[:2] == ["order", "position"] and len(p) == 4
                 and p[3] == "mean_pct" and isinstance(v, (int, float))]
    if positions:
        out["order.position.max_abs_pct"] = max(abs(v) for v in positions)
    preds = [v for p, v in split if p[:2] == ["order", "predecessor"] and len(p) == 4 and p[2] != "none"
             and p[3] == "mean_pct" and isinstance(v, (int, float))]
    if preds:
        out["order.carryover.max_abs_pct"] = max(abs(v) for v in preds)

    # descriptive GPU telemetry (secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.*): percent-scaled
    # copies for the tables (gpu_tel.{engine}.{suite}.{scale}.{util,idle,power,clock}); always printed with
    # secondary.gpu_telemetry.meta.label
    scale = {"util_median_pct": ("util", 1), "idle_fraction": ("idle", 100), "power_fraction_of_limit": ("power", 100),
             "sm_clock_fraction_of_max": ("clock", 100), "mem_util_median_pct": ("memutil", 1)}
    for parts, value in split:
        if parts[:3] == ["secondary", "gpu_telemetry", "cells"] and len(parts) == 7 and parts[6] in scale:
            name, factor = scale[parts[6]]
            out[f"gpu_tel.{parts[5]}.{parts[3]}.{parts[4]}.{name}"] = value * factor if _number(value) else None

    # variability and order summaries for the prose
    higher = [ENGINE_LABELS[e] for e in ENGINES
              if _number(out.get(f"cv.between.{e}.median")) and _number(out.get(f"cv.within.{e}.median"))
              and out[f"cv.between.{e}.median"] > out[f"cv.within.{e}.median"]]
    if any(f"cv.between.{e}.median" in out for e in ENGINES):
        out["cv.between_exceeds_within"] = _join(higher) if higher else "none"
    effects = [(stats.get(f"order.{kind}.{name}.ci_low_pct"), stats.get(f"order.{kind}.{name}.ci_high_pct"))
               for kind, names in (("position", ("1", "2", "3", "4")), ("predecessor", ENGINES)) for name in names]
    effects = [(lo, hi) for lo, hi in effects if _number(lo) and _number(hi)]
    if effects:
        out["order.intervals_total"] = str(len(effects))
        out["order.intervals_including_zero"] = str(sum(1 for lo, hi in effects if lo <= 0 <= hi))

    # startup: median over (suite, scale) cells of the per-cell mean launch-to-ready time
    for engine in ENGINES:
        values = [v for p, v in split if p[:2] == ["secondary", "process"] and len(p) == 6 and p[4] == engine
                  and p[5] == "launch_to_engine_ready_seconds" and isinstance(v, (int, float))]
        if values:
            out[f"startup.{engine}.median_s"] = _median(values)

    # campaign metadata; host identity from the stats.json "host" block (amendments A2, A3)
    for source, target in (("host.gpu", "meta.gpu_name"), ("host.driver_version", "meta.driver_version"),
                           ("host.cpu_model", "meta.cpu_model")):
        if stats.get(source) is not None:
            out[target] = stats[source]
    if stats.get("host.driver_version") is not None and stats.get("host.cuda_version") is not None:
        out["meta.driver_cuda"] = f"{stats['host.driver_version']} (CUDA {stats['host.cuda_version']})"
    # CPU layout (guest-reported lscpu): "vCPUs (threads on physical cores)", e.g. "26 (26 on 13)";
    # without topology fields only "vCPUs (threads)"
    vcpus, threads = stats.get("host.vcpus"), stats.get("host.cpus_arg")
    lo, hi = stats.get("host.cpus_arg_cores_min"), stats.get("host.cpus_arg_cores_max")
    if vcpus is not None and threads is not None:
        cores = None if lo is None or hi is None else (str(lo) if lo == hi else f"{lo}–{hi}")
        out["host.cpu_layout"] = f"{vcpus} ({threads} on {cores})" if cores else f"{vcpus} ({threads})"
        if cores:
            out["host.cpus_arg_cores_text"] = cores
    jar_sha = stats.get("host.rapids_jar_sha256")
    if isinstance(jar_sha, str):  # printable prefix; the full digest stays in stats.json
        out["host.rapids_jar_sha256_short"] = jar_sha[:16]
    later = stats.get("meta.later_amendments")
    if isinstance(later, list):  # amendments added to the design after this host's campaign version
        out["meta.later_amendments_list"] = ", ".join(map(str, later)) or "none"
    if "campaign.n_rounds" in stats:
        out["meta.rounds_completed"] = stats["campaign.n_rounds"]
    excluded = stats.get("campaign.rounds_excluded")
    if isinstance(excluded, list):
        out["meta.rounds_repeated"] = len(excluded)  # older outputs; the analyzer's own meta key wins
        out["meta.rounds_excluded_count"] = len(excluded)
    return out


# ---------------------------------------------------------------- key scanning

TEX_STAT = re.compile(r"\\stat\{([^}]*)\}")
TEX_RATIO = re.compile(r"\\ratio(?:ci|text)\{([^}]*)\}")
MD_FIELD = re.compile(r"\{\{\s*(ci:)?([A-Za-z0-9_.:-]+)\s*\}\}")
MD_COMMENT = re.compile(r"<!--.*?-->", re.S)
RATIO_PARTS = ("estimate", "ci_low", "ci_high")


def _strip_tex_comments(text):
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in text.splitlines())


# Host-conditional text (amendments A2, A3). Each additional host's campaign either ran or did
# not; `status` maps host id -> True (ran), False (not run) or None (undecided), and the pseudo
# host "any" means "at least one additional host ran". LaTeX regions
#   % BEGIN-RAN:<host> ... % END-RAN:<host>        typeset only if <host> ran
#   % BEGIN-NOTRUN:<host> ... % END-NOTRUN:<host>  typeset only if <host> did not run
# may nest (different hosts only). Inline, \hoststat{host}{key}, \hostratioci{host}{prefix} and
# \xhostratioci{host}{rest} (key xhost.<host>.<rest>) print the value if the host ran and a dash
# otherwise. Their keys are required only when the host ran. The Markdown equivalent is
# <!-- IF:<host> --> ... <!-- ELSE:<host> --> ... <!-- ENDIF:<host> -->.
TEX_REGION = re.compile(
    r"^[ \t]*%[ \t]*BEGIN-(RAN|NOTRUN):([a-z0-9]+)\b[^\n]*\n(.*?)^[ \t]*%[ \t]*END-\1:\2\b[^\n]*\n?",
    re.S | re.M)
TEX_MARKER = re.compile(r"^[ \t]*%[ \t]*(BEGIN|END)-(RAN|NOTRUN):([a-z0-9]+)\b", re.M)
TEX_HOSTSTAT = re.compile(r"\\hoststat(?:or)?\{([a-z0-9]+)\}\{([^}]*)\}")
TEX_HOSTRATIO = re.compile(r"\\(x?)hostratioci\{([a-z0-9]+)\}\{([^}]*)\}")
MD_IF = re.compile(r"<!--\s*IF:([a-z0-9]+)\s*-->(.*?)(?:<!--\s*ELSE:\1\s*-->(.*?))?<!--\s*ENDIF:\1\s*-->", re.S)
# A line holding only markers disappears with its line break, so blocks can wrap table rows.
MD_MARKER_LINE = re.compile(r"^[ \t]*((?:<!--\s*(?:IF|ELSE|ENDIF):[a-z0-9]+\s*-->[ \t]*)+)\n", re.M)


def host_state(name, status):
    """True / False / None for a host id or "any", given a status dict (None: all undecided)."""
    if status is None:
        return None
    if name == "any":
        values = [status.get(h) for h in HOSTS]
        if any(v is True for v in values):
            return True
        return False if all(v is False for v in values) else None
    if name not in HOSTS:
        raise StatsError(f"unknown host {name!r} in a conditional region (expected one of {HOSTS} or 'any')")
    return status.get(name)


def _check_markers(text, path):
    """Every BEGIN-<kind>:<host> needs a matching END line; none may be left over."""
    left = TEX_MARKER.search(_tex_resolve(text, None))
    if left:
        raise StatsError(f"{path}: unmatched host region marker {left.group(0).strip()!r}")


def _tex_resolve(text, status):
    """Drop the regions that will not be typeset; undecided regions are kept (markers removed)."""
    def keep(match):
        kind, host, body = match.group(1), match.group(2), match.group(3)
        state = host_state(host, status)
        if state is not None and (kind == "RAN") != state:
            return ""
        return _tex_resolve(body, status)
    return TEX_REGION.sub(keep, text)


def tex_required_keys(paths, status=None):
    """Keys the LaTeX sources need. status None lists every case; a dict selects the case."""
    keys = set()
    for path in paths:
        raw = Path(path).read_text()
        _check_markers(raw, path)
        text = _strip_tex_comments(_tex_resolve(raw, status))
        keys.update(TEX_STAT.findall(text))
        for prefix in TEX_RATIO.findall(text):
            keys.update(f"{prefix}.{part}" for part in RATIO_PARTS)
        for host, key in TEX_HOSTSTAT.findall(text):
            if host_state(host, status) is not False:
                keys.add(f"{host}.{key}")
        for cross, host, rest in TEX_HOSTRATIO.findall(text):
            if host_state(host, status) is not False:
                prefix = f"xhost.{host}.{rest}" if cross else f"{host}.{rest}"
                keys.update(f"{prefix}.{part}" for part in RATIO_PARTS)
    # macro definitions themselves use #1 placeholders
    return sorted(k for k in keys if "#" not in k)


def md_select(text, status, undecided="both"):
    """Resolve IF:<host> blocks. A decided host keeps its IF or ELSE part; an undecided one keeps
    both (undecided="both", for listing keys) or only the IF part (undecided="if", for drafts)."""
    def resolve(part):
        return MD_IF.sub(pick, part)

    def pick(match):
        host, if_part, else_part = match.group(1), match.group(2), match.group(3) or ""
        state = host_state(host, status)
        if state is None and undecided == "if":
            state = True
        if state is None:
            return resolve(if_part) + resolve(else_part)
        return resolve(if_part if state else else_part)
    return resolve(MD_MARKER_LINE.sub(r"\1", text))


def md_required_keys(paths, status=None):
    keys = set()
    for path in paths:
        text = MD_COMMENT.sub("", md_select(Path(path).read_text(), status))
        for is_ci, key in MD_FIELD.findall(text):
            if is_ci:
                keys.update(f"{key}.{part}" for part in RATIO_PARTS)
            else:
                keys.add(key)
    return sorted(keys)


def host_status(prefixes, not_run):
    """Status per additional host from the loaded stats prefixes and {host: reason} for hosts
    declared not run. Raises on contradictions."""
    for host in not_run:
        if host not in HOSTS:
            raise StatsError(f"--not-run {host}=...: unknown host (expected one of {', '.join(HOSTS)})")
        if host in prefixes:
            raise StatsError(f"{host}: give either --stats {host}=PATH or --not-run {host}=REASON, not both")
    unknown = sorted(set(prefixes) - set(PREFIXES) - {"primary"})
    if unknown:
        raise StatsError(f"--stats prefix(es) {', '.join(unknown)}: expected one of {', '.join(PREFIXES)}")
    status = {h: (True if h in prefixes else (False if h in not_run else None)) for h in HOSTS}
    for prefix, host in XHOST.items():
        if prefix in prefixes and not status[host]:
            raise StatsError(f"--stats {prefix}=PATH needs the {host} campaign itself (--stats {host}=PATH)")
    return status


# Provider listing per host, as quoted in the design (amendments A2, A3). GPU memory is listed
# in GB and compared as GiB: drivers report slightly less than the marketing figure.
LISTING = {
    "a100": {"host_ram_gib": 200, "gpu_memory_gib": 40, "vcpus": 30},
    "h100": {"host_ram_gib": 225, "gpu_memory_gib": 80, "vcpus": 26},
    "gh200": {"host_ram_gib": 432, "gpu_memory_gib": 96, "vcpus": 64},
    "b200": {"host_ram_gib": 360, "gpu_memory_gib": 180, "vcpus": 26},
}
SPECS = {"host_ram_gib": ("RAM", "GiB", 1.0), "gpu_memory_gib": ("GPU memory", "GiB", 1.0),
         "vcpus": ("vCPUs", "", 0)}  # name, unit, tolerance


def host_summaries(stats, status):
    """Display keys computed across the loaded host files (never typed in): listing comparison,
    CUDA runtime/driver summary and output agreement relative to the H100."""
    out = listing_comparison(stats, status)
    out.update(cuda_summary(stats, status))
    out.update(agreement_comparison(stats, status))
    out.update(prose_summaries(stats, status))
    return out


def _suite_phrase(listed, suite):
    every = _join([_scale_label(sc) for sc in SCALES[suite]])
    return "at every scale" if listed == every else ("at no scale" if listed == "none" else f"at {listed}")


def _both_suites(keys):
    """<prefix>.both.{above,below}_text for every family with summaries of both suites: "at every
    scale in both suites" when that holds, otherwise one phrase per suite. Also
    <prefix>.<suite>.summary.below_c1 phrases for E1 via the same rule."""
    out = {}
    prefixes = {k[: -len(".baseline.summary.above")] for k in keys if k.endswith(".baseline.summary.above")}
    for prefix in prefixes:
        for kind in ("above", "below", "below_c1"):
            b, c = keys.get(f"{prefix}.baseline.summary.{kind}"), keys.get(f"{prefix}.complex.summary.{kind}")
            if b is None or c is None:
                continue
            pb, pc = _suite_phrase(b, "baseline"), _suite_phrase(c, "complex")
            if pb == pc and pb in ("at every scale", "at no scale"):
                text = f"{pb} in both suites" if pb == "at every scale" else "at no scale in either suite"
            else:
                text = f"{pb} on the baseline suite and {pc} on the complex suite"
            out[f"{prefix}.both.{kind}_text"] = text
    return out


def _family(stats, prefix):
    """<prefix>.<suite>.summary.{min,max,above,below,includes,trend} over the suite's scales: the
    range of estimates, the scales whose 95% CI lies entirely above 1, entirely below 1, or
    includes 1 ("none" if empty), and how the estimate moves with scale."""
    out = {}
    for suite, scales in SCALES.items():
        cells = [(sc, stats.get(f"{prefix}.{suite}.{sc}.estimate"), stats.get(f"{prefix}.{suite}.{sc}.ci_low"),
                  stats.get(f"{prefix}.{suite}.{sc}.ci_high")) for sc in scales]
        cells = [c for c in cells if _number(c[1])]
        if not cells:
            continue
        key = f"{prefix}.{suite}.summary"
        label = lambda group: _join([_scale_label(c[0]) for c in group]) if group else "none"
        with_ci = [c for c in cells if _number(c[2]) and _number(c[3])]
        out[key + ".min"] = float(min(c[1] for c in cells))
        out[key + ".max"] = float(max(c[1] for c in cells))
        out[key + ".above"] = label([c for c in with_ci if c[2] > 1])
        out[key + ".below"] = label([c for c in with_ci if c[3] < 1])
        out[key + ".includes"] = label([c for c in with_ci if c[2] <= 1 <= c[3]])
        groups = [(word, [c for c in with_ci if test(c)]) for word, test in (
            ("below 1 at", lambda c: c[3] < 1), ("including 1 at", lambda c: c[2] <= 1 <= c[3]),
            ("above 1 at", lambda c: c[2] > 1))]
        above = [c[0] for c in with_ci if c[2] > 1]
        scale_list = [c[0] for c in cells]
        if above and above == scale_list[len(scale_list) - len(above):]:
            out[key + ".above_phrase"] = ("at every scale" if len(above) == len(scale_list)
                                          else f"from {_scale_label(above[0])}")
        else:
            out[key + ".above_phrase"] = f"at {label([c for c in with_ci if c[2] > 1])}"
        out[key + ".pattern"] = _join([f"{word} {label(group)}" for word, group in groups if group]) or "no interval"
        est = [c[1] for c in cells]
        if len(est) > 1 and all(b > a for a, b in zip(est, est[1:])):
            trend = f"rose at every step from {_scale_label(cells[0][0])} to {_scale_label(cells[-1][0])}"
        elif len(est) > 1 and all(b < a for a, b in zip(est, est[1:])):
            trend = f"fell at every step from {_scale_label(cells[0][0])} to {_scale_label(cells[-1][0])}"
        else:
            top = max(cells, key=lambda c: c[1])
            trend = f"did not move in one direction with scale (largest at {_scale_label(top[0])})"
        out[key + ".trend"] = trend
    return out


def prose_summaries(stats, status):
    """Summary keys for the results prose, all computed from loaded keys: _family summaries for the
    contrasts on every host that ran, the H100 bridge and E1/E4, and each host's cross-host ratios;
    ladder-wide C3 range, integrity and coverage totals; scales where E1 is below C1."""
    out = {}
    hosts = _ran_hosts(status)
    for host in hosts:
        for c in ("c1", "c2", "c3", "c4"):
            out.update(_family(stats, f"{_ns(host)}{c}"))
        if host != "h100":
            for engine in ENGINES:
                out.update(_family(stats, f"xhost.{host}.{engine}"))
                values = [stats.get(f"xhost.{host}.{engine}.{su}.{sc}.estimate") for su, scs in SCALES.items() for sc in scs]
                values = [v for v in values if _number(v)]
                if values:
                    out[f"xhost.{host}.{engine}.overall.min"] = float(min(values))
                    out[f"xhost.{host}.{engine}.overall.max"] = float(max(values))
    for prefix in ("bridge", "exploratory.e1", "exploratory.e4"):
        out.update(_family(stats, prefix))
    for host in hosts:  # <host>.<c>.<suite>.sf300.vs_h100: interval comparison with the H100's
        if host == "h100":
            continue
        for c in ("c1", "c2", "c3", "c4"):
            for suite in SCALES:
                h_lo, h_hi = (stats.get(f"{host}.{c}.{suite}.sf300.{f}") for f in ("ci_low", "ci_high"))
                r_lo, r_hi = (stats.get(f"{c}.{suite}.sf300.{f}") for f in ("ci_low", "ci_high"))
                if all(_number(v) for v in (h_lo, h_hi, r_lo, r_hi)):
                    out[f"{host}.{c}.{suite}.sf300.vs_h100"] = (
                        "smaller than on the H100 (the intervals do not overlap)" if h_hi < r_lo else
                        "larger than on the H100 (the intervals do not overlap)" if h_lo > r_hi else
                        "not distinguishable from the H100's (the intervals overlap)")
    for host in hosts:  # scales shared by both suites where the complex estimate is the larger
        for c in ("c1", "c2", "c4"):
            shared = [sc for sc in SCALES["complex"] if sc in SCALES["baseline"]]
            pairs = [(sc, stats.get(f"{_ns(host)}{c}.complex.{sc}.estimate"), stats.get(f"{_ns(host)}{c}.baseline.{sc}.estimate"))
                     for sc in shared]
            pairs = [x for x in pairs if _number(x[1]) and _number(x[2])]
            if pairs:
                larger = [_scale_label(sc) for sc, cx, base in pairs if cx > base]
                out[f"{_ns(host)}{c}.complex_larger_scales"] = _join(larger) if larger else "none"
    for suite, scales in SCALES.items():
        below = [sc for sc in scales if _number(stats.get(f"exploratory.e1.{suite}.{sc}.estimate"))
                 and _number(stats.get(f"c1.{suite}.{sc}.estimate"))
                 and stats[f"exploratory.e1.{suite}.{sc}.estimate"] < stats[f"c1.{suite}.{sc}.estimate"]]
        if any(_number(stats.get(f"exploratory.e1.{suite}.{sc}.estimate")) for sc in scales):
            out[f"exploratory.e1.{suite}.summary.below_c1"] = _join([_scale_label(sc) for sc in below]) if below else "none"
    c3 = [(stats.get(f"{_ns(h)}c3.{su}.{sc}.estimate"), stats.get(f"{_ns(h)}c3.{su}.{sc}.ci_low"))
          for h in hosts for su, scs in SCALES.items() for sc in scs]
    c3 = [(e, l) for e, l in c3 if _number(e)]
    if c3:
        out["ladder.c3.min"] = float(min(e for e, _ in c3))
        out["ladder.c3.max"] = float(max(e for e, _ in c3))
        out["ladder.c3.cells"] = str(len(c3))
        out["ladder.c3.cells_above"] = str(sum(1 for _, l in c3 if _number(l) and l > 1))
    compared = [stats.get(f"{_ns(h)}meta.outputs_compared") for h in hosts]
    failed = [stats.get(f"{_ns(h)}meta.outputs_failed") for h in hosts]
    if all(_number(v) for v in compared + failed):
        out["ladder.outputs_compared_total"] = f"{int(sum(compared)):,}"
        out["ladder.outputs_failed_total"] = f"{int(sum(failed)):,}"
        out["ladder.outputs_compared_list"] = _join([f"{int(v):,}" for v in compared])
    out["ladder.hosts_count"] = str(len(hosts))
    out.update(_both_suites(out))
    for host in hosts:  # <host>.crossover.c4.text: one phrase when both suites cross over at the same scale
        b = stats.get(f"{_ns(host)}crossover.baseline.c4.scale")
        c = stats.get(f"{_ns(host)}crossover.complex.c4.scale")
        if b is not None and c is not None:
            out[f"{_ns(host)}crossover.c4.text"] = (f"{b} on both suites" if b == c
                                                     else f"{b} (baseline) and {c} (complex)")
    for host in hosts:  # C1 and C2: scales shared by both suites where the complex estimate is larger
        l1, l2 = out.get(f"{_ns(host)}c1.complex_larger_scales"), out.get(f"{_ns(host)}c2.complex_larger_scales")
        if l1 is not None and l2 is not None:
            shared = _join([_scale_label(sc) for sc in SCALES["complex"] if sc in SCALES["baseline"]])
            if l1 == l2 == shared:
                out[f"{_ns(host)}c12.complex_larger_text"] = (
                    f"both C1 and C2 were larger on the complex suite at every scale the two suites share ({shared})")
            else:
                out[f"{_ns(host)}c12.complex_larger_text"] = (
                    f"C1 was larger on the complex suite at {l1} and C2 at {l2}")
    # ladder.<c>.<suite>.<kind>_text: one phrase when every host has the same scale list, else per host
    for c in ("c1", "c2", "c3", "c4"):
        for suite, scs in SCALES.items():
            for kind in ("above", "below", "includes"):
                lists = [(h, stats.get(f"{_ns(h)}{c}.{suite}.summary.{kind}", out.get(f"{_ns(h)}{c}.{suite}.summary.{kind}")))
                         for h in hosts]
                if not lists or any(v is None for _, v in lists):
                    continue
                values = {v for _, v in lists}
                if len(values) == 1:
                    v = values.pop()
                    every = _join([_scale_label(sc) for sc in scs])
                    phrase = (f"at every {suite} scale" if v == every else f"at {v}" if v != "none" else "at no scale")
                    out[f"ladder.{c}.{suite}.{kind}_text"] = f"{phrase} on every host"
                else:
                    out[f"ladder.{c}.{suite}.{kind}_text"] = "at " + _join(
                        [f"{v} on the {HOST_LABELS[h]}" for h, v in lists])
    # telemetry ranges over hosts and suites at SF300 (gpu_tel.ladder.<engine>.sf300.<field>.{min,max})
    for e in GPU_ENGINES:
        for f in ("util", "idle", "power", "memutil"):
            vals = [(stats.get(f"{_ns(h)}gpu_tel.{e}.{su}.sf300.{f}"), h, su) for h in hosts for su in SCALES]
            vals = [v for v in vals if _number(v[0])]
            if vals:
                lo, hi = min(vals), max(vals)
                out[f"gpu_tel.ladder.{e}.sf300.{f}.min"] = float(lo[0])
                out[f"gpu_tel.ladder.{e}.sf300.{f}.max"] = float(hi[0])
                out[f"gpu_tel.ladder.{e}.sf300.{f}.max_where"] = f"{HOST_LABELS[hi[1]]}, {hi[2]} suite"
        populated = [sc for h in hosts for su, scs in SCALES.items() for sc in scs
                     if _number(stats.get(f"{_ns(h)}gpu_tel.{e}.{su}.{sc}.util"))]
        if populated:
            out[f"gpu_tel.ladder.{e}.smallest_scale"] = _scale_label(min(populated, key=_scale_order))
    clocks = [stats.get(f"{_ns(h)}secondary.gpu_telemetry.cells.{su}.{sc}.{e}.sm_clock_fraction_of_max")
              for h in hosts for su, scs in SCALES.items() for sc in scs for e in GPU_ENGINES]
    clocks = [v for v in clocks if _number(v)]
    if clocks:
        out["ladder.gpu_tel.cells"] = str(len(clocks))
        out["ladder.gpu_tel.clock_at_max"] = str(sum(1 for v in clocks if v >= 0.99))
    later = []  # GPU engines' cross-host ratios: per host, and over the hosts after the H100
    for host in hosts:
        if host == "h100":
            continue
        values = [v for e in GPU_ENGINES for su, scs in SCALES.items() for sc in scs
                  for v in [stats.get(f"xhost.{host}.{e}.{su}.{sc}.estimate")] if _number(v)]
        if values:
            out[f"ladder.xhost_gpu.{host}.min"] = float(min(values))
            out[f"ladder.xhost_gpu.{host}.max"] = float(max(values))
            if LADDER.index(host) > LADDER.index("h100"):
                later += values
    every = [out[f"ladder.xhost_gpu.{h}.{f}"] for h in hosts if h != "h100" for f in ("min", "max")
             if f"ladder.xhost_gpu.{h}.{f}" in out]
    if every:
        out["ladder.xhost_gpu.all.min"] = float(min(every))
        out["ladder.xhost_gpu.all.max"] = float(max(every))
    if later:
        out["ladder.xhost_gpu.later.hosts"] = _join([HOST_LABELS[h] for h in hosts
                                                     if h != "h100" and LADDER.index(h) > LADDER.index("h100")])
        out["ladder.xhost_gpu.later.min"] = float(min(later))
        out["ladder.xhost_gpu.later.max"] = float(max(later))
    for host in hosts:  # Polars GPU wins among the cells it could run
        for suite in SCALES:
            won = stats.get(f"{_ns(host)}fastest.polars_gpu.{suite}.count")
            ran = stats.get(f"{_ns(host)}coverage.polars_gpu.{suite}.supported")
            if _number(won) and _number(ran):
                out[f"{_ns(host)}fastest.polars_gpu.{suite}.of_supported"] = (
                    f"all {int(ran)}" if won == ran else f"{int(won)} of the {int(ran)}")
    exhausted = [v for h in hosts for e in GPU_ENGINES for su in SCALES
                 for v in [stats.get(f"{_ns(h)}coverage.{e}.{su}.resource_exhaustion")] if _number(v)]
    if exhausted:
        out["ladder.resource_exhaustion_total"] = str(int(sum(exhausted)))
    unexpected = [stats.get(f"{_ns(h)}coverage.polars_gpu.unexpected_failures") for h in hosts]
    if all(_number(v) for v in unexpected):
        out["ladder.unexpected_failures_total"] = str(int(sum(unexpected)))
    return out


def _ran_hosts(status):
    return [h for h in LADDER if h == "h100" or status.get(h) is True]


def _ns(host):
    return "" if host == "h100" else f"{host}."


def _version(text):
    try:
        return tuple(int(x) for x in str(text).split(".")[:2])
    except ValueError:
        return None


def cuda_summary(stats, status):
    """hosts.cuda_runtime: the CUDA runtime version (one value if every host that ran agrees, else
    per host); hosts.minor_compat_list: hosts whose driver supports an older CUDA minor version than
    the runtime (host.cuda_version < host.cuda_runtime_version, same major), i.e. running through
    CUDA minor-version compatibility, or "none"."""
    out, runtimes, compat = {}, {}, []
    for host in _ran_hosts(status):
        runtime, driver = stats.get(f"{_ns(host)}host.cuda_runtime_version"), stats.get(f"{_ns(host)}host.cuda_version")
        if runtime is None or driver is None:
            return {}
        runtimes[host] = str(runtime)
        rv, dv = _version(runtime), _version(driver)
        if rv and dv and dv[0] == rv[0] and dv < rv:
            compat.append(HOST_LABELS[host])
    if runtimes:
        values = set(runtimes.values())
        out["hosts.cuda_runtime"] = (values.pop() if len(values) == 1 else
                                     "; ".join(f"{HOST_LABELS[h]} {v}" for h, v in runtimes.items()))
        out["hosts.minor_compat_list"] = _join(compat) if compat else "none"
    return out


def agreement_comparison(stats, status, engines=GPU_ENGINES):
    """xagree.<host>.<engine>.{cells,cells_larger,max_ratio}: over suite-scale cells present on
    both hosts, how many had a larger maximum relative difference from polars than on the H100,
    and the ratio of the two hosts' overall maxima."""
    out = {}
    for host in _ran_hosts(status):
        if host == "h100":
            continue
        for engine in engines:
            pairs = []
            for key, value in stats.items():
                parts = key.split(".")
                if parts[:2] == [host, "output_agreement"] and parts[4:] == [engine, "max_rel_diff"]:
                    ref = stats.get(f"output_agreement.{parts[2]}.{parts[3]}.{engine}.max_rel_diff")
                    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (value, ref)):
                        pairs.append((value, ref))
            if not pairs:
                continue
            out[f"xagree.{host}.{engine}.cells"] = str(len(pairs))
            out[f"xagree.{host}.{engine}.cells_larger"] = str(sum(1 for v, r in pairs if v > r))
            top_ref = max(r for _, r in pairs)
            if top_ref > 0:
                out[f"xagree.{host}.{engine}.max_ratio"] = f"{max(v for v, _ in pairs) / top_ref:.2g}"
    return out


def listing_comparison(stats, status):
    """hosts.<host>.<spec>.matches_listing ("yes"/"no") for every host that ran and reports the
    spec (measured in its host block), and hosts.listing_note, one sentence for the hosts table:
    the specs that differ from the listing by more than the tolerance, or that all match."""
    out, differ, compared = {}, [], 0
    for host in LADDER:
        if host != "h100" and status.get(host) is not True:
            continue
        ns = "" if host == "h100" else f"{host}."
        for spec, (name, unit, tol) in SPECS.items():
            measured = stats.get(f"{ns}host.{spec}")
            if not isinstance(measured, (int, float)) or isinstance(measured, bool):
                continue
            compared += 1
            listed = LISTING[host][spec]
            matches = abs(measured - listed) <= tol
            out[f"hosts.{host}.{spec}.matches_listing"] = "yes" if matches else "no"
            if not matches:
                fmt = "{:.1f}" if unit else "{:g}"
                tail = f" {unit}" if unit else ""
                differ.append(f"{HOST_LABELS[host]} {name} {fmt.format(measured)}{tail} "
                              f"(listed {listed:g}{tail})")
    if compared:
        out["hosts.listing_note"] = (
            "Measured values that differ from the provider's listing quoted in amendments A2 and A3: "
            + "; ".join(differ) + "." if differ else
            "All measured values match the provider's listing quoted in amendments A2 and A3 "
            "(within 1 GiB for RAM and GPU memory; vCPUs exactly).")
    return out


def _join(labels):
    """"a", "a and b", "a, b and c"; "" for an empty list."""
    if not labels:
        return ""
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def status_display(status, not_run):
    """Display keys describing the ladder: hosts.<host>.status ("ran" / "not run"),
    <host>.not_run_reason, and, once every host is decided, hosts.ran_list, hosts.other_ran_list,
    hosts.not_run_list and hosts.n_ran (H100 included)."""
    out = {}
    for host in HOSTS:
        if status[host] is True:
            out[f"hosts.{host}.status"] = "ran"
        elif status[host] is False:
            out[f"hosts.{host}.status"] = "not run"
            out[f"{host}.not_run_reason"] = not_run[host]
    if all(v is not None for v in status.values()):
        ran = [h for h in LADDER if h == "h100" or status[h]]
        others = [HOST_LABELS[h] for h in ran if h != "h100"]
        skipped = [HOST_LABELS[h] for h in LADDER if h != "h100" and not status[h]]
        out["hosts.ran_list"] = _join([HOST_LABELS[h] for h in ran])
        out["hosts.other_ran_list"] = _join(others) if others else "none"
        out["hosts.not_run_list"] = _join(skipped) if skipped else "none"
        out["hosts.n_ran"] = str(len(ran))
    return out


def parse_not_run(items):
    out = {}
    for item in items:
        host, sep, reason = item.partition("=")
        if not sep or not reason.strip():
            raise StatsError(f"--not-run {item!r}: expected HOST=REASON")
        out[host.strip()] = reason.strip()
    return out


def load_many(specs):
    """Load "[PREFIX=]PATH" specs. The first spec must be unprefixed (the primary H100 campaign);
    the others are namespaced under "PREFIX." (a100=, gh200=, b200=), or "xhost.<host>." for
    xhost_<host>=PATH."""
    stats, display, formats, sources, provenance = {}, {}, {}, [], []
    for index, spec in enumerate(specs):
        prefix, sep, path = str(spec).partition("=")
        if not sep:
            prefix, path = "", prefix
        if index == 0 and prefix:
            raise StatsError("the first --stats must be the primary (H100) file, without a prefix")
        if index > 0 and not prefix:
            raise StatsError(f"--stats {spec}: additional files need a PREFIX= (e.g. b200=PATH)")
        if prefix and prefix not in PREFIXES:
            raise StatsError(f"--stats {spec}: prefix must be one of {', '.join(PREFIXES)}")
        if prefix in {name for name, _ in sources}:
            raise StatsError(f"--stats {spec}: prefix {prefix!r} given twice")
        one, one_display, one_formats, synthetic = load_stats(path)
        ns = f"xhost.{XHOST[prefix]}." if prefix in XHOST else (f"{prefix}." if prefix else "")
        for key, value in one.items():
            if ns and key.split(".")[0] in (prefix, ns.split(".")[0]):
                raise StatsError(f"{path}: key {key} already starts with the prefix {prefix!r}")
            stats[ns + key] = value
        display.update({ns + k: v for k, v in one_display.items()})
        formats.update({ns + k: v for k, v in one_formats.items()})
        sources.append((prefix or "primary", Path(path)))
        provenance.append({"name": prefix or "primary", "synthetic": synthetic, "display": list(one_display)})
    return stats, display, formats, sources, provenance


def md_substitute(text, replace):
    """Apply MD_FIELD substitution outside HTML comments only."""
    parts, last = [], 0
    for comment in MD_COMMENT.finditer(text):
        parts.append(MD_FIELD.sub(replace, text[last:comment.start()]))
        parts.append(comment.group(0))
        last = comment.end()
    parts.append(MD_FIELD.sub(replace, text[last:]))
    return "".join(parts)


def missing_keys(required, stats, display):
    return [k for k in required if k not in stats and k not in display]

"""Descriptive summaries of design 5.2 ("Also: within-session and between-round CV; order and carryover by position and predecessor").

No tests, no intervals that claim more than description: the coefficient of variation of the three trial windows inside a session, the
coefficient of variation of a cell's per-round medians across rounds, and the cell value by the engine's position in the round and by the
engine that ran just before it, each normalised to the engine's own geometric mean over rounds (so a value above 1 means "slower or more
energy than that engine's average"). Thermal carryover is likely for energy; the Latin square and the cool-down limit it, they do not remove it (5.2).
"""
from __future__ import annotations

import math
import statistics

from .constants import S1_TAG, S2_PINNED
from .estimation import geomean, ratio_record
from .tree import scale_key, scale_sort

POSITION_NONE = "none"                       # predecessor label of the first session of a (suite, scale) block (Paper 1)


def cv(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return statistics.stdev(values) / mean if mean else None


def median_or_none(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def series_of(hd, engine) -> str:
    """The primary energy series of a host's engine: S2-pinned on the CPU-only host, the S1 counter on a GPU host."""
    return S2_PINNED if hd.kind == "cpu-metal" else S1_TAG.get(hd.scope)


def cell_values(cell, metric, series):
    if metric == "time":
        return cell.time_s, list(cell.trial_times)
    entry = cell.energy.get(series)
    return (entry.total_j, list(entry.trial_j)) if entry is not None and entry.total_j is not None else (None, [])


def cv_summary(tree, store, selection) -> dict:
    """{host: {engine: {suite: {scale: {metric: {within_session_cv, between_round_cv}}}}}}: medians over queries of the two CVs."""
    out = {}
    for host, hd in sorted(tree.hosts.items()):
        rounds = selection.subject.get(host, {})
        instance = hd.reference_instance
        for engine in instance.engines:
            series = series_of(hd, engine)
            for suite in sorted(instance.suites):
                for scale in scale_sort(instance.suites[suite]):
                    for metric in ("time", "energy_total"):
                        per_query = {}
                        for r, label in rounds.items():
                            for q, by_engine in store.at(host, label, r, suite, scale).items():
                                cell = by_engine.get(engine)
                                if cell is None:
                                    continue
                                median, trials = cell_values(cell, metric, series)
                                if median is not None:
                                    per_query.setdefault(q, []).append((median, trials))
                        if not per_query:
                            continue
                        within = median_or_none([median_or_none([cv(trials) for _, trials in rows]) for rows in per_query.values()])
                        between = median_or_none([cv([m for m, _ in rows]) for rows in per_query.values()])
                        out.setdefault(host, {}).setdefault(engine, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})[metric] = {
                            "within_session_cv": within, "between_round_cv": between, "n_queries": len(per_query),
                            "n_rounds": max(len(rows) for rows in per_query.values())}
    return out


def order_summary(tree, store, selection) -> dict:
    """{host: {engine: {suite: {scale: {metric: {by_position: {p: record}, by_predecessor: {engine|none: record}}}}}}}.

    Each (query, round) value is divided by the engine's geometric mean of that query over the analysis's rounds; a round's value for the (suite,
    scale) is the geometric mean of those over the queries; the groups are the rounds in which the engine ran at position p (1-based) or just after
    a given engine. Descriptive: each record is the geometric mean with the same t-interval over its rounds (no interval for a single round)."""
    out = {}
    for host, hd in sorted(tree.hosts.items()):
        rounds = selection.subject.get(host, {})
        instance = hd.reference_instance
        schedule = {int(e["round"]): e for e in instance.paper3.get("schedule") or []}
        for engine in instance.engines:
            series = series_of(hd, engine)
            for suite in sorted(instance.suites):
                for scale in scale_sort(instance.suites[suite]):
                    for metric in ("time", "energy_total"):
                        values = {}
                        for r, label in rounds.items():
                            for q, by_engine in store.at(host, label, r, suite, scale).items():
                                cell = by_engine.get(engine)
                                value = cell_values(cell, metric, series)[0] if cell is not None else None
                                if value is not None and value > 0:
                                    values.setdefault(q, {})[r] = value
                        queries = [q for q, per in values.items() if len(per) == len(rounds)]
                        if not queries or len(rounds) < 2:
                            continue
                        mean = {q: geomean(list(values[q].values())) for q in queries}
                        per_round = {r: math.exp(statistics.fmean(math.log(values[q][r] / mean[q]) for q in queries)) for r in rounds}
                        by_position, by_predecessor = {}, {}
                        for r, value in per_round.items():
                            order = (schedule.get(r) or {}).get("engine_order") or []
                            if engine not in order:
                                continue
                            index = order.index(engine)
                            by_position.setdefault(str(index + 1), {})[r] = value
                            by_predecessor.setdefault(order[index - 1] if index else POSITION_NONE, {})[r] = value
                        out.setdefault(host, {}).setdefault(engine, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})[metric] = {
                            "by_position": {k: ratio_record(v) for k, v in sorted(by_position.items())},
                            "by_predecessor": {k: ratio_record(v) for k, v in sorted(by_predecessor.items())},
                            "n_queries": len(queries), "note": "descriptive; value / the engine's own geometric mean over rounds"}
    return out

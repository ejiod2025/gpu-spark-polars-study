#!/usr/bin/env python3
"""Convert the four host campaigns' stats.json files (and the cross-host files) into
paper2/generated/stats.tex for Paper 2.

Paper 2 is a secondary analysis of the pre-registered study whose primary report is
Paper 1 (paper/). It reuses Paper 1's loader, derivations and formatting
(paper/tools/statskeys.py, imported unchanged) and adds:

  * condition flags for parts of the paper that depend on which inputs exist:
      bridge_<host>  the host's RQ5 side study (Polars 1.44.2 vs 2.0.0) and the
                     exploratory E1/E4 blocks are present (host: a100, h100, gh200, b200)
      bridge_extra   the A100 or the B200 bridge is present
      pcheck         protocol_check.* keys are present (--extra file)
      decomp         decomp.* keys are present (--extra file)
      e1adj          exploratory_adj.e1.* keys (session-adjusted E1 with intervals) are present
    Source regions:  % BEGIN-IF:<cond> ... % END-IF:<cond>  (and BEGIN-IFNOT / END-IFNOT)
    TeX flags:       \\if<flag> (FLAG below), set false here when the condition is false
  * derived display keys under "p2." (and Paper 1-style families for every host), computed
    only from loaded keys, never typed in;
  * claim checks: statements the prose makes in words (for example "below 1 at every
    scale on every host with a bridge") are checked against the loaded keys; a failed
    claim stops the build so the prose cannot drift from the data. Claim ids are cited in
    main.tex as the invisible \\claim{<id>}; an id with no check is an error.

All four host campaigns and the three cross-host files are required.

Usage:
  python3 paper2/tools/stats_to_tex.py --stats H100/stats.json --stats a100=A100/stats.json \\
      --stats gh200=GH200/stats.json --stats b200=B200/stats.json \\
      --stats xhost_a100=X/xhost_A100.json --stats xhost_gh200=X/xhost_GH200.json \\
      --stats xhost_b200=X/xhost_B200.json [--extra P2/paper2.json]
  python3 paper2/tools/stats_to_tex.py --list-keys [--case bridge_a100=1,decomp=0,...]
"""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

PAPER2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PAPER2.parent / "paper" / "tools"))
import statskeys  # noqa: E402  (Paper 1's module, used unchanged)
from statskeys import StatsError, _family, _join, _number, _scale_label  # noqa: E402

LADDER = statskeys.LADDER                     # a100, h100, gh200, b200
LABEL = statskeys.HOST_LABELS
SCALES = statskeys.SCALES
OTHERS = ("a100", "gh200", "b200")
PAIRS = (("c1", "polars", "polars_gpu"), ("c2", "spark_cpu", "spark_gpu"))
CONDS = ["bridge_a100", "bridge_h100", "bridge_gh200", "bridge_b200", "bridge_extra", "pcheck", "decomp", "e1adj"]
# TeX \newif names (letters only), defined in macros.tex
FLAG = {"bridge_a100": "bridgeaonehundred", "bridge_h100": "bridgehonehundred",
        "bridge_gh200": "bridgeghtwohundred", "bridge_b200": "bridgebtwohundred",
        "bridge_extra": "bridgeextra", "pcheck": "pcheck", "decomp": "decomp", "e1adj": "eadj"}
# formats for the extra file's ratios (Paper 1's rules do not know these prefixes)
EXTRA_FORMATS = {f"{p}.*.{f}": "{:.2f}" for p in ("protocol_check", "decomp", "exploratory_adj")
                 for f in ("estimate", "ci_low", "ci_high")}

REGION = re.compile(r"^[ \t]*%[ \t]*BEGIN-(IF|IFNOT):([a-z0-9_]+)\b[^\n]*\n(.*?)^[ \t]*%[ \t]*END-\1:\2\b[^\n]*\n?",
                    re.S | re.M)
MARKER = re.compile(r"^[ \t]*%[ \t]*(BEGIN|END)-(IF|IFNOT):([a-z0-9_]+)\b", re.M)
# derived keys whose text is LaTeX markup (built here from numbers only), written unescaped
MARKUP_KEYS = {"p2.reversal.detail", "p2.bridge.exception_short", "p2.e1.below_phrase_ref", "p2.e4.below_phrase_ref", "p2.k1cmp.bridge_phrase_ref", "p2.pcheck.by_host", "p2.k1cmp.inconclusive_all", "p2.gap.by_host", "p2.e1adj_gap.by_host", "p2.bridge.below_phrase",
               "p2.e1.below_phrase", "p2.e4.below_phrase", "p2.k1cmp.bridge_phrase"}
TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(text):
    return "".join(TEX_SPECIALS.get(ch, ch) for ch in text)


def ns(host):
    return "" if host == "h100" else f"{host}."


# ---------------------------------------------------------------- key scanning

def resolve(text, case):
    """Drop regions not typeset in this case; undecided conditions (None) keep both branches."""
    def keep(match):
        kind, cond, body = match.groups()
        if cond not in CONDS:
            raise StatsError(f"unknown condition {cond!r} in a region marker (expected one of {CONDS})")
        state = case.get(cond)
        if state is not None and (kind == "IF") != state:
            return ""
        return resolve(body, case)
    return REGION.sub(keep, text)


def required_keys(paths, case):
    keys = set()
    for path in paths:
        raw = Path(path).read_text()
        left = MARKER.search(resolve(raw, {}))
        if left:
            raise StatsError(f"{path}: unmatched region marker {left.group(0).strip()!r}")
        text = statskeys._strip_tex_comments(resolve(raw, case))
        keys.update(statskeys.TEX_STAT.findall(text))
        for prefix in statskeys.TEX_RATIO.findall(text):
            keys.update(f"{prefix}.{part}" for part in statskeys.RATIO_PARTS)
    return sorted(k for k in keys if "#" not in k)


TEX_CLAIM = re.compile(r"\\claim\{([a-z0-9-]+)\}")


def cited_claims(paths, case):
    ids = set()
    for path in paths:
        ids.update(TEX_CLAIM.findall(statskeys._strip_tex_comments(resolve(Path(path).read_text(), case))))
    return ids


def parse_case(text):
    case = {c: None for c in CONDS}
    for item in filter(None, (text or "").split(",")):
        cond, _, value = item.partition("=")
        if cond not in case or value not in ("0", "1"):
            raise StatsError(f"--case {item!r}: expected COND=0|1, COND in {', '.join(CONDS)}")
        case[cond] = value == "1"
    return case


# ---------------------------------------------------------------- conditions and derived keys

def conditions(stats):
    case = {}
    for host in LADDER:
        p = ns(host)
        case[f"bridge_{host}"] = bool(stats.get(p + "side_study.meta.available") is True
                                      and stats.get(p + "exploratory.e1.meta.available") is True
                                      and _number(stats.get(p + "bridge.baseline.sf300.estimate")))
    case["bridge_extra"] = case["bridge_a100"] or case["bridge_b200"]
    case["pcheck"] = any(k.startswith("protocol_check.") and k.endswith(".estimate") for k in stats)
    case["decomp"] = any(k.startswith("decomp.") and k.endswith(".estimate") for k in stats)
    case["e1adj"] = any(k.startswith("exploratory_adj.e1.") and k.endswith(".estimate") and len(k.split(".")) == 6
                        for k in stats)
    return case


def _cells(stats, prefix, suite):
    """[(scale, estimate, ci_low|None, ci_high|None)] for the suite's scales that have an estimate."""
    out = []
    for sc in SCALES[suite]:
        e, lo, hi = (stats.get(f"{prefix}.{suite}.{sc}.{f}") for f in ("estimate", "ci_low", "ci_high"))
        if _number(e):
            out.append((sc, e, lo if _number(lo) else None, hi if _number(hi) else None))
    return out


def first_above(cells):
    """(first scale whose CI lies entirely above 1, whether every larger scale's CI does too)."""
    for i, (sc, _, lo, _) in enumerate(cells):
        if lo is not None and lo > 1:
            return sc, all(c[2] is not None and c[2] > 1 for c in cells[i:])
    return None, True


def derived(stats, case):
    """p2.* display keys (and families for the non-primary hosts), from loaded keys only."""
    out = {}
    bridged = [h for h in LADDER if case[f"bridge_{h}"]]
    out["p2.bridge.hosts_list"] = _join([LABEL[h] for h in bridged]) if bridged else "none"
    out["p2.bridge.n_hosts"] = str(len(bridged))
    for host in LADDER:
        p = ns(host)
        if host != "h100":  # Paper 1 derives these families for the H100 only
            for prefix in ("bridge", "exploratory.e1", "exploratory.e4"):
                for key, value in _family(stats, p + prefix).items():
                    out.setdefault(key, value)
        threads, lo, hi, smt = (stats.get(p + "host." + k) for k in
                                ("cpus_arg", "cpus_arg_cores_min", "cpus_arg_cores_max", "smt_threads_per_core"))
        if all(_number(v) for v in (threads, lo, hi, smt)):
            cores = str(lo) if lo == hi else f"{lo}--{hi}"
            out[f"p2.{host}.cores_used"] = cores
            out[f"p2.{host}.layout"] = (f"{threads} threads on {cores} physical cores"
                                        + (f" ({smt} hardware threads per core)" if smt > 1 else
                                           " (one hardware thread per core)"))
        hyp = stats.get(p + "host.hypervisor")
        out[f"p2.{host}.hypervisor_text"] = str(hyp) if hyp else "none reported"
        # first scale whose CI lies entirely above 1 (GPU side faster), per contrast
        for name, prefix in (("c1", p + "c1"), ("c4", p + "c4"), ("e1", p + "exploratory.e1"),
                             ("e4", p + "exploratory.e4")):
            for suite in SCALES:
                cells = _cells(stats, prefix, suite)
                if cells:
                    sc, sustained = first_above(cells)
                    out[f"p2.{host}.{name}.{suite}.first_above"] = _scale_label(sc)
                    out[f"p2.{host}.{name}.{suite}.sustained"] = "yes" if sustained else "no"
        if not case[f"bridge_{host}"]:
            continue
        # share of the speedup lost against the newer CPU release: 100 (1 - E1/C1), point estimates;
        # scales where C1's CI lies above 1 while E1's lies below 1 ("reversal")
        for suite in SCALES:
            gaps, e_below, reversal = [], [], []
            for sc in SCALES[suite]:
                c1 = [stats.get(f"{p}c1.{suite}.{sc}.{f}") for f in ("estimate", "ci_low", "ci_high")]
                e1 = [stats.get(f"{p}exploratory.e1.{suite}.{sc}.{f}") for f in ("estimate", "ci_low", "ci_high")]
                if not (all(_number(v) for v in c1) and all(_number(v) for v in e1)):
                    continue
                gap = 100.0 * (1.0 - e1[0] / c1[0])
                gaps.append(gap)
                out[f"p2.{host}.gap.{suite}.{sc}"] = f"{gap:.0f}"
                if e1[0] < c1[0]:
                    e_below.append(sc)
                if c1[1] > 1 and e1[2] < 1:
                    reversal.append(sc)
                    out.setdefault("_reversal_cells", []).append((host, suite, sc))
            if gaps:
                out[f"p2.{host}.gap.{suite}.min"] = f"{min(gaps):.0f}"
                out[f"p2.{host}.gap.{suite}.max"] = f"{max(gaps):.0f}"
                out[f"p2.{host}.e1_below_c1.{suite}"] = _join([_scale_label(s) for s in e_below]) if e_below else "none"
                out[f"p2.{host}.reversal.{suite}"] = _join([_scale_label(s) for s in reversal]) if reversal else "none"
    # bridge ranges over every host with a bridge (suite estimates; cx_window per query)
    for suite in SCALES:
        vals = [stats.get(f"{ns(h)}bridge.{suite}.{sc}.estimate") for h in bridged for sc in SCALES[suite]]
        vals = [v for v in vals if _number(v)]
        if vals:
            out[f"p2.bridge.{suite}.min"] = f"{min(vals):.2f}"
            out[f"p2.bridge.{suite}.max"] = f"{max(vals):.2f}"
    cxw = [stats.get(f"{ns(h)}bridge.complex.{sc}.q.cx_window.estimate") for h in bridged for sc in SCALES["complex"]]
    cxw = [v for v in cxw if _number(v)]
    if cxw:
        out["p2.bridge.cx_window.min"] = f"{min(cxw):.2f}"
        out["p2.bridge.cx_window.max"] = f"{max(cxw):.2f}"
    # bridge over the queries in E1's suite estimate (those the GPU side ran): geometric mean of
    # the per-query bridge estimates, which equals the suite point estimate when every query is
    # timed in every round; point estimates only (no interval is formed here)
    gpuq = {}
    for h in bridged:
        for suite in SCALES:
            for sc in SCALES[suite]:
                used = stats.get(f"{ns(h)}exploratory.e1.{suite}.{sc}.queries_used")
                per = [stats.get(f"{ns(h)}bridge.{suite}.{sc}.q.{q}.estimate") for q in used or []]
                if not used or not all(_number(v) for v in per):
                    continue
                value = math.exp(sum(math.log(v) for v in per) / len(per))
                out[f"p2.{h}.bridge_gpuq.{suite}.{sc}"] = f"{value:.2f}"
                out[f"p2.{h}.bridge_gpuq.{suite}.n_queries"] = str(len(per))
                gpuq.setdefault(suite, []).append(value)
    for suite, values in gpuq.items():
        out[f"p2.bridge_gpuq.{suite}.min"] = f"{min(values):.2f}"
        out[f"p2.bridge_gpuq.{suite}.max"] = f"{max(values):.2f}"
    gaps = [float(v) for k, v in out.items() if re.fullmatch(r"p2\.[a-z0-9]+\.gap\.[a-z]+\.sf\d+", k)]
    if gaps:
        out["p2.gap.max"] = f"{max(gaps):.0f}"
    out["p2.reversal.phrase"] = _join([f"on the {LABEL[h]} at {su} {sc_}" for h, su, sc_ in
                                       [(h, su, x) for h in bridged for su in SCALES
                                        for x in out.get(f"p2.{h}.reversal.{su}", "none").replace(" and ", ", ").split(", ")
                                        if x != "none"]]) or "nowhere"
    out["p2.reversal.phrase_cap"] = out["p2.reversal.phrase"][0].upper() + out["p2.reversal.phrase"][1:]
    rev = [f"the {LABEL[h]} at {su} {out[f'p2.{h}.reversal.{su}']}" for h in bridged for su in SCALES
           if out.get(f"p2.{h}.reversal.{su}", "none") != "none"]
    out["p2.reversal.list"] = _join(rev) if rev else "none"
    # cross-host speedup ratio C_host / C_H100 (point estimates). It equals the CPU engine's
    # cross-host time ratio divided by the GPU engine's whenever all three use the same queries
    # (log identity of geometric means); ".identity" records whether the query sets match.
    for h in OTHERS:
        for c, cpu, gpu in PAIRS:
            for suite in SCALES:
                for sc in SCALES[suite]:
                    ch, cr = stats.get(f"{h}.{c}.{suite}.{sc}.estimate"), stats.get(f"{c}.{suite}.{sc}.estimate")
                    if not (_number(ch) and _number(cr)):
                        continue
                    pre = f"p2.{h}.{c}.{suite}.{sc}"
                    out[pre + ".speedup_ratio"] = f"{ch / cr:.2f}"
                    n = {stats.get(f"{h}.{c}.{suite}.{sc}.n_queries"),
                         stats.get(f"xhost.{h}.{cpu}.{suite}.{sc}.n_queries"),
                         stats.get(f"xhost.{h}.{gpu}.{suite}.{sc}.n_queries")}
                    out[pre + ".identity"] = "yes" if len(n) == 1 else "no"
    # session-adjusted E1 (exploratory): E1 / K1 per cell, point estimates; K1 = protocol_check
    # (side-study Polars 1.44.2 / main-round Polars 1.44.2), so E1/K1 = bridge x C1 over E1's queries
    for h in bridged:
        k1 = [stats.get(f"protocol_check.{h}.{su}.{sc}.estimate") for su in SCALES for sc in SCALES[su]]
        k1 = [v for v in k1 if _number(v)]
        if not k1:
            continue
        out[f"p2.{h}.pcheck.min"] = f"{min(k1):.2f}"
        out[f"p2.{h}.pcheck.max"] = f"{max(k1):.2f}"
        out[f"p2.{h}.pcheck.max_pct"] = f"{100 * (max(k1) - 1):.0f}"
        excl = [f"{su} {_scale_label(sc)}" for su in SCALES for sc in SCALES[su]
                if _number(stats.get(f"protocol_check.{h}.{su}.{sc}.ci_low"))
                and (stats[f"protocol_check.{h}.{su}.{sc}.ci_low"] > 1 or stats[f"protocol_check.{h}.{su}.{sc}.ci_high"] < 1)]
        out[f"p2.{h}.pcheck.min_pct"] = f"{100 * (1 - min(k1)):.0f}"
        out[f"p2.{h}.pcheck.n_excluding_1"] = str(len(excl))
        out[f"p2.{h}.pcheck.excluding_1_list"] = _join(excl) if excl else "none"
        for suite in SCALES:
            cells, gaps = [], []
            for sc in SCALES[suite]:
                # K1 over E1's queries (the analysis' session_check) when given; it differs from
                # protocol_check on the complex suite, where protocol_check uses all six Polars queries
                k = stats.get(f"exploratory_adj.e1.{h}.{suite}.{sc}.session_check.estimate")
                if not _number(k):
                    k = stats.get(f"protocol_check.{h}.{suite}.{sc}.estimate")
                e1, c1 = (stats.get(f"{ns(h)}exploratory.e1.{suite}.{sc}.estimate"),
                          stats.get(f"{ns(h)}c1.{suite}.{sc}.estimate"))
                if not all(_number(v) for v in (e1, k, c1)):
                    continue
                adj = e1 / k
                out[f"p2.{h}.e1adj.{suite}.{sc}"] = f"{adj:.2f}"
                gap = 100.0 * (1.0 - adj / c1)
                out[f"p2.{h}.e1adj_gap.{suite}.{sc}"] = f"{gap:.0f}"
                cells.append((sc, adj)); gaps.append(gap)
            if gaps:
                out[f"p2.{h}.e1adj_gap.{suite}.min"] = f"{min(gaps):.0f}"
                out[f"p2.{h}.e1adj_gap.{suite}.max"] = f"{max(gaps):.0f}"
                first = next((sc for i, (sc, _) in enumerate(cells) if all(v > 1 for _, v in cells[i:])), None)
                out[f"p2.{h}.e1adj.{suite}.first_above_point"] = _scale_label(first)
        # interval version (K4, when delivered): first scale whose CI lies above 1
        for suite in SCALES:
            cells = _cells(stats, f"exploratory_adj.e1.{h}", suite)
            if cells:
                sc, sustained = first_above(cells)
                out[f"p2.{h}.e1adj_ci.{suite}.first_above"] = _scale_label(sc)
                out[f"p2.{h}.e1adj_ci.{suite}.sustained"] = "yes" if sustained else "no"
    # "where the verdict changes", generated per host with a bridge (exploratory). For each suite and pair
    # (C1->E1, C4->E4): the first scale whose CI lies above 1, unadjusted; then the session-adjusted
    # E1 (point estimates, and intervals when K4 is loaded) against E1.
    moves, same_hosts, adj_moves, adj_at_first, ci_moves = [], [], [], [], []
    for h in bridged:
        host_moves = []
        for suite in SCALES:
            for pre, exp in (("c1", "e1"), ("c4", "e4")):
                a_, b_ = out.get(f"p2.{h}.{pre}.{suite}.first_above"), out.get(f"p2.{h}.{exp}.{suite}.first_above")
                if a_ is None or b_ is None:
                    continue
                if a_ != b_:
                    host_moves.append(f"{LABEL[h]}, {suite} suite, {pre.upper()} {a_} to {exp.upper()} {b_}")
            e1f, adjf = out.get(f"p2.{h}.e1.{suite}.first_above"), out.get(f"p2.{h}.e1adj.{suite}.first_above_point")
            if e1f is not None and adjf is not None:
                if adjf != e1f:
                    adj_moves.append(f"{LABEL[h]}, {suite} suite, {e1f} to {adjf}")
                sc = e1f.lower()
                if f"p2.{h}.e1adj.{suite}.{sc}" in out:
                    adj_at_first.append(f"{LABEL[h]} {suite} {e1f} ({out[f'p2.{h}.e1adj.{suite}.{sc}']})")
            cif = out.get(f"p2.{h}.e1adj_ci.{suite}.first_above")
            if e1f is not None and cif is not None and cif != e1f:
                ci_moves.append(f"{LABEL[h]}, {suite} suite, {e1f} to {cif}")
        moves += host_moves
        if not host_moves:
            same_hosts.append(LABEL[h])
    out["p2.verdict.moves_list"] = "; ".join(moves) if moves else "none"
    out["p2.verdict.unchanged_hosts"] = _join(same_hosts) if same_hosts else "none"
    out["p2.verdict.n_moves"] = str(len(moves))
    out["p2.verdict_adj.moves_list"] = "; ".join(adj_moves) if adj_moves else "none"
    out["p2.verdict_adj.at_first_list"] = _join(adj_at_first) if adj_at_first else "none"
    if any(f"p2.{h}.e1adj_ci.{su}.first_above" in out for h in bridged for su in SCALES):
        out["p2.verdict_adjci.moves_list"] = "; ".join(ci_moves) if ci_moves else "none"
    # reversal cells with their two verdicts (values from the contrast keys)
    def rt(prefix):
        e, lo, hi = (stats.get(f"{prefix}.{f}") for f in ("estimate", "ci_low", "ci_high"))
        return f"{e:.2f}$\\times$ (95\\%~CI {lo:.2f}--{hi:.2f})"
    detail = []
    for h in bridged:
        for su in SCALES:
            for sc in SCALES[su]:
                c1p, e1p = f"{ns(h)}c1.{su}.{sc}", f"{ns(h)}exploratory.e1.{su}.{sc}"
                if all(_number(stats.get(f"{x}.{f}")) for x in (c1p, e1p) for f in ("estimate", "ci_low", "ci_high")) \
                        and stats[c1p + ".ci_low"] > 1 and stats[e1p + ".ci_high"] < 1:
                    detail.append(f"on the {LABEL[h]} at {su} {_scale_label(sc)}: C1 {rt(c1p)}, E1 {rt(e1p)}")
    out["p2.reversal.detail"] = "; ".join(detail) if detail else "none"
    # E1-vs-C1 shift against the session-check range (exploratory, descriptive): per cell, the
    # shift 1 - E1/C1 (point estimates) and the K1 interval's largest distance from 1; the shift is
    # distinguishable from the session effect only where it exceeds that distance.
    inconclusive_all = []
    for h in bridged:
        n_cells, n_exceed, incl = 0, 0, []
        for suite in SCALES:
            for sc in SCALES[suite]:
                e1, c1 = stats.get(f"{ns(h)}exploratory.e1.{suite}.{sc}.estimate"), stats.get(f"{ns(h)}c1.{suite}.{sc}.estimate")
                lo, hi = (stats.get(f"protocol_check.{h}.{suite}.{sc}.{f}") for f in ("ci_low", "ci_high"))
                if not all(_number(v) for v in (e1, c1, lo, hi)):
                    continue
                gap, limit = 1.0 - e1 / c1, max(abs(lo - 1.0), abs(hi - 1.0))
                exceeds = gap > limit
                pre = f"p2.{h}.k1cmp.{suite}.{sc}"
                out[pre + ".gap_value"], out[pre + ".limit_value"] = gap, limit  # unrounded, for the claim
                out[pre + ".gap_pct"], out[pre + ".limit_pct"] = f"{100 * gap:.1f}", f"{100 * limit:.1f}"
                out[pre + ".exceeds"] = "yes" if exceeds else "no"
                n_cells += 1
                n_exceed += exceeds
                if not exceeds:
                    sgn = f"$-${abs(100 * gap):.1f}" if gap < 0 else f"{100 * gap:.1f}"
                    incl.append(f"{suite} {_scale_label(sc)} (shift {sgn}\\%, K1 interval within "
                                f"{100 * limit:.1f}\\% of~1)")
        if n_cells:
            out[f"p2.{h}.k1cmp.n_cells"], out[f"p2.{h}.k1cmp.n_exceed"] = str(n_cells), str(n_exceed)
            out[f"p2.{h}.k1cmp.inconclusive_list"] = _join(incl) if incl else "none"
            inconclusive_all += [f"{LABEL[h]} {x}" for x in incl]
    if any(f"p2.{h}.k1cmp.n_cells" in out for h in bridged):
        out["p2.k1cmp.inconclusive_all"] = "; ".join(inconclusive_all) if inconclusive_all else "none"
        out["p2.k1cmp.summary"] = _join([f"{out[f'p2.{h}.k1cmp.n_exceed']} of {out[f'p2.{h}.k1cmp.n_cells']} "
                                          f"cells on the {LABEL[h]}" for h in bridged if f"p2.{h}.k1cmp.n_cells" in out])
        cells = [x.split(" (")[0] for x in inconclusive_all]
        out["p2.k1cmp.inconclusive_cells"] = _join(cells) if cells else "none"
    # K4 verdicts (gpu_faster / cpu_faster / inconclusive, from each interval against 1) before and
    # after the session adjustment, at suite level (E1 and E4) and per query (E1)
    suite_cells, suite_changed, q_cells, q_changed = 0, [], 0, []
    for k, v in stats.items():
        parts = k.split(".")
        if parts[0] != "exploratory_adj" or parts[-1] != "verdict_changes" or not isinstance(v, bool):
            continue
        fam, h = parts[1], parts[2]
        if len(parts) == 6:
            suite_cells += 1
            if v:
                suite_changed.append(f"{fam.upper()} {LABEL.get(h, h)} {parts[3]} {_scale_label(parts[4])}")
        elif len(parts) == 8 and parts[5] == "q" and fam == "e1":
            q_cells += 1
            if v:
                pre = ".".join(parts[:-1])
                q_changed.append(f"{LABEL.get(h, h)} {parts[3]} {_scale_label(parts[4])} {parts[6]} "
                                 f"({stats.get(pre + '.verdict_unadjusted', '?').replace('_', ' ')} to "
                                 f"{stats.get(pre + '.verdict_adjusted', '?').replace('_', ' ')})")
    if suite_cells:
        out["p2.adj.suite_cells"], out["p2.adj.suite_changed_n"] = str(suite_cells), str(len(suite_changed))
        out["p2.adj.suite_changed_list"] = "; ".join(suite_changed) if suite_changed else "none"
    if q_cells:
        out["p2.adj.q_cells"], out["p2.adj.q_changed_n"] = str(q_cells), str(len(q_changed))
        out["p2.adj.q_changed_list"] = "; ".join(q_changed) if q_changed else "none"
    # ranges and exception phrases that stay correct when a cell goes the other way (LaTeX markup)
    def pct_range(prefix, suite):
        cells = [(sc, float(out[f"{prefix}.{suite}.{sc}"])) for sc in SCALES[suite] if f"{prefix}.{suite}.{sc}" in out]
        if not cells:
            return None
        lo, hi = min(cells, key=lambda c: c[1]), max(cells, key=lambda c: c[1])
        if lo[1] >= 0:
            return f"{lo[1]:.0f}--{hi[1]:.0f}\\%", ""
        neg = _join([_scale_label(sc) for sc, v in cells if v < 0])
        return f"$-${abs(lo[1]):.0f}\\% to {hi[1]:.0f}\\%", f"; negative at {neg}, where E1 is above C1"
    for kind in ("gap", "e1adj_gap"):
        parts = []
        for h in bridged:
            r = {su: pct_range(f"p2.{h}.{kind}", su) for su in SCALES}
            for su, val in r.items():
                if val:
                    out[f"p2.{h}.{kind}.{su}.range"] = val[0] + (f" ({val[1][2:]})" if val[1] else "")
            if all(r.values()):
                parts.append(f"{LABEL[h]} {r['baseline'][0]} (baseline{r['baseline'][1]}) and "
                             f"{r['complex'][0]} (complex{r['complex'][1]})")
        if parts:
            out[f"p2.{kind}.by_host"] = "; ".join(parts)

    def ci(pre):
        e, lo_, hi_ = (stats.get(f"{pre}.{f}") for f in ("estimate", "ci_low", "ci_high"))
        return f"{e:.2f} [{lo_:.2f}, {hi_:.2f}]"
    not_below = [(h, su, sc) for h in bridged for su in SCALES for sc in SCALES[su]
                 if _number(stats.get(f"{ns(h)}bridge.{su}.{sc}.ci_high")) and stats[f"{ns(h)}bridge.{su}.{sc}.ci_high"] >= 1]
    out["_bridge_not_below"] = not_below
    nb = _join([f"{LABEL[h]} {su} {_scale_label(sc)} ({ci(f'{ns(h)}bridge.{su}.{sc}')})" for h, su, sc in not_below])
    all_bridged = len(bridged) == len(LADDER)
    out["p2.bridge.scope"] = "on every host" if all_bridged else f"on the hosts with a bridge ({_join([LABEL[h] for h in bridged])})"
    out["p2.bridge.scope_note"] = "" if all_bridged else f" ({_join([LABEL[h] for h in bridged])} have a bridge in this build)"
    out["p2.bridge.scope_cap"] = out["p2.bridge.scope"][0].upper() + out["p2.bridge.scope"][1:]
    out["p2.bridge.exception_short"] = ("" if not not_below else " except " + _join(
        [f"{LABEL[h]} {su} {_scale_label(sc)}" for h, su, sc in not_below]) + ", where it includes~1")
    out["p2.bridge.below_phrase"] = ("in every cell on every host" if not not_below else
                                     f"in every cell on every host except {nb}, where the interval includes~1")
    for fam, base in (("e1", "c1"), ("e4", "c4")):
        exc = [(h, su, sc) for h in bridged for su in SCALES for sc in SCALES[su]
               if _number(stats.get(f"{ns(h)}exploratory.{fam}.{su}.{sc}.estimate"))
               and stats[f"{ns(h)}exploratory.{fam}.{su}.{sc}.estimate"] >= stats[f"{ns(h)}{base}.{su}.{sc}.estimate"]]
        out[f"_{fam}_not_below"] = exc
        txt = _join([f"{LABEL[h]} {su} {_scale_label(sc)}" for h, su, sc in exc])
        covered = bool(exc) and set(exc) <= set(not_below)
        out[f"p2.{fam}.below_phrase_ref"] = (
            "at every scale" if not exc else
            "at every scale except in the cell noted above, where the bridge interval includes~1" if covered else
            f"at every scale except {txt}")
        out[f"p2.{fam}.below_phrase"] = ("at every scale on every host" if not exc else
                                         f"at every scale on every host except {txt}, where the bridge interval includes~1")
    incl_cells = {(h, su, sc) for h in bridged for su in SCALES for sc in SCALES[su]
                  if out.get(f"p2.{h}.k1cmp.{su}.{sc}.exceeds") == "no"}
    nbk = [c for c in not_below if c in incl_cells]
    out["p2.k1cmp.bridge_phrase_ref"] = ("as well" if not nbk else
                                         "as well, except where its interval includes~1 (Section~\\ref{sec:res-version})")
    out["p2.k1cmp.bridge_phrase"] = ("as well" if not nbk else "as well, except in " + _join(
        [f"{LABEL[h]} {su} {_scale_label(sc)}" for h, su, sc in nbk]) + ", where its interval includes~1")
    pc_hosts = [h for h in bridged if f"p2.{h}.pcheck.min" in out]
    if pc_hosts:
        out["p2.pcheck.by_host"] = "; ".join(
            f"{LABEL[h]} {out[f'p2.{h}.pcheck.min']}--{out[f'p2.{h}.pcheck.max']} (intervals excluding~1: "
            f"{out[f'p2.{h}.pcheck.excluding_1_list']})" for h in pc_hosts)
    adjgaps = [float(v) for k, v in out.items() if re.fullmatch(r"p2\.[a-z0-9]+\.e1adj_gap\.[a-z]+\.sf\d+", k)]
    if adjgaps:
        out["p2.e1adj_gap.max"] = f"{max(adjgaps):.0f}"
    # protocol check summary (extra file): range of estimates and cells whose CI includes 1
    pc = [(k[: -len(".estimate")], v) for k, v in stats.items()
          if k.startswith("protocol_check.") and k.endswith(".estimate") and _number(v)]
    if pc:
        out["p2.pcheck.min"] = f"{min(v for _, v in pc):.2f}"
        out["p2.pcheck.max"] = f"{max(v for _, v in pc):.2f}"
        incl = sum(1 for pre, _ in pc if _number(stats.get(pre + ".ci_low")) and _number(stats.get(pre + ".ci_high"))
                   and stats[pre + ".ci_low"] <= 1 <= stats[pre + ".ci_high"])
        out["p2.pcheck.cells"] = str(len(pc))
        out["p2.pcheck.cells_including_1"] = str(incl)
    return out


# ---------------------------------------------------------------- claim checks

def claims(stats, case, out):
    """[(id, sentence the prose makes, holds)]. main.tex marks each with \\claim{<id>}."""
    get = stats.get
    bridged = [h for h in LADDER if case[f"bridge_{h}"]]
    res = []

    def all_ci(prefix, suite, below):
        cells = _cells(stats, prefix, suite)
        if below:
            return bool(cells) and all(hi is not None and hi < 1 for _, _, _, hi in cells)
        return bool(cells) and all(lo is not None and lo > 1 for _, _, lo, _ in cells)

    def dominates(host, num, other):
        """|log x_num| > |log x_other| in every cell (cross-host time ratios)."""
        cells = [(get(f"xhost.{host}.{num}.{su}.{sc}.estimate"), get(f"xhost.{host}.{other}.{su}.{sc}.estimate"))
                 for su in SCALES for sc in SCALES[su]]
        return all(_number(a) and _number(b) and abs(math.log(a)) > abs(math.log(b)) for a, b in cells)

    res.append(("bridge-never-slower", "no bridge interval lies entirely above 1 on any host with a bridge",
                not any(_number(get(f"{ns(h)}bridge.{su}.{sc}.ci_low")) and get(f"{ns(h)}bridge.{su}.{sc}.ci_low") > 1
                        for h in bridged for su in SCALES for sc in SCALES[su])))
    nb_set = set(out.get("_bridge_not_below", []))
    for fam in ("e1", "e4"):
        res.append((f"{fam}-exceptions-explained", f"every cell where {fam.upper()} is not below its contrast has a "
                    "bridge interval that includes or exceeds 1", all(c in nb_set for c in out.get(f"_{fam}_not_below", []))))
    res.append(("e1-e4-same-exceptions", "E1 and E4 are not below their contrasts in the same cells, and there is "
                "at most one bridge cell whose interval reaches 1 (the text says 'the cell noted above')",
                set(out.get("_e1_not_below", [])) == set(out.get("_e4_not_below", [])) and len(nb_set) <= 1))
    res.append(("e1-exceptions-k1", "every cell where E1 is not below C1 is one where the shift lies within the K1 limits",
                all(out.get(f"p2.{h}.k1cmp.{su}.{sc}.exceeds") == "no" for h, su, sc in out.get("_e1_not_below", []))
                or not case["pcheck"]))
    res.append(("first-above-sustained", "every first-above-1 scale is followed by CIs above 1 at all larger scales",
                all(v == "yes" for k, v in out.items() if k.endswith(".sustained"))))
    res.append(("h100-smt", "the H100 host reports 2 hardware threads per core and 26 threads occupy 13 cores",
                get("host.smt_threads_per_core") == 2 and get("host.cpus_arg_cores_max") == 13))
    res.append(("cores-differ", "26 threads occupy more physical cores on the A100 and GH200 than on the H100 and B200",
                min(get("a100.host.cpus_arg_cores_min"), get("gh200.host.cpus_arg_cores_min"))
                > max(get("host.cpus_arg_cores_max"), get("b200.host.cpus_arg_cores_max"))))
    res.append(("same-threads", "every host ran its engines with the same --cpus value",
                len({get(f"{ns(h)}host.cpus_arg") for h in LADDER}) == 1))
    res.append(("gh200-cpu-faster", "on the GH200, Polars CPU was faster than on the H100 in every cell (CI below 1)",
                all(all_ci("xhost.gh200.polars", s, True) for s in SCALES)))
    res.append(("a100-cpu-slower", "on the A100, Polars CPU was slower than on the H100 in every cell (CI above 1)",
                all(all_ci("xhost.a100.polars", s, False) for s in SCALES)))
    res.append(("gh200-c1-lower", "GH200 C1 is below H100 C1 at every scale (point estimates)",
                all(get(f"gh200.c1.{s}.{sc}.estimate") < get(f"c1.{s}.{sc}.estimate") for s in SCALES for sc in SCALES[s])))
    res.append(("gh200-cpu-side", "on the GH200 the Polars CPU cross-host ratio is further from 1 than the Polars GPU one in every cell",
                dominates("gh200", "polars", "polars_gpu")))
    res.append(("a100-gpu-side", "on the A100 the Polars GPU cross-host ratio is further from 1 than the Polars CPU one in every cell",
                dominates("a100", "polars_gpu", "polars")))
    identity = True
    for h in OTHERS:
        for c, cpu, gpu in PAIRS:
            for su in SCALES:
                for sc in SCALES[su]:
                    if out.get(f"p2.{h}.{c}.{su}.{sc}.identity") != "yes":
                        continue
                    lhs = math.log(get(f"{h}.{c}.{su}.{sc}.estimate") / get(f"{c}.{su}.{sc}.estimate"))
                    rhs = math.log(get(f"xhost.{h}.{cpu}.{su}.{sc}.estimate") / get(f"xhost.{h}.{gpu}.{su}.{sc}.estimate"))
                    identity &= abs(lhs - rhs) < 1e-9
    res.append(("speedup-identity", "C_host/C_H100 = CPU-engine ratio / GPU-engine ratio wherever the query sets match",
                identity))
    res.append(("identity-only-c1-complex", "the query sets differ only for C1 on the complex suite",
                all((v == "yes") != (".c1.complex." in k) for k, v in out.items() if k.endswith(".identity"))))
    res.append(("cores-pairs", "the H100 and B200 report the same SMT and cores used, as do the A100 and GH200; "
                "only the H100/B200 report SMT, and their threads match the vCPU count",
                out.get("p2.h100.cores_used") == out.get("p2.b200.cores_used")
                and out.get("p2.a100.cores_used") == out.get("p2.gh200.cores_used")
                and get("host.smt_threads_per_core") == get("b200.host.smt_threads_per_core") == 2
                and get("a100.host.smt_threads_per_core") == get("gh200.host.smt_threads_per_core") == 1
                and get("host.cpus_arg_share_of_vcpus") == get("b200.host.cpus_arg_share_of_vcpus") == 1.0))
    k1keys = [k for k in stats if k.startswith("protocol_check.") and k.endswith(".gap_exceeds_k1_limits")]
    if k1keys:
        agree = True
        for k in k1keys:
            h, su, sc = k.split(".")[1:4]
            pre = f"p2.{h}.k1cmp.{su}.{sc}"
            mine = out.get(pre + ".exceeds")
            agree &= mine is not None and (mine == "yes") == bool(stats[k])
            for theirs, ours in (("e1_c1_gap", "gap_value"), ("k1_limit", "limit_value")):
                v = stats.get(f"protocol_check.{h}.{su}.{sc}.{theirs}")
                agree &= _number(v) and abs(v - out.get(pre + "." + ours, float("nan"))) < 1e-9
        res.append(("k1cmp-agrees", "the analysis' per-cell gap_exceeds_k1_limits, gap and limit match the "
                    "converter's derivation", agree))
    res.append(("k1cmp-reversal-exceeds", "every reversal cell (C1 above 1, E1 below 1) has a shift beyond the K1 limits",
                all(out.get(f"p2.{h}.k1cmp.{su}.{sc}.exceeds", "yes") == "yes" for h, su, sc in out.get("_reversal_cells", []))))
    res.append(("reversal-exists", "at least one host-suite-scale cell has C1's CI above 1 and E1's below 1",
                out.get("p2.reversal.detail", "none") != "none"))
    res.append(("gap-grows-baseline", "100(1-E1/C1) grows at every step in scale on the baseline suite on every bridged host",
                all(all(float(a) < float(b) for a, b in zip(g, g[1:]))
                    for h in bridged for g in [[out[f"p2.{h}.gap.baseline.{sc}"] for sc in SCALES["baseline"]
                                                if f"p2.{h}.gap.baseline.{sc}" in out]])))
    res.append(("prior-threads", "the earlier A100 study used more threads than this campaign",
                get("a100_h100.study.cpu_threads") > get("a100_h100.study.cpu_threads_h100")))
    if case["pcheck"]:
        cells = [(k[len("protocol_check."):-len(".estimate")], v) for k, v in stats.items()
                 if k.startswith("protocol_check.") and k.endswith(".estimate") and _number(v)]
        small = True
        for cell, v in cells:
            host, suite, sc = cell.split(".")
            b = out.get(f"p2.{host}.bridge_gpuq.{suite}.{sc}")
            small &= b is not None and abs(math.log(v)) < abs(math.log(float(b)))
        res.append(("pcheck-small", "in every protocol-check cell the session ratio is closer to 1 than the "
                    "bridge over the GPU queries for the same host, suite and scale", bool(cells) and small))
        res.append(("h100-pcheck-one-excluded", "exactly one H100 K1 interval excludes 1, at complex SF100 (the text quotes it)",
                    out.get("p2.h100.pcheck.excluding_1_list") == "complex SF100"))
        res.append(("a100-pcheck-below", "every A100 K1 interval that excludes 1 lies below 1",
                    all(get(f"protocol_check.a100.{su}.{sc}.ci_high") < 1
                        for su in SCALES for sc in SCALES[su]
                        if _number(get(f"protocol_check.a100.{su}.{sc}.ci_low"))
                        and (get(f"protocol_check.a100.{su}.{sc}.ci_low") > 1 or get(f"protocol_check.a100.{su}.{sc}.ci_high") < 1))
                    and out.get("p2.a100.pcheck.n_excluding_1", "0") != "0"))
        res.append(("gh200-pcheck-none", "no GH200 K1 interval excludes 1",
                    out.get("p2.gh200.pcheck.excluding_1_list") == "none"))
        res.append(("h100-pcheck-above-1", "every H100 protocol-check point estimate is above 1",
                    all(v > 1 for cell, v in cells if cell.startswith("h100."))))
    if case["pcheck"] and case["bridge_h100"]:
        res.append(("h100-adj-shrinks", "on the H100 the session-adjusted E1 is below C1 at every scale",
                    all(float(out[f"p2.h100.e1adj_gap.{su}.{sc}"]) > 0 for su in SCALES for sc in SCALES[su])))
    if case["e1adj"]:
        ok = True
        for k, v in stats.items():
            if k.startswith("exploratory_adj.e1.") and k.endswith(".estimate") and len(k.split(".")) == 6 and _number(v):
                h, su, sc = k.split(".")[2:5]
                e1 = get(f"{ns(h)}exploratory.e1.{su}.{sc}.estimate")
                k1 = get(f"exploratory_adj.e1.{h}.{su}.{sc}.session_check.estimate")
                ok &= _number(e1) and _number(k1) and abs(math.log(v) - math.log(e1 / k1)) < 1e-6
        res.append(("e1adj-matches", "the delivered session-adjusted E1 equals E1/K1 for the point estimates", ok))
        res.append(("adj-ci-sustained", "every adjusted-E1 first-above-1 scale (interval) is followed by intervals above 1",
                    all(v == "yes" for k, v in out.items() if ".e1adj_ci." in k and k.endswith(".sustained"))))
    if case["decomp"]:
        ok = True
        for h in OTHERS:
            for sc in SCALES["complex"]:
                pre = f"decomp.{h}.c1.complex.{sc}"
                r, cpu = get(f"{pre}.speedup_ratio.estimate"), get(f"{pre}.cpu_side.estimate")
                gpu = get(f"xhost.{h}.polars_gpu.complex.{sc}.estimate")
                if all(_number(v) for v in (r, cpu, gpu)):
                    ok &= abs(math.log(r) - (math.log(cpu) - math.log(gpu))) < 1e-6
        res.append(("decomp-identity", "C1 complex: speedup_ratio = matched cpu_side / xhost polars_gpu", ok))
    return res


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", action="append", default=[], metavar="[PREFIX=]PATH")
    ap.add_argument("--extra", type=Path, help="Paper 2 analysis file (protocol_check.*, decomp.*), optional")
    ap.add_argument("--output", type=Path, default=PAPER2 / "generated" / "stats.tex")
    ap.add_argument("--tex", type=Path, nargs="+", default=[PAPER2 / "main.tex"])
    ap.add_argument("--allow-missing", action="store_true",
                    help="draft: missing keys print as placeholders; failed claims are listed, not fatal")
    ap.add_argument("--list-keys", action="store_true")
    ap.add_argument("--case", help="with --list-keys: COND=0|1,... (default: every case)")
    args = ap.parse_args(argv)
    try:
        if args.list_keys:
            print("\n".join(required_keys(args.tex, parse_case(args.case))))
            return 0
        stats, display, formats, sources, provenance = statskeys.load_many(args.stats)
        names = {name for name, _ in sources}
        status = statskeys.host_status(names, {})
        absent = ([f"--stats {h}=PATH" for h in OTHERS if not status[h]]
                  + [f"--stats xhost_{h}=PATH" for h in OTHERS if f"xhost_{h}" not in names])
        if absent:
            raise StatsError("Paper 2 needs all four host campaigns and their cross-host files; missing: "
                             + ", ".join(absent))
        if args.extra:
            extra, extra_display, extra_formats, synthetic = statskeys.load_stats(args.extra)
            clash = sorted(set(extra) & set(stats))
            if clash:
                raise StatsError(f"{args.extra}: keys already defined by the host files: {', '.join(clash[:5])}")
            stats.update(extra)
            display.update(extra_display)
            formats.update(extra_formats)
            sources.append(("paper2", args.extra))
            provenance.append({"name": "paper2", "synthetic": synthetic, "display": list(extra_display)})
        for pattern, spec in EXTRA_FORMATS.items():
            formats.setdefault(pattern, spec)
        display.update(statskeys.host_summaries(stats, status))
        case = conditions(stats)
        out = derived(stats, case)
        for key, value in out.items():
            if key.startswith("_") or key.endswith((".gap_value", ".limit_value")):
                continue  # unrounded values used only by claims()
            if key not in stats and key not in display:  # a key given by the analysis wins
                display[key] = value
        checks = claims(stats, case, out)
        failed = [(cid, text) for cid, text, ok in checks if not ok]
        unknown = sorted(cited_claims(args.tex, case) - {cid for cid, _, _ in checks})
        if unknown:
            raise StatsError("\\claim{} id(s) in the text with no check in claims(): " + ", ".join(unknown))
        required = required_keys(args.tex, case)
        missing = statskeys.missing_keys(required, stats, display)
        if (missing or failed) and not args.allow_missing:
            for cid, text in failed:
                print(f"ERROR: claim {cid} no longer holds: {text}", file=sys.stderr)
            if missing:
                print(f"ERROR: {len(missing)} key(s) used by Paper 2 are missing:", file=sys.stderr)
                for key in missing:
                    print(f"  {key}", file=sys.stderr)
            return 1
        draft = bool(missing or failed)
        manual, synthetic = statskeys.manual_values(provenance, False, draft)
        lines = ["% generated by paper2/tools/stats_to_tex.py -- do not edit"]
        for name, path in sources:
            lines.append(f"% {name}: {path} sha256 {hashlib.sha256(Path(path).read_bytes()).hexdigest()}")
        lines.append("\\statsfinalfalse" if draft else "\\statsfinaltrue")
        if manual:
            lines += [f"% manual values (_display) -- {m}" for m in manual] + ["\\manualvaluestrue"]
        if synthetic:
            lines += [f"% synthetic stats file: {', '.join(synthetic)}", "\\syntheticstatstrue"]
        lines += [f"% condition {c}: {'true' if case[c] else 'false'}" for c in CONDS]
        lines += [f"\\{FLAG[c]}false" for c in CONDS if not case[c]]
        nulls = []
        for key in sorted(set(stats) | set(display)):
            if key in display and isinstance(display[key], float):
                # computed summaries (e.g. Paper 1's xhost.<host>.<engine>.overall.min) use the key's format
                text, markup = statskeys.format_value(key, display[key], formats, "tex")
            elif key in display:
                text, markup = str(display[key]), key in MARKUP_KEYS or key.endswith((".range", ".k1cmp.inconclusive_list"))
            else:
                if isinstance(stats[key], list):
                    continue  # lists are never printable; only an error if a macro asks for one
                if stats[key] is None:
                    nulls.append(key)
                text, markup = statskeys.format_value(key, stats[key], formats, "tex")
            lines.append(f"\\defstat{{{key}}}{{{text if markup else tex_escape(text)}}}")
        listed = sorted(k for k in required if isinstance(stats.get(k), list))
        if listed:
            raise StatsError("required key(s) hold lists, not printable values: " + ", ".join(listed))
    except StatsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n")
    (args.output.parent / "conditions.json").write_text(json.dumps(case, indent=2) + "\n")
    used_nulls = sorted(set(nulls) & set(required))
    if used_nulls:
        print("WARNING: required key(s) are null and print as 'n/a': " + ", ".join(used_nulls), file=sys.stderr)
    if draft:
        print(f"WARNING: draft mode: {len(missing)} key(s) missing, {len(failed)} claim(s) failed", file=sys.stderr)
        for cid, text in failed:
            print(f"  claim {cid}: {text}", file=sys.stderr)
    on = ", ".join(c for c in CONDS if case[c]) or "none"
    print(f"wrote {args.output}: {len(stats) + len(display)} statistics, {len(required)} required; "
          f"conditions true: {on}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Audit demo labels and the moveset graph against what the game actually
played (the player's animation id, lmtID, logged by state_reader.lua).
Best run over EXPERT demonstrations — hunts where the combos were done
on purpose — since that's what settles the graph's open edges.

Reports:
  1. Label consistency — for each labelled move (graph node, charge
     level), which lmtIDs it produced first. A clean label maps to one
     dominant lmtID; a scattered one means the segmenter or the graph is
     mislabelling it. The lmtID catalog is built only from TRUSTED labels:
     not re-rooted (a re-root from a non-neutral root is the tracker's
     fallback guess) and not an input a candidate edge is about —
     otherwise the very labels under audit would define the answer.
  2. Declared edges never seen in any demo.
  3. Candidate edges (the moveset YAML's `candidates:`, which the tracker
     ignores): for demos where the candidate's input was pressed at its
     from-node, did the game play the candidate's target (supported) or
     the re-rooted move the tracker labelled instead (contradicted)?
  4. Transitions the game played that the graph doesn't declare
     (consecutive catalogued lmtIDs close enough in time to be a chain).
  5. Measured combo gaps per node (time from one move's input ending to
     the next chained input) — for replacing the placeholder
     `combo_window_s` values.

Usage:
    python scripts/audit_tool_labels.py                         # all v2 episodes under demos/storage
    python scripts/audit_tool_labels.py demos/storage/<id> ... --write-catalog configs/weapons/greatsword_lmt_catalog.yaml

Nothing here edits the moveset graph — promote/drop edges by hand, then
scripts/relabel_demos.py to re-label old episodes against the new graph.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demos.labeling import read_jsonl  # noqa: E402
from env.tools import ToolSet  # noqa: E402


def primary_lmt(rec):
    during = rec.get("lmt", {}).get("during") or []
    return during[0] if during else None


def node_key(rec) -> str:
    level = (rec.get("call") or {}).get("args", {}).get("level")
    to = rec["graph"]["to"]
    return f"{to}@{level}" if level else to


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episodes", nargs="*")
    parser.add_argument("--storage", default="demos/storage")
    parser.add_argument("--tools", default="configs/weapons/greatsword_tools.yaml")
    parser.add_argument("--min-count", type=int, default=2, help="ignore undeclared transitions seen fewer times")
    parser.add_argument("--write-catalog", default=None, help="write the lmtID -> move catalog as YAML here")
    args = parser.parse_args()

    toolset = ToolSet.from_config(args.tools)
    graph = toolset.graph
    dirs = [Path(p) for p in args.episodes] or sorted(p.parent for p in Path(args.storage).glob("*/tool_calls.jsonl"))
    episodes = [(d, read_jsonl(d / "tool_calls.jsonl"), read_jsonl(d / "lmt_events.jsonl")) for d in dirs]
    moves = [(d, r) for d, recs, _ in episodes for r in recs if r["graph"].get("move")]
    print(f"{len(episodes)} episode(s), {len(moves)} labelled move(s), "
          f"{sum(len(l) for _, _, l in episodes)} lmtID change(s).\n")
    if not moves:
        raise SystemExit("Nothing to audit yet — record some v2 demos first (scripts/record_hunt.py).")

    # 1. Label consistency + catalog (trusted labels only — see docstring)
    candidate_inputs = set()
    for cand in graph.candidates:
        if "continues_as" in cand:
            candidate_inputs |= {(cand["from"], i) for i in graph.claimed_edges(cand["continues_as"])}
        else:
            candidate_inputs.add((cand["from"], cand["input"]))

    def trusted(r):
        return not r["graph"]["rerooted"] and (r["graph"]["from"], r["input"]) not in candidate_inputs

    by_key: dict[str, Counter] = defaultdict(Counter)
    for _, r in moves:
        by_key[node_key(r)][primary_lmt(r)] += 1
    catalog: dict = {}
    for _, r in moves:
        if trusted(r) and primary_lmt(r) is not None:
            catalog.setdefault(primary_lmt(r), Counter())[node_key(r)] += 1
    print("=== 1. Label consistency (first lmtID after each labelled move) ===")
    for key in sorted(by_key):
        c = by_key[key]
        (top, n), total = c.most_common(1)[0], sum(c.values())
        flag = "" if n / total >= 0.8 else "   <- inconsistent"
        print(f"  {key:34s} n={total:4d}  top lmt={top} ({n / total:.0%})  {dict(c.most_common(4))}{flag}")
    lmt_to_key = {lmt: c.most_common(1)[0][0] for lmt, c in catalog.items()}
    key_to_lmt = {}
    for lmt, key in lmt_to_key.items():
        key_to_lmt.setdefault(key.split("@")[0], set()).add(lmt)

    # 2. Declared edges never seen
    seen_edges = Counter(e for _, r in moves for e in r["graph"].get("edge_ids", []))
    unseen = [e.id for e in graph.edges + graph.any_edges if not seen_edges[e.id]]
    print(f"\n=== 2. Declared edges never seen ({len(unseen)}/{len(graph.edges) + len(graph.any_edges)}) ===")
    for e in unseen:
        print(f"  {e}")

    # 3. Candidate edges
    print("\n=== 3. Candidate edges (not declared; tracker ignores them) ===")
    for cand in graph.candidates:
        frm = cand["from"]
        if "continues_as" in cand:
            target_node = cand["continues_as"]
            inputs = {i: e.to for i, e in graph.claimed_edges(target_node).items() if e.from_node != "*"}
            desc = f"{frm} continues_as {target_node}"
        else:
            to = cand["to"]
            if graph.nodes[to].kind == "charge":
                release = next((e.to for e in graph.edges if e.from_node == to and e.input == "release"), to)
                inputs = {cand["input"]: release}
            else:
                inputs = {cand["input"]: to}
            desc = f"{frm} --{cand['input']}--> {to}"
        support = contra = other = 0
        for _, r in moves:
            g = r["graph"]
            if g["from"] != frm or r["input"] not in inputs:
                continue
            lmt = primary_lmt(r)
            expected = key_to_lmt.get(inputs[r["input"]], set())
            labelled = key_to_lmt.get(g["to"], set())
            if lmt in expected and lmt not in labelled:
                support += 1
            elif lmt in labelled and lmt not in expected:
                contra += 1
            else:
                other += 1
        verdict = ("no data" if not (support + contra + other) else
                   "SUPPORTED" if support > contra else "contradicted" if contra > support else "unclear")
        print(f"  {desc:55s} {verdict:12s} (support {support}, contra {contra}, ambiguous {other})")

    # 4. Undeclared transitions the game played
    declared_pairs = {(e.from_node, e.to) for e in graph.edges}
    for e in graph.edges:  # a y_hold edge lands on a charge node; count its finishers too
        if graph.nodes[e.to].kind == "charge":
            declared_pairs |= {(e.from_node, f.to) for f in graph.edges if f.from_node == e.to}
    observed = Counter()
    for _, _, lmt_events in episodes:
        seq = [(r["t_arrival"], lmt_to_key[r["lmt_id"]].split("@")[0])
               for r in lmt_events if r.get("lmt_id") in lmt_to_key]
        for (ta, a), (tb, b) in zip(seq, seq[1:]):
            node = graph.nodes.get(a)
            if node is not None and tb - ta <= node.duration_s + node.combo_window_s:
                observed[(a, b)] += 1
    print("\n=== 4. Observed transitions not declared (by catalog; min count "
          f"{args.min_count}) ===")
    for (a, b), n in observed.most_common():
        if n >= args.min_count and a != b and (a, b) not in declared_pairs \
                and graph.nodes.get(a) is not None and graph.nodes[a].kind != "root":
            print(f"  {a} -> {b}  x{n}")

    # 5. Combo gaps
    gaps: dict[str, list[float]] = defaultdict(list)
    for _, recs, _ in episodes:
        prev = None
        for r in recs:
            g = r["graph"]
            if not g.get("move"):
                continue
            if prev is not None and not g["rerooted"] and not g["expired"] and not g["starts_chain"]:
                gaps[g["from"]].append(r["t_start"] - prev["t_end"])
            prev = r
    print("\n=== 5. Chained-input gaps per node (suggest combo_window_s >= max) ===")
    for node in sorted(gaps):
        v = gaps[node]
        current = graph.nodes[node].combo_window_s + graph.nodes[node].duration_s
        print(f"  {node:24s} n={len(v):4d} median={statistics.median(v):.2f}s max={max(v):.2f}s "
              f"(graph allows {current:.2f}s after the move commits)")

    if args.write_catalog:
        lines = ["# lmtID -> labelled move, from scripts/audit_tool_labels.py. Generated; don't hand-edit.",
                 f"# episodes: {[d.name for d, _, _ in episodes]}", "catalog:"]
        for lmt, c in sorted(catalog.items(), key=lambda kv: str(kv[0])):
            lines.append(f"  {json.dumps(lmt)}: {{move: {c.most_common(1)[0][0]}, counts: {json.dumps(dict(c))}}}")
        Path(args.write_catalog).write_text("\n".join(lines) + "\n")
        print(f"\nWrote {args.write_catalog}")


if __name__ == "__main__":
    main()

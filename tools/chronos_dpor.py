#!/usr/bin/env python3
"""Trace-guided DPOR support for Chronos' deterministic prefix scheduler.

The dependence relation is conservative for synchronization objects and exact
for named annotated memory accesses. Only dependent, unordered events generate
backtracking choices. The explorer reports completeness separately from its
schedule budget; unsupported or unannotated memory accesses are outside this
model, as documented by Chronos.
"""
from __future__ import annotations
import json
from pathlib import Path

READS = {"read", "atomic_load"}
WRITES = {"write", "atomic_store", "atomic_rmw"}
SYNC = {
    "mutex_lock", "mutex_trylock", "mutex_unlock", "cond_wait", "cond_wake",
    "cond_signal", "cond_broadcast", "sem_wait", "sem_trywait", "sem_post",
    "barrier_wait", "barrier_leave", "once", "atomic_load", "atomic_store",
    "atomic_rmw",
}


def read_trace(path):
    events = []
    race_pairs = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "i" in item and "t" in item:
                events.append(item)
            elif item.get("end") == 1:
                result = item.get("result", {})
                for finding in result.get("findings", []):
                    a, b = finding.get("a", {}), finding.get("b", {})
                    if isinstance(a.get("step"), int) and isinstance(b.get("step"), int):
                        race_pairs.add(tuple(sorted((a["step"], b["step"]))))
    for i, j in race_pairs:
        if 0 <= i < len(events) and 0 <= j < len(events):
            events[i].setdefault("_race_pairs", []).append(j)
            events[j].setdefault("_race_pairs", []).append(i)
    return events


def _mask(event):
    try:
        return int(str(event.get("run", "0")), 16)
    except ValueError:
        return 0


def _dependent(a, b):
    if b.get("i") in a.get("_race_pairs", []) or a.get("i") in b.get("_race_pairs", []):
        return True
    oa, ob = a.get("op", ""), b.get("op", "")
    if oa in (READS | WRITES) and ob in (READS | WRITES):
        na, nb = a.get("nm"), b.get("nm")
        if na and nb and na == nb and (oa in WRITES or ob in WRITES):
            return True
        # Address-based instrumentation may not have source names. A runtime
        # race finding is then the authoritative dependence source.
        return False
    if oa in SYNC and ob in SYNC and a.get("o", -1) >= 0 and a.get("o") == b.get("o"):
        return True
    # Thread creation/join and exit affect reachability/order.
    if oa in {"create", "join", "exit", "start"} or ob in {"create", "join", "exit", "start"}:
        return oa == ob or {oa, ob} <= {"create", "start", "exit", "join"}
    return False


def _hb_matrix(events):
    n = len(events)
    edges = [set() for _ in range(n)]
    last_by_tid = {}
    for idx, ev in enumerate(events):
        tid = ev.get("t")
        if tid in last_by_tid:
            edges[last_by_tid[tid]].add(idx)
        last_by_tid[tid] = idx
        for source in ev.get("hb", []):
            if isinstance(source, int) and 0 <= source < n:
                edges[source].add(idx)
    reach = [set(x) for x in edges]
    for k in range(n):
        for i in range(n):
            if k in reach[i]:
                reach[i].update(reach[k])
    return reach


def backtracking_choices(events, tids):
    """Return (prefix, risk score) choices for dependent unordered pairs."""
    if not events:
        return []
    hb = _hb_matrix(events)
    scores = {}
    n = min(len(events), len(tids))
    for i in range(n):
        a = events[i]
        ta = a.get("t")
        for j in range(i + 1, n):
            b = events[j]
            tb = b.get("t")
            if ta == tb or not _dependent(a, b):
                continue
            if j in hb[i] or i in hb[j]:
                continue
            # Backtrack at the latest earlier state where b's thread was
            # enabled. If it is not runnable at i, moving it to an earlier
            # enabled prefix can change the enabling chain; that new trace is
            # analysed recursively by the explorer.
            backtrack_at = next((k for k in range(i, -1, -1)
                                 if _mask(events[k]) & (1 << int(tb))), None)
            if backtrack_at is None:
                continue
            prefix = tuple(tids[:backtrack_at]) + (int(tb),)
            oa, ob = a.get("op", ""), b.get("op", "")
            if oa in WRITES and ob in WRITES:
                score = 10
            elif oa in WRITES or ob in WRITES:
                score = 7
            elif oa in SYNC and ob in SYNC:
                score = 4
            else:
                score = 2
            if a.get("_race_pairs") or b.get("_race_pairs"):
                score += 3
            scores[prefix] = max(scores.get(prefix, 0), score)
    return sorted(scores.items(), key=lambda item: (-item[1], len(item[0]), item[0]))


def backtracking_prefixes(events, tids):
    """Return prefixes that reverse a dependent unordered event pair."""
    return [prefix for prefix, _score in backtracking_choices(events, tids)]


def dependent_pair_count(events):
    if not events:
        return 0
    hb = _hb_matrix(events)
    count = 0
    for i, a in enumerate(events):
        for j in range(i + 1, len(events)):
            b = events[j]
            if a.get("t") == b.get("t") or not _dependent(a, b):
                continue
            if j not in hb[i] and i not in hb[j]:
                count += 1
    return count

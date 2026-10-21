#!/usr/bin/env python3
"""chronos - driver for the Chronos concurrency testing runtime (Phase 1).

Sub-commands
  run       one execution under a chosen strategy
  explore   random | pct | dfs schedule exploration (optionally stop at first failure)
  replay    strictly replay a saved schedule (N times), verifying the failure signature
  minimize  delta-debug a failing schedule down to a minimal set of schedule deviations
  trace     replay a schedule and capture a full execution trace
  viz       build the self-contained HTML visualiser from one or more traces
  bench     run the whole benchmark manifest (see tools/bench.py)

All schedules are plain text (docs/FORMATS.md). Everything the tool prints is
derived from files written by the runtime, never from guesses.
"""
import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "build" / "libchronos.so"

# --------------------------------------------------------------------------
# single execution
# --------------------------------------------------------------------------
EXEC_COUNT = 0   # number of program executions performed through run_once (reported by the test-suite)


class Result:
    """Outcome of one execution."""

    def __init__(self):
        self.status = "noresult"      # ok | failure | divergence | timeout | config_error | noresult
        self.signature = None
        self.steps = 0
        self.switches = 0
        self.preemptions = 0
        self.findings = []
        self.failure = None
        self.divergence = None
        self.rc = None
        self.wall = 0.0
        self.raw = {}

    @property
    def ok(self):
        return self.status == "ok"

    def finding_types(self):
        return sorted({f["type"] for f in self.findings})

    def __repr__(self):
        return f"Result({self.status}, {self.signature}, steps={self.steps})"


def run_once(prog, args=(), *, strategy="random", seed=1, pct_depth=3, pct_steps=200,
             schedule_in=None, preempt_in=None, prefix_in=None, fatal=(), spurious=0,
             max_steps=200000, watchdog_ms=20000, timeout=60, sched_out=None,
             trace_out=None, extra_env=None, preload=True, lib=None):
    """Run `prog` under the runtime and return a Result."""
    global EXEC_COUNT
    EXEC_COUNT += 1
    res = Result()
    fd, rpath = tempfile.mkstemp(prefix="chronos_res_", suffix=".json")
    os.close(fd)
    os.unlink(rpath)
    env = dict(os.environ)
    if preload:
        env["LD_PRELOAD"] = str(lib or LIB)
        env.update({
            "CHRONOS_STRATEGY": strategy, "CHRONOS_SEED": str(seed),
            "CHRONOS_PCT_DEPTH": str(pct_depth), "CHRONOS_PCT_STEPS": str(pct_steps),
            "CHRONOS_MAX_STEPS": str(max_steps), "CHRONOS_SPURIOUS": str(spurious),
            "CHRONOS_WATCHDOG_MS": str(watchdog_ms), "CHRONOS_QUIET": "1",
            "CHRONOS_RESULT_OUT": rpath,
        })
        for k, v in (("CHRONOS_SCHEDULE_IN", schedule_in), ("CHRONOS_PREEMPT_IN", preempt_in),
                     ("CHRONOS_PREFIX_IN", prefix_in), ("CHRONOS_SCHEDULE_OUT", sched_out),
                     ("CHRONOS_TRACE_OUT", trace_out)):
            if v:
                env[k] = str(v)
        env["CHRONOS_FATAL"] = ",".join(fatal)
        if not fatal:
            env.pop("CHRONOS_FATAL")
        env["CHRONOS_PROGRAM"] = str(prog)
    if extra_env:
        env.update(extra_env)
    t0 = time.time()
    try:
        p = subprocess.run([str(prog), *map(str, args)], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout, start_new_session=True)
        res.rc = p.returncode
    except subprocess.TimeoutExpired:
        res.status = "timeout"
        res.signature = "TIMEOUT"
        res.wall = time.time() - t0
        return res
    res.wall = time.time() - t0
    if not preload:
        res.status = "ok" if res.rc == 0 else "failure"
        res.signature = None if res.rc == 0 else f"NATIVE[rc={res.rc}]"
        return res
    if os.path.exists(rpath):
        try:
            with open(rpath) as f:
                raw = json.load(f)
        finally:
            os.unlink(rpath)
        res.raw = raw
        res.status = raw["status"]
        res.steps = raw.get("steps", 0)
        res.switches = raw.get("switches", 0)
        res.preemptions = raw.get("preemptions", 0)
        res.findings = raw.get("findings", [])
        res.failure = raw.get("failure")
        res.divergence = raw.get("divergence")
        if res.failure:
            res.signature = res.failure["signature"]
    elif res.rc == 88:
        res.status = "config_error"
    else:
        res.status = "noresult"
        res.signature = f"NORESULT[rc={res.rc}]"
    return res


def sig_matches(sig, target):
    if target is None:
        return sig is not None
    return sig is not None and sig.startswith(target)


# --------------------------------------------------------------------------
# schedule / override / prefix files
# --------------------------------------------------------------------------
Step = collections.namedtuple("Step", "step tid opidx op obj run")


def parse_schedule(path):
    """Return (header dict, [Step]). Raises ValueError on malformed files."""
    with open(path) as f:
        lines = f.read().split("\n")
    if not lines or lines[0].split() != ["chronos-schedule", "1"]:
        raise ValueError("bad schedule header")
    hdr, steps, i, declared = {}, [], 1, None
    while i < len(lines):
        t = lines[i].split()
        i += 1
        if not t:
            continue
        if t[0] == "steps":
            declared = int(t[1])
            break
        hdr[t[0]] = t[1] if len(t) > 1 else ""
    if declared is None:
        raise ValueError("missing steps line")
    for k in range(declared):
        t = lines[i + k].split()
        steps.append(Step(int(t[0]), int(t[1]), int(t[2]), t[3], int(t[4]), int(t[5], 16)))
    return hdr, steps


def write_schedule(path, steps, program="prog", fail=None):
    with open(path, "w") as f:
        f.write(f"chronos-schedule 1\nprogram {program}\n")
        if fail:
            f.write(f"fail {fail}\n")
        f.write(f"steps {len(steps)}\n")
        for s in steps:
            f.write(f"{s.step} {s.tid} {s.opidx} {s.op} {s.obj} {s.run:x}\n")
        f.write("end\n")


def write_overrides(path, ovs):
    with open(path, "w") as f:
        f.write("chronos-preempt 1\n")
        for p, c, n in sorted(ovs):
            f.write(f"{p} {c} {n}\n")
        f.write("end\n")


def write_prefix(path, tids):
    with open(path, "w") as f:
        f.write("chronos-prefix 1\n")
        for i in range(0, len(tids), 40):
            f.write(" ".join(map(str, tids[i:i + 40])) + "\n")
        f.write("end\n")


def lowest_bit(mask):
    return (mask & -mask).bit_length() - 1


def derive_overrides(steps):
    """Deviations of a schedule from the non-preemptive base policy.

    Base policy: keep running the previously running thread while it is
    runnable, otherwise the lowest runnable thread id. An override
    (prev, cnt) -> next means "at the decision after thread `prev` has
    executed `cnt` operations, run `next` instead of the base choice".
    """
    ovs = []
    for i, s in enumerate(steps):
        if i == 0:
            prev, cnt = 0, 0
        else:
            prev, cnt = steps[i - 1].tid, steps[i - 1].opidx + 1
        base = prev if (s.run >> prev) & 1 else lowest_bit(s.run)
        if s.tid != base:
            ovs.append((prev, cnt, s.tid))
    return ovs


def preemption_prefix_counts(steps):
    """pre[i] = number of preemptions among decisions 0..i-1."""
    pre = [0] * (len(steps) + 1)
    for i, s in enumerate(steps):
        inc = 0
        if i > 0:
            prev = steps[i - 1].tid
            if s.tid != prev and (s.run >> prev) & 1:
                inc = 1
        pre[i + 1] = pre[i] + inc
    return pre


def bits(mask):
    i = 0
    while mask:
        if mask & 1:
            yield i
        mask >>= 1
        i += 1


# --------------------------------------------------------------------------
# exploration
# --------------------------------------------------------------------------
def estimate_steps(prog, args, **kw):
    """Pilot run used to size PCT's change-point range."""
    r = run_once(prog, args, strategy="random", seed=0, **kw)
    return max(r.steps, 4)


def explore_sampling(prog, args, *, strategy="random", runs=1000, seed0=1, stop_on_first=True,
                     target=None, fatal=(), pct_depths=4, pct_steps=None, spurious=0, timeout=60,
                     out_dir=None, keep_first=True, watchdog_ms=20000, max_steps=200000):
    """Random / PCT sampling. Returns a stats dict."""
    st = {"strategy": strategy, "runs": 0, "failures": 0, "first_failure_run": None,
          "signatures": collections.Counter(), "finding_types": collections.Counter(),
          "anomalies": 0, "wall": 0.0, "steps_total": 0, "first_failure_seed": None}
    if strategy == "pct" and pct_steps is None:
        pct_steps = estimate_steps(prog, args, fatal=fatal, spurious=spurious, timeout=timeout)
    tmp = tempfile.mkdtemp(prefix="chronos_exp_")
    t0 = time.time()
    try:
        for i in range(runs):
            seed = seed0 + i
            sp = os.path.join(tmp, "s.sched")
            kw = dict(strategy=strategy, seed=seed, fatal=fatal, spurious=spurious, timeout=timeout,
                      sched_out=sp, watchdog_ms=watchdog_ms, max_steps=max_steps)
            if strategy == "pct":
                kw["pct_depth"] = 1 + (i % pct_depths)
                kw["pct_steps"] = pct_steps
            r = run_once(prog, args, **kw)
            st["runs"] += 1
            st["steps_total"] += r.steps
            for ft in r.finding_types():
                st["finding_types"][ft] += 1
            if r.status in ("divergence", "timeout", "config_error", "noresult"):
                st["anomalies"] += 1
            if r.status == "failure" and sig_matches(r.signature, target):
                st["failures"] += 1
                st["signatures"][r.signature] += 1
                if st["first_failure_run"] is None:
                    st["first_failure_run"] = i + 1
                    st["first_failure_seed"] = seed
                    if out_dir and keep_first and os.path.exists(sp):
                        os.makedirs(out_dir, exist_ok=True)
                        shutil.copy(sp, os.path.join(out_dir, "failing.sched"))
                        with open(os.path.join(out_dir, "failing.json"), "w") as f:
                            json.dump(r.raw, f, indent=1)
                if stop_on_first:
                    break
    finally:
        st["wall"] = time.time() - t0
        shutil.rmtree(tmp, ignore_errors=True)
    st["signatures"] = dict(st["signatures"])
    st["finding_types"] = dict(st["finding_types"])
    return st


def explore_dfs(prog, args, *, bound=None, max_schedules=100000, stop_on_first=False, target=None,
                fatal=(), spurious=0, timeout=60, seen=None, out_dir=None, watchdog_ms=20000,
                collect_outcomes=False):
    """Stateless depth-first enumeration of schedules (optionally preemption-bounded).

    Every complete schedule is executed once: a prefix of scheduler choices is
    replayed (strategy=prefix) and extended with the non-preemptive base
    policy; each later decision with an unexplored alternative spawns a new
    prefix. With bound=None the whole schedule tree is enumerated.
    """
    tmp = tempfile.mkdtemp(prefix="chronos_dfs_")
    seen = seen if seen is not None else set()
    st = {"strategy": "dfs", "bound": bound, "schedules": 0, "failures": 0, "first_failure_at": None,
          "complete": False, "signatures": collections.Counter(), "anomalies": 0, "wall": 0.0,
          "outcomes": collections.Counter(), "max_preemptions": 0}
    stack = [[]]
    t0 = time.time()
    stopped = False
    try:
        while stack:
            if st["schedules"] >= max_schedules:
                stopped = True
                break
            prefix = stack.pop()
            pp, sp = os.path.join(tmp, "p.txt"), os.path.join(tmp, "s.sched")
            write_prefix(pp, prefix)
            r = run_once(prog, args, strategy="prefix", prefix_in=pp, fatal=fatal, spurious=spurious,
                         timeout=timeout, sched_out=sp, watchdog_ms=watchdog_ms)
            if r.status in ("divergence", "timeout", "config_error", "noresult") or not os.path.exists(sp):
                st["anomalies"] += 1
                continue
            _, steps = parse_schedule(sp)
            tids = tuple(s.tid for s in steps)
            fresh = tids not in seen
            seen.add(tids)
            pre = preemption_prefix_counts(steps)
            if fresh:
                st["schedules"] += 1
                st["max_preemptions"] = max(st["max_preemptions"], pre[-1])
                outcome = r.signature if r.status == "failure" else "ok"
                if collect_outcomes:
                    st["outcomes"][outcome] += 1
                if r.status == "failure" and sig_matches(r.signature, target):
                    st["failures"] += 1
                    st["signatures"][r.signature] += 1
                    if st["first_failure_at"] is None:
                        st["first_failure_at"] = st["schedules"]
                        if out_dir:
                            os.makedirs(out_dir, exist_ok=True)
                            shutil.copy(sp, os.path.join(out_dir, "failing.sched"))
                    if stop_on_first:
                        stopped = True
                        break
            for i in range(len(prefix), len(steps)):
                s = steps[i]
                prev = steps[i - 1].tid if i > 0 else 0
                for alt in bits(s.run):
                    if alt == s.tid:
                        continue
                    p = pre[i] + (1 if ((s.run >> prev) & 1 and alt != prev) else 0)
                    if bound is None or p <= bound:
                        stack.append(list(tids[:i]) + [alt])
        st["complete"] = (not stopped) and (not stack)
    finally:
        st["wall"] = time.time() - t0
        shutil.rmtree(tmp, ignore_errors=True)
    st["signatures"] = dict(st["signatures"])
    st["outcomes"] = dict(st["outcomes"])
    return st



def explore_dpor(prog, args, *, max_schedules=10000, stop_on_first=False, target=None,
                 fatal=(), spurious=0, timeout=60, out_dir=None, watchdog_ms=20000,
                 heuristic="baseline"):
    """Trace-guided DPOR: branch only at runnable alternatives involved in
    dependent, concurrent events. It uses exact replayable prefixes and reports
    whether the search exhausted its frontier within the supplied budget.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import chronos_dpor
    tmp = tempfile.mkdtemp(prefix="chronos_dpor_")
    st = {"strategy": "dpor", "schedules": 0, "failures": 0, "first_failure_at": None,
          "complete": False, "frontier_remaining": 0, "anomalies": 0,
          "backtracks": 0, "dependent_pairs": 0, "wall": 0.0,
          "signatures": collections.Counter(), "finding_types": collections.Counter()}
    stack = [(tuple(), 0)]
    seen_prefixes = {tuple()}
    seen_schedules = set()
    t0 = time.time()
    stopped = False
    try:
        while stack:
            if st["schedules"] >= max_schedules:
                break
            if heuristic == "risk":
                best = max(range(len(stack)), key=lambda idx: (stack[idx][1], -len(stack[idx][0]), tuple(-x for x in stack[idx][0])))
                prefix, _priority = stack.pop(best)
            else:
                prefix, _priority = stack.pop()
            pp = os.path.join(tmp, "prefix.txt")
            sp = os.path.join(tmp, "schedule.sched")
            tp = os.path.join(tmp, "trace.jsonl")
            write_prefix(pp, prefix)
            r = run_once(prog, args, strategy="prefix", prefix_in=pp, fatal=fatal,
                         spurious=spurious, timeout=timeout, sched_out=sp,
                         trace_out=tp, watchdog_ms=watchdog_ms)
            if r.status in ("divergence", "timeout", "config_error", "noresult") or not os.path.exists(sp):
                st["anomalies"] += 1
                continue
            _, steps = parse_schedule(sp)
            tids = tuple(s.tid for s in steps)
            if tids in seen_schedules:
                continue
            seen_schedules.add(tids)
            st["schedules"] += 1
            for finding in r.findings:
                st["finding_types"][finding.get("type", "unknown")] += 1
            if r.status == "failure" and sig_matches(r.signature, target):
                st["failures"] += 1
                st["signatures"][r.signature] += 1
                if st["first_failure_at"] is None:
                    st["first_failure_at"] = st["schedules"]
                    if out_dir:
                        os.makedirs(out_dir, exist_ok=True)
                        shutil.copy2(sp, os.path.join(out_dir, "failing.sched"))
                        shutil.copy2(tp, os.path.join(out_dir, "failing.trace.jsonl"))
                if stop_on_first:
                    stopped = True
                    break
            events = chronos_dpor.read_trace(tp) if os.path.exists(tp) else []
            choices = chronos_dpor.backtracking_choices(events, tids)
            st["dependent_pairs"] += chronos_dpor.dependent_pair_count(events)
            st["backtracks"] += len(choices)
            for branch, priority in choices:
                if branch not in seen_prefixes:
                    seen_prefixes.add(branch)
                    stack.append((branch, priority if heuristic == "risk" else 0))
        st["frontier_remaining"] = len(stack)
        st["complete"] = not stack and not stopped
    finally:
        st["wall"] = time.time() - t0
        shutil.rmtree(tmp, ignore_errors=True)
    st["signatures"] = dict(st["signatures"])
    st["finding_types"] = dict(st["finding_types"])
    st["heuristic"] = heuristic
    return st


def explore_icb(prog, args, *, max_bound=6, max_schedules=20000, target=None, fatal=(), spurious=0,
                timeout=60, out_dir=None):
    """Iterative context (preemption) bounding: bound 0, 1, 2, ... until a failure."""
    seen = set()
    total = 0
    t0 = time.time()
    for b in range(max_bound + 1):
        st = explore_dfs(prog, args, bound=b, max_schedules=max_schedules - total, stop_on_first=True,
                         target=target, fatal=fatal, spurious=spurious, timeout=timeout, seen=seen,
                         out_dir=out_dir)
        total += st["schedules"]
        if st["failures"]:
            return {"strategy": "icb", "found": True, "bound": b, "schedules_to_first_failure": total,
                    "wall": time.time() - t0, "signatures": st["signatures"]}
        if total >= max_schedules:
            break
    return {"strategy": "icb", "found": False, "bound": None, "schedules_to_first_failure": None,
            "schedules": total, "wall": time.time() - t0, "signatures": {}}


# --------------------------------------------------------------------------
# replay / trace / minimisation
# --------------------------------------------------------------------------
def replay(prog, args, sched_path, *, times=1, expect=None, fatal=(), spurious=0, timeout=60,
           trace_out=None):
    """Strictly replay a schedule. Returns per-run verdicts and a reproduction rate."""
    hdr, _ = parse_schedule(sched_path)
    expect = expect if expect is not None else hdr.get("fail")
    matches, divergences, other = 0, 0, 0
    last = None
    for i in range(times):
        r = run_once(prog, args, strategy="replay", schedule_in=sched_path, fatal=fatal,
                     spurious=spurious, timeout=timeout, trace_out=trace_out if i == 0 else None)
        last = r
        if r.status == "divergence":
            divergences += 1
        elif expect is None and r.status == "ok":
            matches += 1
        elif expect is not None and r.status == "failure" and r.signature == expect:
            matches += 1
        else:
            other += 1
    return {"times": times, "matches": matches, "divergences": divergences, "other": other,
            "rate": matches / times if times else 0.0, "expected": expect,
            "last": last.raw if last else None}


def ddmin(items, test):
    """Zeller's delta debugging. `test(subset) -> bool` (True = failure persists)."""
    items = list(items)
    n = 2
    while len(items) >= 2:
        chunk = max(1, len(items) // n)
        subsets = [items[i:i + chunk] for i in range(0, len(items), chunk)]
        reduced = False
        for s in subsets:
            if len(s) < len(items) and test(s):
                items, n, reduced = s, 2, True
                break
        if not reduced:
            for i in range(len(subsets)):
                comp = [x for j, s in enumerate(subsets) if j != i for x in s]
                if comp and len(comp) < len(items) and test(comp):
                    items, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(items):
                break
            n = min(len(items), n * 2)
    return items


def minimize(prog, args, sched_path, out_dir, *, target=None, fatal=(), spurious=0, timeout=60,
             watchdog_ms=20000):
    """Minimise a failing schedule to a 1-minimal set of deviations from the base policy."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hdr, steps = parse_schedule(sched_path)
    target = target or hdr.get("fail")
    orig_ovs = derive_overrides(steps)
    orig_pre = preemption_prefix_counts(steps)[-1]
    orig_sw = sum(1 for i in range(1, len(steps)) if steps[i].tid != steps[i - 1].tid)
    info = {"target": target, "original": {"steps": len(steps), "switches": orig_sw,
                                           "preemptions": orig_pre, "deviations": len(orig_ovs)}}
    tmp = tempfile.mkdtemp(prefix="chronos_min_")
    cache, runs = {}, [0]
    pf = os.path.join(tmp, "o.txt")

    def test(subset):
        key = frozenset(subset)
        if key in cache:
            return cache[key]
        write_overrides(pf, subset)
        r = run_once(prog, args, strategy="preempt", preempt_in=pf, fatal=fatal, spurious=spurious,
                     timeout=timeout, watchdog_ms=watchdog_ms)
        runs[0] += 1
        ok = r.status == "failure" and r.signature == target
        cache[key] = ok
        return ok

    try:
        if not test(orig_ovs):
            info["reproduced_in_override_form"] = False
            info["error"] = "failure does not reproduce from the deviation set; schedule left unminimised"
            return info
        info["reproduced_in_override_form"] = True
        minimal = [] if test([]) else ddmin(orig_ovs, test)
        # verify 1-minimality explicitly
        one_minimal = all(not test([x for x in minimal if x != y]) for y in minimal) if minimal else True
        # materialise the minimal run as a full strict schedule
        write_overrides(str(out_dir / "minimal.preempt"), minimal)
        r = run_once(prog, args, strategy="preempt", preempt_in=str(out_dir / "minimal.preempt"),
                     fatal=fatal, spurious=spurious, timeout=timeout, sched_out=str(out_dir / "minimal.sched"),
                     watchdog_ms=watchdog_ms)
        if not (r.status == "failure" and r.signature == target):
            info["error"] = "internal: minimal deviation set stopped reproducing"
            return info
        _, msteps = parse_schedule(out_dir / "minimal.sched")
        v = replay(prog, args, str(out_dir / "minimal.sched"), times=5, expect=target, fatal=fatal,
                   spurious=spurious, timeout=timeout)
        info["minimal"] = {"steps": len(msteps),
                           "switches": sum(1 for i in range(1, len(msteps)) if msteps[i].tid != msteps[i - 1].tid),
                           "preemptions": preemption_prefix_counts(msteps)[-1],
                           "deviations": len(minimal)}
        info["one_minimal"] = bool(one_minimal)
        info["strict_replay_verified"] = v["matches"] == v["times"]
        info["test_runs"] = runs[0]
        o, m = info["original"], info["minimal"]
        info["switch_reduction"] = (1 - m["switches"] / o["switches"]) if o["switches"] else 0.0
        info["deviation_reduction"] = (1 - m["deviations"] / o["deviations"]) if o["deviations"] else 0.0
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _fatal(s):
    return tuple(x for x in (s or "").split(",") if x)


def cmd_run(a):
    r = run_once(a.program, a.args, strategy=a.strategy, seed=a.seed, pct_depth=a.depth, pct_steps=a.pct_steps,
                 schedule_in=a.schedule_in, preempt_in=a.preempt_in, fatal=_fatal(a.fatal), spurious=a.spurious,
                 sched_out=a.sched_out, trace_out=a.trace_out, timeout=a.timeout)
    print(json.dumps(r.raw or {"status": r.status, "signature": r.signature}, indent=1))
    return 0 if r.ok else 1


def cmd_explore(a):
    fatal = _fatal(a.fatal)
    if a.strategy == "dpor":
        st = explore_dpor(a.program, a.args, max_schedules=a.runs, stop_on_first=a.stop_on_first,
                          target=a.target, fatal=fatal, spurious=a.spurious, out_dir=a.out,
                          heuristic=a.heuristic)
    elif a.strategy == "dfs":
        st = explore_dfs(a.program, a.args, bound=a.bound, max_schedules=a.runs, stop_on_first=a.stop_on_first,
                         target=a.target, fatal=fatal, spurious=a.spurious, out_dir=a.out, collect_outcomes=True)
    elif a.strategy == "icb":
        st = explore_icb(a.program, a.args, max_bound=a.bound or 6, max_schedules=a.runs, target=a.target,
                         fatal=fatal, spurious=a.spurious, out_dir=a.out)
    else:
        st = explore_sampling(a.program, a.args, strategy=a.strategy, runs=a.runs, seed0=a.seed,
                              stop_on_first=a.stop_on_first, target=a.target, fatal=fatal,
                              spurious=a.spurious, out_dir=a.out)
    print(json.dumps(st, indent=1, default=str))
    return 0


def cmd_replay(a):
    v = replay(a.program, a.args, a.schedule, times=a.times, expect=a.expect, fatal=_fatal(a.fatal),
               spurious=a.spurious, trace_out=a.trace_out)
    v.pop("last", None)
    print(json.dumps(v, indent=1))
    return 0 if v["matches"] == v["times"] else 1


def cmd_minimize(a):
    info = minimize(a.program, a.args, a.schedule, a.out, target=a.target, fatal=_fatal(a.fatal),
                    spurious=a.spurious)
    print(json.dumps(info, indent=1))
    return 0 if "minimal" in info else 1


def cmd_trace(a):
    r = run_once(a.program, a.args, strategy="replay", schedule_in=a.schedule, trace_out=a.out,
                 fatal=_fatal(a.fatal), spurious=a.spurious)
    print(f"trace written to {a.out} (status={r.status}, signature={r.signature})")
    return 0


def cmd_viz(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import chronos_viz
    traces = [(Path(t).stem if ":" not in t else t.split(":", 1)[0], t.split(":", 1)[-1]) for t in a.traces]
    chronos_viz.build(traces, a.out, title=a.title)
    print(f"visualiser written to {a.out}")
    return 0


def cmd_bench(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench
    return bench.main(a)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="chronos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("program")
        p.add_argument("args", nargs="*")
        p.add_argument("--fatal", default="", help="comma list: race,lockorder (make detector findings fatal)")
        p.add_argument("--spurious", type=int, default=0, help="spurious cond-var wake-up budget")

    p = sub.add_parser("run"); common(p)
    p.add_argument("--strategy", default="random"); p.add_argument("--seed", type=int, default=1)
    p.add_argument("--depth", type=int, default=3); p.add_argument("--pct-steps", type=int, default=200)
    p.add_argument("--schedule-in"); p.add_argument("--preempt-in")
    p.add_argument("--sched-out"); p.add_argument("--trace-out"); p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("explore"); common(p)
    p.add_argument("--strategy", choices=["random", "pct", "dfs", "icb", "dpor"], default="random")
    p.add_argument("--runs", type=int, default=1000); p.add_argument("--seed", type=int, default=1)
    p.add_argument("--stop-on-first", action="store_true"); p.add_argument("--target")
    p.add_argument("--bound", type=int); p.add_argument("--out")
    p.add_argument("--heuristic", choices=["baseline", "risk"], default="baseline",
                   help="DPOR branch ordering: baseline or risk-guided (write/race-heavy first)")
    p.set_defaults(fn=cmd_explore)

    p = sub.add_parser("replay"); common(p)
    p.add_argument("--schedule", required=True); p.add_argument("--times", type=int, default=1)
    p.add_argument("--expect"); p.add_argument("--trace-out")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("minimize"); common(p)
    p.add_argument("--schedule", required=True); p.add_argument("--out", required=True); p.add_argument("--target")
    p.set_defaults(fn=cmd_minimize)

    p = sub.add_parser("trace"); common(p)
    p.add_argument("--schedule", required=True); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("viz")
    p.add_argument("traces", nargs="+", help="trace.jsonl or label:trace.jsonl")
    p.add_argument("--out", required=True); p.add_argument("--title", default="Chronos trace")
    p.set_defaults(fn=cmd_viz)

    p = sub.add_parser("bench")
    p.add_argument("--manifest", default=str(ROOT / "benchmarks" / "manifest.json"))
    p.add_argument("--out", default=str(ROOT / "results"))
    p.add_argument("--reps", type=int, default=20, help="independent repetitions per strategy")
    p.add_argument("--only", help="comma list of benchmark names")
    p.add_argument("--quick", action="store_true", help="smaller budgets (smoke test)")
    p.set_defaults(fn=cmd_bench)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

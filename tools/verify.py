#!/usr/bin/env python3
"""verify - fresh, staged, evidence-producing verification of Chronos Phase 1.

Rules (the acceptance contract):
  * every stage runs in its own subprocess with its own wall-clock budget;
  * a stage that exceeds its budget is NOT_COMPLETED - never PASSED;
  * a stage that is skipped (missing tool) is SKIPPED - never PASSED;
  * stage results carry the run id of THIS invocation; results from an older run are ignored;
  * the final verdict is PHASE 1 VERIFIED only if every stage is PASSED.

    python3 tools/verify.py --fresh                 # start a new run, execute every stage
    python3 tools/verify.py --resume                # continue the current run (skips stages already PASSED)
    python3 tools/verify.py --stages e2e,asan       # only these stages (same run id)
    python3 tools/verify.py --report                # (re)write VERIFICATION.md from the stage files
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "fuzz"))
import chronos as C          # noqa: E402
import chronos_viz           # noqa: E402

OUT = ROOT / "results" / "verification"
PASSED, FAILED, NOTDONE, SKIPPED = "PASSED", "FAILED", "NOT_COMPLETED", "SKIPPED"

# stage name -> (budget seconds, description)
STAGES = {
    "build":        (240, "clean rebuild of every target, zero warnings, all artefacts present"),
    "protocol":     (400, "scheduler protocol tests (oracle enumeration, stress >1000 runs, invariants)"),
    "primitives":   (300, "every supported primitive, C++ std::thread/mutex/condvar"),
    "failurekinds": (200, "assert / crash / hang / step-limit / unsupported / bad config"),
    "detectors":    (300, "race and lock-order truth tables"),
    "replay":       (500, "byte-identical replay, all mutation classes, 100 replays of every bug"),
    "minimizer":    (400, "ddmin properties, lossless override form, 1-minimality"),
    "explorers":    (500, "every bug found, controls clean, ICB, exhaustive outcome distribution"),
    "parsers":      (200, "valid/malformed parser inputs"),
    "fuzz":         (700, "full parser fuzzing: 2500 mutated inputs per parser under ASan+UBSan"),
    "static":       (300, "strict warnings and gcc -fanalyzer"),
    "asan_sweep":   (900, "runtime rebuilt with ASan+UBSan, sweep over 30 programs"),
    "asan_fail":    (200, "failure paths under ASan+UBSan"),
    "tsan_sweep":   (900, "runtime rebuilt with TSan, sweep over 30 programs"),
    "mutation":     (900, "harness self-check: 12 deliberately broken runtimes must all be caught, the intact one must not"),
    "e2e":          (900, "full pipeline for every bug and control, artefacts checked one by one"),
    "load":         (600, "stress and replay while CPU burners compete for the machine"),
    "aslr":         (200, "replay is independent of address-space randomisation"),
    "viz_dom":      (200, "headless DOM rendering of every generated visualiser page"),
    "bench":        (900, "full benchmark, results checked against the manifest"),
    "repeat":       (1200, "end-to-end pipeline repeated in 3 fresh processes (flakiness check)"),
}
ORDER = list(STAGES)


# ---------------------------------------------------------------------------
def sources_hash():
    h = hashlib.sha256()
    for p in sorted(list((ROOT / "src").glob("*")) + list((ROOT / "include").glob("*")) + list((ROOT / "tools").glob("*.py"))):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def write_stage(run_id, stage, status, detail, **extra):
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"run_id": run_id, "stage": stage, "status": status, "detail": detail, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
           "sources": sources_hash(), **extra}
    (OUT / f"{stage}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def run_cmd(cmd, timeout, env=None, cwd=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd or ROOT)
    return p.returncode, p.stdout + p.stderr


def summary_of(output):
    for line in reversed(output.splitlines()):
        if line.startswith("SUMMARY "):
            return json.loads(line[8:])
    return None


def test_stage(pattern, min_tests):
    """Run test_runner.py -k pattern in a subprocess and parse its SUMMARY line."""
    rc, out = run_cmd([sys.executable, "tests/test_runner.py", "-k", pattern], timeout=10 ** 6)
    s = summary_of(out)
    if s is None:
        return FAILED, "no SUMMARY line: " + out[-400:], {}
    ok = rc == 0 and s["passed"] and s["tests"] >= min_tests and s["skipped"] == 0
    return (PASSED if ok else FAILED), f"{s['tests']} tests, {s['failures']} failures, {s['errors']} errors, {s['skipped']} skipped", {"summary": s, "tail": out[-600:] if not ok else ""}


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def st_build():
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    rc, out = run_cmd(["make", "all"], timeout=220)
    warn = [l for l in out.splitlines() if "warning:" in l or "error:" in l]
    n_bug = len(list((ROOT / "tests/programs/bugs").glob("*.c*")))
    n_ctl = len(list((ROOT / "tests/programs/controls").glob("*.c*")))
    n_unit = len(list((ROOT / "tests/unit").glob("*.c")))
    have = (len(list((ROOT / "build/bugs").iterdir())), len(list((ROOT / "build/controls").iterdir())), len(list((ROOT / "build/unit").iterdir())))
    ok = rc == 0 and not warn and have == (n_bug, n_ctl, n_unit) and (ROOT / "build/libchronos.so").exists() and (ROOT / "build/schedule_check").exists()
    return (PASSED if ok else FAILED), f"rc={rc}, warnings={len(warn)}, binaries bugs/controls/unit={have} expected={(n_bug, n_ctl, n_unit)}", {"warnings": warn[:5]}


def st_protocol():   return test_stage("TestSchedulerProtocol", 8)
def st_primitives(): return test_stage("TestPrimitives", 12)
def st_failure():    return test_stage("TestFailureKinds", 7)
def st_detectors():  return test_stage("TestDetectors", 8)
def st_replay():     return test_stage("TestReplayAndDivergence", 5)
def st_minimizer():  return test_stage("TestMinimizer", 3)
def st_explorers():  return test_stage("TestExplorers", 4)
def st_static():     return test_stage("TestStaticAnalysis", 2)
def st_asan_fail():  return test_stage("test_asan_failure_paths", 1)
def st_tsan():       return test_stage("test_tsan", 1)
def st_asan():       return test_stage("test_asan_ubsan", 1)


def st_parsers():
    return _parsers_valid()


def _parsers_valid():
    s1 = test_stage("test_valid_files_accepted", 1)
    s2 = test_stage("test_malformed_files_rejected_cleanly", 1)
    ok = s1[0] == PASSED and s2[0] == PASSED
    return (PASSED if ok else FAILED), f"valid-files: {s1[1]}; malformed-files: {s2[1]}", {}


def st_fuzz():
    import fuzz_parsers
    total, details = 0, {}
    for kind, corpus in (("sched", "corpus_schedule.txt"), ("over", "corpus_preempt.txt"), ("prefix", "corpus_prefix.txt")):
        seed = (ROOT / "tests/fuzz" / corpus).read_bytes()
        st, bad = fuzz_parsers.run_fuzz(kind, seed, 2500)
        details[kind] = {"accepted": st["accepted"], "rejected": st["rejected"], "bad": len(bad)}
        total += st["accepted"] + st["rejected"] + len(bad)
        if bad:
            return FAILED, f"{kind}: {len(bad)} crashes/timeouts/sanitizer reports, first: {bad[0]}", details
    ok = total == 7500
    return (PASSED if ok else FAILED), f"{total} inputs, 0 crashes, 0 sanitizer reports", details


# ---------------------------------------------------------------------------
# end-to-end verification of one bug
# ---------------------------------------------------------------------------
def compile_prog(src, out):
    cxx = str(src).endswith(".cpp")
    cmd = (["g++", "-std=gnu++17"] if cxx else ["gcc", "-std=gnu11"]) + ["-Wall", "-Wextra", "-O1", "-g", f"-I{ROOT/'include'}", str(src), "-o", str(out), "-pthread"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0 and not p.stderr.strip(), p.stderr


def validate_trace(trace_path, sched_path, expect_sig):
    """The trace must agree with the schedule step by step and be internally consistent."""
    checks = {}
    hdr, ev, res = None, [], None
    for line in open(trace_path):
        o = json.loads(line)
        if "chronos_trace" in o: hdr = o
        elif "end" in o: res = o["result"]
        else: ev.append(o)
    _, steps = C.parse_schedule(sched_path)
    checks["trace parses with header, events and result"] = hdr is not None and res is not None and len(ev) > 0
    checks["header.steps == events == schedule steps"] = hdr["steps"] == len(ev) == len(steps)
    checks["event (thread, op, op-index) equals schedule"] = all(e["t"] == s.tid and e["op"] == s.op and e["k"] == s.opidx for e, s in zip(ev, steps))
    checks["runnable sets equal schedule"] = all(int(e["run"], 16) == s.run for e, s in zip(ev, steps))
    per = {}
    ok_k = True
    for e in ev:
        if per.get(e["t"], -1) + 1 != e["k"]: ok_k = False
        per[e["t"]] = e["k"]
    checks["per-thread op indices are contiguous from 0"] = ok_k
    checks["first event of every non-main thread is 'start'"] = all(next(e for e in ev if e["t"] == t)["op"] == "start" for t in per if t != 0)
    checks["happens-before sources precede the event"] = all(all(0 <= h < e["i"] for h in e.get("hb", [])) for e in ev)
    checks["every thread count matches header"] = len(per) == hdr["threads"]
    checks["trace result carries the expected signature"] = (res["failure"]["signature"] == expect_sig) if expect_sig else res["status"] == "ok"
    return checks


def independent_one_minimal(prog, args, min_preempt, target):
    """Re-derive 1-minimality without using minimize()'s own bookkeeping."""
    lines = [l.split() for l in open(min_preempt).read().splitlines() if l and not l.startswith(("chronos", "end"))]
    ovs = [tuple(map(int, l)) for l in lines]
    tmp = tempfile.mkdtemp(prefix="vf_min_")
    try:
        def fails(subset):
            p = os.path.join(tmp, "o.txt")
            C.write_overrides(p, subset)
            r = C.run_once(prog, args, strategy="preempt", preempt_in=p)
            return r.status == "failure" and r.signature == target
        full = fails(ovs)
        each = [fails([o for o in ovs if o != x]) for x in ovs]
        return full and not any(each), len(ovs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def e2e_bug(case, work, seed_offset=0):
    name = case["name"]
    src = next((ROOT / "tests/programs/bugs").glob(name + ".c*"))
    checks, info = {}, {"name": name}
    exe = work / name
    ok, err = compile_prog(src, exe)
    checks["compile (fresh, warning-free)"] = ok
    if not ok:
        return checks, {**info, "compile_error": err[:300]}
    args = case.get("args", [])
    # explore: random first, ICB fallback
    st = C.explore_sampling(str(exe), args, runs=6000, seed0=1 + seed_offset, stop_on_first=True, target=case["oracle"], out_dir=str(work / (name + "_x")))
    method = "random"
    if st["first_failure_run"] is None:
        icb = C.explore_icb(str(exe), args, max_bound=5, max_schedules=6000, target=case["oracle"], out_dir=str(work / (name + "_x")))
        method = "icb" if icb["found"] else None
        info["icb"] = icb
    checks["explore finds the bug"] = method is not None
    if method is None:
        return checks, info
    info["explore"] = {"method": method, "runs_to_first": st.get("first_failure_run"), "seed": st.get("first_failure_seed")}
    sched = work / (name + "_x") / "failing.sched"
    hdr, steps = C.parse_schedule(sched)
    sig = hdr["fail"]
    checks["failure signature matches the oracle"] = sig.startswith(case["oracle"])
    checks["schedule saved and parseable (chosen thread always runnable)"] = len(steps) > 0 and all((s.run >> s.tid) & 1 for s in steps)
    # replay deterministically
    tf = work / (name + ".failing.trace.jsonl")
    rp = C.replay(str(exe), args, str(sched), times=100, trace_out=str(tf))
    checks["replay 100/100, zero divergence, same signature"] = rp["matches"] == 100 and rp["divergences"] == 0 and rp["other"] == 0
    same = True
    for i in range(5):
        out = work / f"{name}.re{i}.sched"
        r = C.run_once(str(exe), args, strategy="replay", schedule_in=str(sched), sched_out=str(out))
        same &= (out.read_text().split("steps ", 1)[1] == sched.read_text().split("steps ", 1)[1])
    checks["replay regenerates the identical schedule (5 runs)"] = same
    # minimize
    mn = C.minimize(str(exe), args, str(sched), str(work / (name + "_min")), target=sig)
    checks["minimizer produced a minimal schedule"] = "minimal" in mn
    if "minimal" not in mn:
        return checks, {**info, "minimize": mn}
    info["minimize"] = mn
    checks["minimizer reports 1-minimal and strict-replay verified"] = bool(mn["one_minimal"] and mn["strict_replay_verified"])
    minimal = work / (name + "_min") / "minimal.sched"
    mpre = work / (name + "_min") / "minimal.preempt"
    one_min, n_ov = independent_one_minimal(str(exe), args, mpre, sig)
    checks["1-minimality re-verified independently"] = one_min
    _, msteps = C.parse_schedule(minimal)
    checks["minimal override set == deviations of the minimal schedule"] = sorted(C.derive_overrides(msteps)) == sorted(tuple(map(int, l.split())) for l in open(mpre).read().splitlines() if l and not l.startswith(("chronos", "end")))
    checks["minimal not larger than original (deviations)"] = n_ov <= mn["original"]["deviations"]
    rpm = C.replay(str(exe), args, str(minimal), times=100, expect=sig)
    checks["minimal replay 100/100 with the original signature"] = rpm["matches"] == 100 and rpm["divergences"] == 0
    # traces
    tm = work / (name + ".minimal.trace.jsonl")
    C.run_once(str(exe), args, strategy="replay", schedule_in=str(minimal), trace_out=str(tm))
    for label, tr, sc in (("original", tf, sched), ("minimal", tm, minimal)):
        for k, v in validate_trace(tr, sc, sig).items():
            checks[f"trace[{label}]: {k}"] = v
    # visualiser
    html = work / (name + ".viz.html")
    chronos_viz.build([("original", str(tf)), ("minimized", str(tm))], str(html), title=f"verify {name}")
    page = html.read_text()
    checks["visualiser page has no external resource references"] = "http://" not in page.replace("http://www.w3.org/2000/svg", "") and 'src="http' not in page
    blob = json.loads(page.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0].replace("<\\/", "</"))
    checks["visualiser data equals both traces"] = [len(t["events"]) for t in blob["traces"]] == [len(steps), len(msteps)] and \
        [e["t"] for e in blob["traces"][0]["events"]] == [s.tid for s in steps]
    info["html"] = str(html)
    info["steps"] = [len(steps), len(msteps)]
    info["expect_sig"] = sig
    return checks, info


def e2e_control(case, work):
    name = case["name"]
    src = next((ROOT / "tests/programs/controls").glob(name + ".c*"))
    checks, exe = {}, work / name
    ok, err = compile_prog(src, exe)
    checks["compile (fresh, warning-free)"] = ok
    bad = 0
    for s in range(1, 31):
        r = C.run_once(str(exe), [], seed=s, fatal=("race", "lockorder"), spurious=case.get("spurious", 0), sched_out=str(work / f"{name}.s"), trace_out=str(work / f"{name}.t") if s == 1 else None)
        bad += r.status != "ok" or bool(r.findings)
    checks["30 schedules: ok, no race/lock-order finding"] = bad == 0
    r = C.run_once(str(exe), [], seed=7, sched_out=str(work / f"{name}.a.sched"))
    r2 = C.run_once(str(exe), [], strategy="replay", schedule_in=str(work / f"{name}.a.sched"), sched_out=str(work / f"{name}.b.sched"))
    checks["replay of a passing run is identical"] = r2.status == "ok" and (work / f"{name}.a.sched").read_text().split("steps ", 1)[1] == (work / f"{name}.b.sched").read_text().split("steps ", 1)[1]
    C.run_once(str(exe), [], strategy="replay", schedule_in=str(work / f"{name}.a.sched"), trace_out=str(work / f"{name}.trace.jsonl"))
    for k, v in validate_trace(work / f"{name}.trace.jsonl", work / f"{name}.a.sched", None).items():
        checks[f"trace: {k}"] = v
    chronos_viz.build([("run", str(work / f"{name}.trace.jsonl"))], str(work / f"{name}.viz.html"))
    return checks, {"name": name, "html": str(work / f"{name}.viz.html"), "steps": [r.steps]}


def run_e2e(seed_offset=0, keep=None):
    manifest = json.loads((ROOT / "benchmarks/manifest.json").read_text())
    work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="chronos_e2e_"))
    work.mkdir(parents=True, exist_ok=True)
    allres, failed = {}, []
    for case in manifest["bugs"]:
        checks, info = e2e_bug(case, work, seed_offset)
        allres[case["name"]] = {"checks": checks, "info": info}
        failed += [f"{case['name']}: {k}" for k, v in checks.items() if not v]
    for case in manifest["controls"]:
        checks, info = e2e_control(case, work)
        allres["control:" + case["name"]] = {"checks": checks, "info": info}
        failed += [f"control {case['name']}: {k}" for k, v in checks.items() if not v]
    n_checks = sum(len(v["checks"]) for v in allres.values())
    return allres, failed, n_checks, work


def st_e2e():
    allres, failed, n, work = run_e2e()
    OUT.mkdir(parents=True, exist_ok=True)
    keep = OUT / "e2e_artifacts"
    shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir()
    for name, r in allres.items():
        h = r["info"].get("html")
        if h and Path(h).exists():
            shutil.copy(h, keep / Path(h).name)
    (OUT / "e2e_checks.json").write_text(json.dumps({k: v["checks"] for k, v in allres.items()}, indent=1))
    per_bug = {k: (sum(v["checks"].values()), len(v["checks"])) for k, v in allres.items()}
    return (PASSED if not failed else FAILED), f"{n - len(failed)}/{n} checks passed across {len(allres)} programs" + (f"; FAILED: {failed[:5]}" if failed else ""), {"per_program": per_bug, "failed": failed}


def st_repeat():
    """Three fresh processes, different exploration seeds; every check must pass each time."""
    results = []
    for i in range(3):
        rc, out = run_cmd([sys.executable, "tools/verify.py", "--e2e-once", str(1000 * (i + 1))], timeout=380)
        line = [l for l in out.splitlines() if l.startswith("E2E_RESULT ")]
        if not line:
            return FAILED, f"repeat {i}: no result: {out[-300:]}", {}
        r = json.loads(line[-1][11:])
        results.append(r)
        if r["failed"]:
            return FAILED, f"repeat {i} failed: {r['failed'][:3]}", {"results": results}
    return PASSED, "3/3 fresh end-to-end runs passed (" + ", ".join(f"{r['checks']} checks" for r in results) + ")", {"results": results}


def st_load():
    """Protocol stress, replay and a few bugs while CPU burners compete (provokes timing-dependent flakiness)."""
    burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(4)]
    try:
        bad, runs = [], 0
        for kind, name, args in (("unit", "u_stress", []), ("controls", "prodcons_cv", []), ("controls", "semaphore_lock", []), ("controls", "barrier_phases", [])):
            for strat in ("random", "pct"):
                for s in range(1, 41):
                    r = C.run_once(str(ROOT / "build" / kind / name), args, strategy=strat, seed=s, pct_steps=120, timeout=120)
                    runs += 1
                    if r.status != "ok":
                        bad.append((name, strat, s, r.status, r.signature))
        for name in ("lost_update", "atomicity_violation", "cond_if_not_while", "dining_philosophers", "lost_wakeup"):
            exe = str(ROOT / "build/bugs" / name)
            td = tempfile.mkdtemp()
            case = next(c for c in json.loads((ROOT / "benchmarks/manifest.json").read_text())["bugs"] if c["name"] == name)
            st = C.explore_sampling(exe, [], runs=6000, stop_on_first=True, target=case["oracle"], out_dir=td, timeout=120)
            if st["first_failure_run"] is None:
                bad.append((name, "not found under load"))
                continue
            v = C.replay(exe, [], os.path.join(td, "failing.sched"), times=40, timeout=120)
            runs += 40
            if v["matches"] != 40:
                bad.append((name, "replay under load", v["matches"]))
            shutil.rmtree(td, ignore_errors=True)
        return (PASSED if not bad else FAILED), f"{runs} runs with 4 CPU burners competing; anomalies: {len(bad)} {bad[:3]}", {"anomalies": bad}
    finally:
        for b in burners:
            b.kill()


def st_aslr():
    """Schedules must not depend on addresses: replay with ASLR on and off gives identical results."""
    if not shutil.which("setarch"):
        return SKIPPED, "setarch not available", {}
    bad = []
    for name in ("lost_update", "dining_philosophers", "lost_wakeup", "double_checked_init"):
        exe = str(ROOT / "build/bugs" / name)
        td = tempfile.mkdtemp()
        st = C.explore_sampling(exe, [], runs=4000, stop_on_first=True, out_dir=td, target=None)
        if st["first_failure_run"] is None:
            bad.append((name, "no failure")); continue
        sched = os.path.join(td, "failing.sched")
        hdr, _ = C.parse_schedule(sched)
        for noaslr in (True, False):
            for _ in range(10):
                env = dict(os.environ, LD_PRELOAD=str(C.LIB), CHRONOS_STRATEGY="replay", CHRONOS_SCHEDULE_IN=sched, CHRONOS_QUIET="1",
                           CHRONOS_RESULT_OUT=os.path.join(td, "r.json"))
                cmd = ([ "setarch", platform.machine(), "-R"] if noaslr else []) + [exe]
                if os.path.exists(os.path.join(td, "r.json")): os.remove(os.path.join(td, "r.json"))
                subprocess.run(cmd, env=env, capture_output=True, timeout=60)
                r = json.load(open(os.path.join(td, "r.json")))
                if not (r["status"] == "failure" and r["failure"]["signature"] == hdr["fail"]):
                    bad.append((name, noaslr, r["status"]))
        shutil.rmtree(td, ignore_errors=True)
    return (PASSED if not bad else FAILED), f"replay identical with ASLR on/off for 4 bugs x 10 runs; mismatches: {len(bad)}", {"bad": bad}


def jsdom_available():
    for c in (os.environ.get("JSDOM_PATH"), str(ROOT / "tools/node_modules/jsdom"), "/tmp/vz/node_modules/jsdom"):
        if c and Path(c).exists():
            return c
    return None


def st_viz_dom():
    vt = test_stage("TestVisualizer", 2)
    if vt[0] != PASSED:
        return FAILED, "TestVisualizer: " + vt[1], {}
    node = shutil.which("node")
    jd = jsdom_available()
    if not node or not jd:
        return SKIPPED, "node or jsdom not available (npm install jsdom --prefix tools); NOT counted as passed", {}
    keep = OUT / "e2e_artifacts"
    if not keep.exists() or not list(keep.glob("*.viz.html")):
        return NOTDONE, "run the e2e stage first (it produces the pages)", {}
    results, bad = {}, []
    checks = json.loads((OUT / "e2e_checks.json").read_text()) if (OUT / "e2e_checks.json").exists() else {}
    manifest = json.loads((ROOT / "benchmarks/manifest.json").read_text())
    for case in manifest["bugs"]:
        page = keep / f"{case['name']}.viz.html"
        blob = json.loads(page.read_text().split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0].replace("<\\/", "</"))
        exp = {"steps": [len(t["events"]) for t in blob["traces"]], "tabs": 2, "signature": blob["traces"][0]["result"]["failure"]["signature"]}
        p = subprocess.run([node, str(ROOT / "tests/viz_dom_check.js"), str(page), json.dumps(exp)], capture_output=True, text=True, env=dict(os.environ, JSDOM_PATH=jd), timeout=60)
        results[case["name"]] = p.returncode == 0
        if p.returncode != 0:
            bad.append((case["name"], p.stdout[-300:] + p.stderr[-200:]))
    for case in manifest["controls"]:
        page = keep / f"{case['name']}.viz.html"
        blob = json.loads(page.read_text().split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0].replace("<\\/", "</"))
        exp = {"steps": [len(t["events"]) for t in blob["traces"]], "tabs": 0, "signature": None}
        p = subprocess.run([node, str(ROOT / "tests/viz_dom_check.js"), str(page), json.dumps(exp)], capture_output=True, text=True, env=dict(os.environ, JSDOM_PATH=jd), timeout=60)
        results["control:" + case["name"]] = p.returncode == 0
        if p.returncode != 0:
            bad.append((case["name"], p.stdout[-300:] + p.stderr[-200:]))
    return (PASSED if not bad else FAILED), f"{sum(results.values())}/{len(results)} pages render correctly in a headless DOM", {"bad": bad}


def st_bench():
    rc, out = run_cmd([sys.executable, "tools/chronos.py", "bench", "--reps", "20", "--out", str(ROOT / "results")], timeout=850)
    sj = ROOT / "results/summary.json"
    if rc != 0 or not sj.exists():
        return FAILED, f"bench rc={rc}: {out[-300:]}", {}
    S = json.loads(sj.read_text())
    manifest = json.loads((ROOT / "benchmarks/manifest.json").read_text())
    names_b = {b["name"] for b in S["bugs"]}
    names_c = {c["name"] for c in S["controls"]}
    problems = []
    if names_b != {b["name"] for b in manifest["bugs"]}: problems.append("bug set differs from manifest")
    if names_c != {c["name"] for c in manifest["controls"]}: problems.append("control set differs from manifest")
    for b in S["bugs"]:
        if not all(b["checks"].values()): problems.append(f"{b['name']}: {[k for k, v in b['checks'].items() if not v]}")
        if not b.get("detector_ok"): problems.append(f"{b['name']}: detector expectation")
        if not (ROOT / "results/bugs" / b["name"] / "viz.html").exists(): problems.append(f"{b['name']}: viz.html missing")
    for c in S["controls"]:
        if not c["pass"]: problems.append(f"control {c['name']}")
    return (PASSED if not problems and S["all_passed"] else FAILED), f"{len(S['bugs'])} bugs + {len(S['controls'])} controls checked against the manifest; problems: {problems or 'none'}", {"problems": problems}


# ---------------------------------------------------------------------------
# mutation self-check: does the verification have teeth?
# ---------------------------------------------------------------------------
MUTANTS = [
    ("replay ignores runnable-set mismatch", "rt_core.c", "if (r->run != mask)", "if (0 && r->run != mask)", "replay_checks"),
    ("replay ignores operation mismatch", "rt_core.c", "if (t->pend_op != r->op)", "if (0 && t->pend_op != r->op)", "replay_checks"),
    ("replay ignores object mismatch", "rt_core.c", "if (oid != r->obj)", "if (0 && oid != r->obj)", "replay_checks"),
    ("replay ignores op-index mismatch", "rt_core.c", "if (t->opcnt != r->opidx)", "if (0 && t->opcnt != r->opidx)", "replay_checks"),
    ("recorded schedule names the wrong thread", "rt_core.c", "s->tid = (int16_t)pick;", "s->tid = (int16_t)(pick ^ (g_nsteps == 3 ? 1 : 0));", "replay_identity"),
    ("mutex lock always enabled (mutual exclusion lost)", "rt_core.c", "if (m->u.mtx.owner < 0) return 1;", "return 1;", "controls"),
    ("semaphore wait always enabled", "rt_core.c", "case OP_SEM_WAIT: return t->pend_obj->u.sem.count > 0;", "case OP_SEM_WAIT: return 1;", "sem"),
    ("mutex release no longer carries happens-before", "rt_core.c", "  ch_vc_join(o->vc, t->vc);\n  t->vc[t->tid]++;", "  t->vc[t->tid]++;", "false_race"),
    ("race detector believes every access is ordered", "rt_race.c", "return e.clk == 0 || e.clk <= t->vc[e.tid];", "return 1;", "missed_race"),
    ("lock-order detector disabled", "rt_lockorder.c", "  int to = node_of(m->id);", "  ch_lo_push(t, m); return;\n  int to = node_of(m->id);", "missed_lockorder"),
    ("deadlock detection removed", "rt_core.c", "if (mask == 0) build_deadlock_failure();", "", "deadlock"),
    ("minimiser returns its input unchanged", None, None, None, "ddmin_noop"),
]


def build_variant(src_root, outdir, fname=None, old=None, new=None):
    d = Path(outdir)
    shutil.copytree(src_root / "src", d / "src")
    if fname:
        f = d / "src" / fname
        t = f.read_text()
        if old not in t:
            raise RuntimeError(f"mutation anchor not found in {fname}: {old!r}")
        f.write_text(t.replace(old, new, 1))
    srcs = [str(d / "src" / x) for x in ("rt_core.c", "rt_sync.c", "rt_race.c", "rt_lockorder.c", "rt_out.c", "rt_schedfile.c")]
    p = subprocess.run(["gcc", "-std=gnu11", "-O1", "-fPIC", "-shared", "-pthread", "-ldl", *srcs, "-o", str(d / "libchronos.so")], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("variant does not compile: " + p.stderr[:300])
    return str(d / "libchronos.so")


def _corrupt(text, kind, i):
    lines = text.split("\n")
    h = next(k for k, l in enumerate(lines) if l.startswith("steps "))
    li = h + 1 + i
    f = lines[li].split()
    if kind == "opidx": f[2] = str(int(f[2]) + 1)
    elif kind == "op": f[3] = "yield" if f[3] != "yield" else "write"
    elif kind == "obj": f[4] = str(int(f[4]) + 1)
    else:
        m = int(f[5], 16)
        f[5] = format(m | (1 << next(b for b in range(8) if not (m >> b) & 1)), "x")
    lines[li] = " ".join(f)
    return "\n".join(lines)


def detectors_for(lib, td):
    """Return {detector_name: survived?} - True means the variant behaved correctly for that check."""
    B = ROOT / "build"
    out = {}
    base = os.path.join(td, "base.sched")
    C.run_once(str(B / "controls/prodcons_cv"), [], seed=5, sched_out=base, lib=lib)
    text = open(base).read()
    n = int(next(l for l in text.split("\n") if l.startswith("steps ")).split()[1])
    ok = True
    for kind in ("runmask", "op", "obj", "opidx"):
        for i in (3, n // 2, n - 2):
            q = os.path.join(td, "c.sched")
            open(q, "w").write(_corrupt(text, kind, i))
            r = C.run_once(str(B / "controls/prodcons_cv"), [], strategy="replay", schedule_in=q, lib=lib)
            ok &= r.status == "divergence"
    out["replay_checks"] = ok
    b2 = os.path.join(td, "b2.sched")
    r1 = C.run_once(str(B / "controls/prodcons_cv"), [], seed=9, sched_out=os.path.join(td, "a.sched"), lib=lib)
    r2 = C.run_once(str(B / "controls/prodcons_cv"), [], strategy="replay", schedule_in=os.path.join(td, "a.sched"), sched_out=b2, lib=lib)
    out["replay_identity"] = r2.status == "ok" and open(os.path.join(td, "a.sched")).read().split("steps ", 1)[1] == open(b2).read().split("steps ", 1)[1]
    out["controls"] = all(C.run_once(str(B / "controls/counter_mutex"), [], seed=s, fatal=("race", "lockorder"), lib=lib).status == "ok" for s in range(1, 61))
    out["sem"] = all(C.run_once(str(B / "unit/u_sem_semantics"), [], seed=s, lib=lib).status == "ok" for s in range(1, 31))
    out["false_race"] = all(not C.run_once(str(B / "unit/u_race_modes"), ["mutex"], seed=s, lib=lib).findings for s in range(1, 11))
    out["missed_race"] = all("RACE" in C.run_once(str(B / "unit/u_race_modes"), ["ww"], seed=s, lib=lib).finding_types() for s in range(1, 6))
    out["missed_lockorder"] = "LOCKORDER" in C.run_once(str(B / "unit/u_lockorder_modes"), ["abba"], strategy="base", lib=lib).finding_types()
    out["deadlock"] = C.run_once(str(B / "unit/u_selfdeadlock"), [], lib=lib, timeout=20).signature == "DEADLOCK[mutex_lock]"
    return out


def st_mutation():
    results, problems = {}, []
    with tempfile.TemporaryDirectory(prefix="vf_mut_") as root:
        lib = build_variant(ROOT, os.path.join(root, "intact"))
        td = os.path.join(root, "t0"); os.mkdir(td)
        base = detectors_for(lib, td)
        results["INTACT runtime"] = base
        if not all(base.values()):
            return FAILED, f"the intact runtime fails its own checks: {[k for k, v in base.items() if not v]}", {"results": results}
        for k, (name, fname, old, new, killer) in enumerate(MUTANTS, 1):
            if fname is None:      # python-side mutant: the minimiser does nothing
                orig = C.ddmin
                C.ddmin = lambda items, test: list(items)
                try:
                    cs, _ = e2e_bug(next(c for c in json.loads((ROOT / "benchmarks/manifest.json").read_text())["bugs"] if c["name"] == "lost_update"), Path(root))
                finally:
                    C.ddmin = orig
                killed = not all(cs.values())
                results[name] = {"killed_by": "independent 1-minimality check" if not cs.get("1-minimality re-verified independently", True) else [kk for kk, v in cs.items() if not v][:2]}
            else:
                try:
                    mlib = build_variant(ROOT, os.path.join(root, f"m{k}"), fname, old, new)
                except RuntimeError as e:
                    problems.append(f"{name}: {e}")
                    continue
                td = os.path.join(root, f"t{k}"); os.mkdir(td)
                d = detectors_for(mlib, td)
                killed = not d[killer]
                results[name] = {"expected_killer": killer, "killed": killed, "all_checks": d}
            if not killed:
                problems.append(f"SURVIVED: {name}")
    n = len(MUTANTS)
    return (PASSED if not problems else FAILED), f"intact runtime passes all checks; {n - len([p for p in problems if p.startswith('SURVIVED')])}/{n} deliberately broken runtimes were caught" + (f"; problems: {problems}" if problems else ""), {"results": results}


FUNCS = {"build": st_build, "protocol": st_protocol, "primitives": st_primitives, "failurekinds": st_failure, "detectors": st_detectors,
         "replay": st_replay, "minimizer": st_minimizer, "explorers": st_explorers, "parsers": st_parsers, "fuzz": st_fuzz, "static": st_static,
         "asan_sweep": st_asan, "asan_fail": st_asan_fail, "tsan_sweep": st_tsan, "e2e": st_e2e, "mutation": st_mutation, "load": st_load, "aslr": st_aslr,
         "viz_dom": st_viz_dom, "bench": st_bench, "repeat": st_repeat}


# ---------------------------------------------------------------------------
def read_stage(stage):
    p = OUT / f"{stage}.json"
    return json.loads(p.read_text()) if p.exists() else None


def write_report(run_id):
    rows, verdict_ok = [], True
    for st in ORDER:
        r = read_stage(st)
        if r is None or r["run_id"] != run_id:
            status, detail, secs = NOTDONE, "not run in this verification run", "-"
        else:
            status, detail, secs = r["status"], r["detail"], r.get("seconds", "-")
        if status != PASSED:
            verdict_ok = False
        rows.append((st, status, detail, secs))
    meta = json.loads((OUT / "RUN.json").read_text())
    md = ["# Chronos Phase 1 - verification report", "",
          f"Run id `{run_id}` - started {meta['started']} - sources `{meta['sources']}` - {platform.platform()} - {os.cpu_count()} CPU", "",
          "Status legend: **PASSED** (completed, all assertions held) - **FAILED** (completed, something is wrong) - "
          "**NOT_COMPLETED** (timeout or not executed in this run: never a pass) - **SKIPPED** (prerequisite missing: never a pass).", "",
          "| stage | status | evidence | seconds |", "|---|---|---|---|"]
    for st, status, detail, secs in rows:
        md.append(f"| {st} | **{status}** | {detail[:230].replace('|', '/')} | {secs} |")
    n = {s: sum(1 for r in rows if r[1] == s) for s in (PASSED, FAILED, NOTDONE, SKIPPED)}
    md += ["", f"Totals: {n[PASSED]} passed, {n[FAILED]} failed, {n[NOTDONE]} not completed, {n[SKIPPED]} skipped.", "",
           f"## Verdict: {'PHASE 1 VERIFIED' if verdict_ok else 'PHASE 1 NOT YET VERIFIED'}", ""]
    if (OUT / "e2e_checks.json").exists():
        e = json.loads((OUT / "e2e_checks.json").read_text())
        md += ["## End-to-end checks per program (this run)", "", "| program | checks passed |", "|---|---|"]
        md += [f"| {k} | {sum(v.values())}/{len(v)} |" for k, v in e.items()]
    (OUT / "VERIFICATION.md").write_text("\n".join(md) + "\n")
    return verdict_ok, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--stages")
    ap.add_argument("--stage-once", help="internal: run one stage in this process")
    ap.add_argument("--e2e-once", type=int, help="internal: one end-to-end run with a seed offset")
    ap.add_argument("--run-id")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.e2e_once is not None:
        allres, failed, n, work = run_e2e(a.e2e_once)
        print("E2E_RESULT " + json.dumps({"checks": n, "failed": failed}))
        shutil.rmtree(work, ignore_errors=True)
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    if a.stage_once:
        t0 = time.time()
        status, detail, extra = FUNCS[a.stage_once]()
        write_stage(a.run_id, a.stage_once, status, detail, seconds=round(time.time() - t0, 1), **{"data": extra})
        print(f"{a.stage_once}: {status} - {detail}")
        return 0 if status == PASSED else 1
    runfile = OUT / "RUN.json"
    if a.fresh or not runfile.exists():
        for f in OUT.glob("*.json"):
            f.unlink()
        shutil.rmtree(OUT / "logs", ignore_errors=True)
        runfile.write_text(json.dumps({"run_id": uuid.uuid4().hex[:12], "started": time.strftime("%Y-%m-%d %H:%M:%S"), "sources": sources_hash()}))
    meta = json.loads(runfile.read_text())
    run_id = meta["run_id"]
    if a.report:
        ok, _ = write_report(run_id)
        print(open(OUT / "VERIFICATION.md").read())
        return 0 if ok else 1
    (OUT / "logs").mkdir(exist_ok=True)
    wanted = a.stages.split(",") if a.stages else ORDER
    for st in wanted:
        prev = read_stage(st)
        if a.resume and prev and prev["run_id"] == run_id and prev["status"] == PASSED:
            print(f"[{st}] already PASSED in this run - skipped re-execution")
            continue
        budget = STAGES[st][0]
        t0 = time.time()
        print(f"[{st}] budget {budget}s - {STAGES[st][1]}", flush=True)
        try:
            p = subprocess.run([sys.executable, "tools/verify.py", "--stage-once", st, "--run-id", run_id], cwd=ROOT, capture_output=True, text=True, timeout=budget)
            (OUT / "logs" / f"{st}.log").write_text(p.stdout + "\n" + p.stderr)
            if not (OUT / f"{st}.json").exists() or read_stage(st)["run_id"] != run_id:
                write_stage(run_id, st, FAILED, f"stage crashed without a result: {(p.stderr or p.stdout)[-300:]}", seconds=round(time.time() - t0, 1))
        except subprocess.TimeoutExpired:
            write_stage(run_id, st, NOTDONE, f"exceeded its {budget}s budget (timeout is NOT a pass)", seconds=round(time.time() - t0, 1))
        r = read_stage(st)
        print(f"[{st}] {r['status']} - {r['detail'][:200]}", flush=True)
        write_report(run_id)
    ok, _ = write_report(run_id)
    print("\nVERDICT:", "PHASE 1 VERIFIED" if ok else "PHASE 1 NOT YET VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

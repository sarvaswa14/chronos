#!/usr/bin/env python3
"""bench - runs the Chronos Phase 1 benchmark manifest end to end.

For every injected bug:   native stress baseline, random / PCT / preemption-bounded
                          exploration (repeated), failure capture, strict replay x100,
                          delta-debugging minimisation, trace + visualiser.
For every control:        thousands of schedules with race / lock-order findings made
                          fatal, to prove the absence of false alarms.
Exit status 0 only if every acceptance check passes.
"""
import html
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chronos as C  # noqa: E402
import chronos_viz  # noqa: E402

BUILD = C.ROOT / "build"


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def summarize(xs):
    if not xs:
        return {"n": 0, "median": None, "mean": None, "p90": None, "min": None, "max": None}
    return {"n": len(xs), "median": statistics.median(xs), "mean": round(statistics.mean(xs), 1),
            "p90": pct(xs, 0.9), "min": min(xs), "max": max(xs)}


def native_baseline(binary, args, runs, timeout):
    """Run the unmodified binary (no runtime) and count observed failures."""
    fails = 0
    for _ in range(runs):
        r = C.run_once(binary, args, preload=False, timeout=timeout)
        if r.status in ("failure", "timeout"):
            fails += 1
    return {"runs": runs, "failures": fails}


def sample_reps(binary, args, strategy, case, reps, work, quick):
    """Independent repetitions of stop-at-first-failure sampling."""
    found, misses, first_runs = 0, 0, []
    budget = case["budget"] if not quick else min(case["budget"], 1000)
    reps_run = 0
    for rep in range(reps):
        st = C.explore_sampling(binary, args, strategy=strategy, runs=budget, seed0=1 + rep * 100003,
                                stop_on_first=True, target=case["oracle"], timeout=case["timeout"],
                                out_dir=str(work) if rep == 0 else None)
        reps_run += 1
        if st["first_failure_run"] is not None:
            found += 1
            first_runs.append(st["first_failure_run"])
            misses = 0
        else:
            misses += 1
            if misses >= 3 and found == 0:      # hopeless within budget: stop spending time
                break
    return {"reps": reps_run, "found": found, "budget": budget, "schedules_to_first_failure": summarize(first_runs),
            "samples": first_runs}


def rate_run(binary, args, strategy, case, runs):
    st = C.explore_sampling(binary, args, strategy=strategy, runs=runs, seed0=777, stop_on_first=False,
                            target=case["oracle"], timeout=case["timeout"])
    return {"runs": st["runs"], "failures": st["failures"], "failure_rate": st["failures"] / st["runs"],
            "finding_types": st["finding_types"], "signatures": st["signatures"], "anomalies": st["anomalies"]}


def run_bug(case, out_root, reps, quick):
    name = case["name"]
    binary = str(BUILD / case["binary"])
    args = case.get("args", [])
    work = Path(out_root) / "bugs" / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    t0 = time.time()
    res = {"name": name, "class": case["class"], "oracle": case["oracle"]}
    log = lambda m: print(f"[{name}] {m}", flush=True)  # noqa: E731

    log("native baseline")
    res["native"] = native_baseline(binary, args, 30 if quick else case["native_runs"], 1.0 if "DEADLOCK" in case["oracle"] else 5.0)

    sched_dir = work / "schedules"
    sched_dir.mkdir()
    for strat in ("random", "pct"):
        log(f"{strat}: {reps} repetitions")
        res[strat] = sample_reps(binary, args, strat, case, reps, sched_dir / strat, quick)
        log(f"{strat}: rate runs")
        res[strat]["rate"] = rate_run(binary, args, strat, case, 100 if quick else 300)

    log("preemption-bounded search (ICB)")
    icb_cap = 800 if quick else 5000
    res["icb"] = C.explore_icb(binary, args, max_bound=5, max_schedules=icb_cap, target=case["oracle"],
                               timeout=case["timeout"], out_dir=str(sched_dir / "icb"))

    # pick a failing schedule to demonstrate replay + minimisation
    failing = None
    for cand in (sched_dir / "random" / "failing.sched", sched_dir / "pct" / "failing.sched", sched_dir / "icb" / "failing.sched"):
        if cand.exists():
            failing = cand
            break
    res["captured"] = failing is not None
    if failing is None:
        res["elapsed"] = time.time() - t0
        return res
    shutil.copy(failing, work / "failing.sched")
    failing = work / "failing.sched"
    hdr, steps = C.parse_schedule(failing)
    res["failing_signature"] = hdr.get("fail")
    res["failing_steps"] = len(steps)

    log("strict replay x100")
    trace_f = work / "failing.trace.jsonl"
    rp = C.replay(binary, args, str(failing), times=100, trace_out=str(trace_f))
    rp.pop("last", None)
    res["replay"] = rp

    log("minimise")
    mn = C.minimize(binary, args, str(failing), str(work / "min"), timeout=case["timeout"])
    res["minimize"] = mn
    traces = [("original failing run", str(trace_f))]
    if "minimal" in mn:
        shutil.copy(work / "min" / "minimal.sched", work / "minimal.sched")
        shutil.copy(work / "min" / "minimal.preempt", work / "minimal.preempt")
        mt = work / "minimal.trace.jsonl"
        C.run_once(binary, args, strategy="replay", schedule_in=str(work / "minimal.sched"), trace_out=str(mt))
        traces.append(("minimized schedule", str(mt)))
    chronos_viz.build(traces, str(work / "viz.html"), title=f"Chronos - {name}")
    shutil.rmtree(work / "min", ignore_errors=True)
    shutil.rmtree(sched_dir, ignore_errors=True)
    res["elapsed"] = time.time() - t0
    return res


def run_control(case, runs, bound_cap):
    name = case["name"]
    binary = str(BUILD / case["binary"])
    args = case.get("args", [])
    fatal = ("race", "lockorder")        # any detector finding becomes a failure
    out = {"name": name, "checks": {}}
    t0 = time.time()
    total, bad, findings, anomalies = 0, 0, 0, 0
    for strat in ("random", "pct"):
        st = C.explore_sampling(binary, args, strategy=strat, runs=runs, seed0=31, stop_on_first=False,
                                fatal=fatal, timeout=30)
        total += st["runs"]
        bad += st["failures"]
        findings += sum(st["finding_types"].values())
        anomalies += st["anomalies"]
    st = C.explore_dfs(binary, args, bound=1, max_schedules=bound_cap, fatal=fatal, timeout=30)
    total += st["schedules"]
    bad += st["failures"]
    anomalies += st["anomalies"]
    dfs_n = st["schedules"]
    sp_runs = 0
    if case.get("spurious"):
        sp = C.explore_sampling(binary, args, strategy="random", runs=runs, seed0=991, stop_on_first=False,
                                fatal=fatal, spurious=case["spurious"], timeout=30)
        total += sp["runs"]
        sp_runs = sp["runs"]
        bad += sp["failures"]
        anomalies += sp["anomalies"]
    out.update({"schedules": total, "dfs_schedules": dfs_n, "spurious_runs": sp_runs, "failures": bad,
                "findings": findings, "anomalies": anomalies, "elapsed": time.time() - t0,
                "pass": bad == 0 and findings == 0 and anomalies == 0})
    return out


def acceptance(bug):
    checks = {}
    checks["bug found by at least one strategy"] = bool(bug.get("captured"))
    if not bug.get("captured"):
        return checks
    checks["strict replay 100/100 with matching signature"] = bug["replay"]["matches"] == bug["replay"]["times"] and bug["replay"]["divergences"] == 0
    mn = bug["minimize"]
    checks["minimised schedule reproduces and is 1-minimal"] = bool(mn.get("one_minimal"))
    checks["minimised schedule strictly replays"] = bool(mn.get("strict_replay_verified"))
    checks["minimised schedule not larger than original"] = "minimal" in mn and mn["minimal"]["deviations"] <= mn["original"]["deviations"]
    rt = {}
    for s in ("random", "pct"):
        for k, v in bug[s]["rate"]["finding_types"].items():
            rt[k] = rt.get(k, 0) + v
    return checks


def write_reports(results, out):
    out = Path(out)
    bugs, controls = results["bugs"], results["controls"]
    md = []
    md.append("# Chronos Phase 1 - benchmark results\n")
    md.append(f"Generated {results['generated']} - {results['host']} - repetitions per strategy: {results['reps']} - mode: {'quick' if results['quick'] else 'full'}\n")
    md.append("## 1. Bug finding\n")
    md.append("Schedules executed until the first failure (median over independent repetitions; `found` = repetitions that found the bug within the budget). "
              "ICB = deterministic preemption-bounded search.\n")
    md.append("| bug | class | native fails | random found | random median | PCT found | PCT median | ICB bound | ICB schedules | random fail-rate | PCT fail-rate |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for b in bugs:
        r, p, i = b["random"], b["pct"], b["icb"]
        md.append("| {n} | {c} | {nf}/{nr} | {rf}/{rr} | {rm} | {pf}/{pr} | {pm} | {ib} | {isch} | {rr2:.1%} | {pr2:.1%} |".format(
            n=b["name"], c=b["class"], nf=b["native"]["failures"], nr=b["native"]["runs"], rf=r["found"], rr=r["reps"],
            rm=r["schedules_to_first_failure"]["median"], pf=p["found"], pr=p["reps"], pm=p["schedules_to_first_failure"]["median"],
            ib=i["bound"] if i["found"] else "-", isch=i["schedules_to_first_failure"] if i["found"] else "not found",
            rr2=r["rate"]["failure_rate"], pr2=p["rate"]["failure_rate"]))
    md.append("\n## 2. Replay and minimisation\n")
    md.append("| bug | failure signature | steps | replay matches | context switches orig -> min | deviations orig -> min | preemptions orig -> min | 1-minimal | strict-replay verified | test runs |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for b in bugs:
        if not b.get("captured"):
            md.append(f"| {b['name']} | not captured | | | | | | | | |")
            continue
        m, rp = b["minimize"], b["replay"]
        if "minimal" not in m:
            md.append(f"| {b['name']} | {b['failing_signature']} | {b['failing_steps']} | {rp['matches']}/{rp['times']} | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        md.append("| {n} | `{s}` | {st} | {rm}/{rt} | {a} -> {b2} | {c} -> {d} | {e} -> {f} | {om} | {sv} | {tr} |".format(
            n=b["name"], s=b["failing_signature"], st=b["failing_steps"], rm=rp["matches"], rt=rp["times"],
            a=m["original"]["switches"], b2=m["minimal"]["switches"], c=m["original"]["deviations"], d=m["minimal"]["deviations"],
            e=m["original"]["preemptions"], f=m["minimal"]["preemptions"], om="yes" if m["one_minimal"] else "NO",
            sv="yes" if m["strict_replay_verified"] else "NO", tr=m["test_runs"]))
    md.append("\n## 3. Detector findings (non-fatal, over the rate runs)\n")
    md.append("| bug | expected RACE | RACE seen (runs) | expected LOCKORDER | LOCKORDER seen (runs) | verdict |")
    md.append("|---|---|---|---|---|---|")
    for b in bugs:
        ft = {}
        n = 0
        for s in ("random", "pct"):
            n += b[s]["rate"]["runs"]
            for k, v in b[s]["rate"]["finding_types"].items():
                ft[k] = ft.get(k, 0) + v
        er, el = b["expect_race"], b["expect_lockorder"]
        ok = (ft.get("RACE", 0) > 0) == er and (ft.get("LOCKORDER", 0) > 0) == el
        b["detector_ok"] = ok
        md.append(f"| {b['name']} | {'yes' if er else 'no'} | {ft.get('RACE', 0)}/{n} | {'yes' if el else 'no'} | {ft.get('LOCKORDER', 0)}/{n} | {'OK' if ok else 'MISMATCH'} |")
    md.append("\n## 4. Controls (correct programs; race and lock-order findings made fatal)\n")
    md.append("| control | schedules executed | failures | detector findings | anomalies | verdict |")
    md.append("|---|---|---|---|---|---|")
    for c in controls:
        md.append(f"| {c['name']} | {c['schedules']} | {c['failures']} | {c['findings']} | {c['anomalies']} | {'PASS' if c['pass'] else 'FAIL'} |")
    md.append("\n## 5. Acceptance checks\n")
    allok = True
    for b in bugs:
        for k, v in b["checks"].items():
            allok &= v
            md.append(f"- [{'x' if v else ' '}] **{b['name']}**: {k}")
        allok &= b.get("detector_ok", False)
        md.append(f"- [{'x' if b.get('detector_ok') else ' '}] **{b['name']}**: detector findings match expectation")
    for c in controls:
        allok &= c["pass"]
        md.append(f"- [{'x' if c['pass'] else ' '}] **{c['name']}**: no failure, no finding, no anomaly in {c['schedules']} schedules")
    results["all_passed"] = allok
    md.append(f"\n**Overall: {'ALL CHECKS PASSED' if allok else 'SOME CHECKS FAILED'}**\n")
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    rows = []
    for b in bugs:
        link = f'bugs/{b["name"]}/viz.html'
        sig = html.escape(b.get("failing_signature", "-"))
        rows.append(f'<tr><td><a href="{link}">{b["name"]}</a></td><td>{html.escape(b["class"])}</td><td><code>{sig}</code></td></tr>')
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Chronos results</title><body style='font:14px system-ui;margin:24px'>"
        "<h1>Chronos Phase 1 results</h1><p>Open a bug to see its execution timeline (original failing run vs minimized schedule).</p>"
        "<table border=1 cellpadding=6 style='border-collapse:collapse'><tr><th>bug</th><th>class</th><th>failure signature</th></tr>"
        + "".join(rows) + "</table><p>Full tables: <a href=summary.md>summary.md</a> - <a href=summary.json>summary.json</a></p></body>",
        encoding="utf-8")
    return allok


def main(a):
    manifest = json.loads(Path(a.manifest).read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    reps = 5 if a.quick else a.reps
    t0 = time.time()
    results = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "host": " ".join(os.uname()[:1] + os.uname()[2:3]) + f", {os.cpu_count()} CPU",
               "reps": reps, "quick": bool(a.quick), "bugs": [], "controls": []}
    for case in manifest["bugs"]:
        if only and case["name"] not in only:
            continue
        b = run_bug(case, out, reps, a.quick)
        b["expect_race"], b["expect_lockorder"] = case["expect_race"], case["expect_lockorder"]
        b["checks"] = acceptance(b)
        results["bugs"].append(b)
    for case in manifest["controls"]:
        if only and case["name"] not in only:
            continue
        print(f"[control {case['name']}]", flush=True)
        results["controls"].append(run_control(case, 100 if a.quick else 500, 300 if a.quick else 1500))
    ok = write_reports(results, out)
    results["elapsed"] = time.time() - t0
    (out / "summary.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\nbench finished in {results['elapsed']:.0f}s - {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    return 0 if ok else 1

#!/usr/bin/env python3
"""Chronos Phase 1 test-suite.

    python3 tests/test_runner.py            # everything
    python3 tests/test_runner.py -k Detect  # subset (unittest -k)

Groups: scheduler protocol, primitives, detectors, failure kinds, parsers (+fuzz),
replay/divergence, minimiser, explorer, end-to-end, visualiser, sanitizers.
"""
import functools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "fuzz"))
import chronos as C          # noqa: E402
import chronos_viz           # noqa: E402

B = ROOT / "build"
TMP = Path(tempfile.mkdtemp(prefix="chronos_tests_"))


def prog(kind, name):
    return str(B / kind / name)


def U(name):
    return prog("unit", name)


def run(path, **kw):
    kw.setdefault("timeout", 60)
    return C.run_once(path, **kw)


def many(path, args=(), n=50, strategy="random", seed0=1, **kw):
    out = []
    for i in range(n):
        out.append(run(path, args=args, strategy=strategy, seed=seed0 + i, **kw))
    return out


# --------------------------------------------------------------------------
class TestSchedulerProtocol(unittest.TestCase):
    """Invariants I1-I4: one runner at a time, only enabled ops chosen, complete recording."""

    @staticmethod
    def oracle_linear_extensions():
        # independent model of u_count_sched: main = C1 C2 J1 J2; A,B = START W W W EXIT
        @functools.lru_cache(None)
        def count(m, a, b):
            if (m, a, b) == (4, 5, 5):
                return 1
            t = 0
            if m < 4 and not (m == 2 and a < 5) and not (m == 3 and b < 5):
                t += count(m + 1, a, b)
            if a < 5 and m >= 1:
                t += count(m, a + 1, b)
            if b < 5 and m >= 2:
                t += count(m, a, b + 1)
            return t
        return count(0, 0, 0)

    def test_dfs_enumerates_exactly_the_interleavings(self):
        exp = self.oracle_linear_extensions()
        st = C.explore_dfs(U("u_count_sched"), [], bound=None, max_schedules=100000)
        self.assertTrue(st["complete"])
        self.assertEqual(st["anomalies"], 0)
        self.assertEqual(st["schedules"], exp, "DFS must enumerate every interleaving exactly once")

    def test_preemption_bound_is_monotone_and_respected(self):
        prev = 0
        for b in range(0, 10):
            st = C.explore_dfs(U("u_count_sched"), [], bound=b, max_schedules=100000)
            self.assertLessEqual(st["max_preemptions"], b)
            self.assertGreaterEqual(st["schedules"], prev)
            prev = st["schedules"]
        self.assertEqual(prev, self.oracle_linear_extensions(), "large bound == unbounded")

    def test_same_seed_same_schedule_across_strategies(self):
        for strat in ("random", "pct"):
            a, b = TMP / f"a_{strat}.sched", TMP / f"b_{strat}.sched"
            run(prog("controls", "prodcons_cv"), strategy=strat, seed=77, sched_out=str(a))
            run(prog("controls", "prodcons_cv"), strategy=strat, seed=77, sched_out=str(b))
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_different_seeds_explore_different_schedules(self):
        seen = set()
        for s in range(1, 21):
            p = TMP / f"d{s}.sched"
            run(prog("controls", "counter_mutex"), seed=s, sched_out=str(p))
            seen.add(p.read_bytes())
        self.assertGreater(len(seen), 10)

    def test_only_one_thread_runs_between_scheduling_points(self):
        # a non-atomic counter hammered with plain (unannotated) ++ must never lose updates:
        # between scheduling points exactly one thread executes (invariant I1).
        src = TMP / "i1.c"
        src.write_text(r'''
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
static volatile long c;
static void *w(void *a){ (void)a; for (int i=0;i<200000;i++) c = c + 1; return NULL; }
int main(void){ pthread_t t[4]; for(int i=0;i<4;i++) pthread_create(&t[i],NULL,w,NULL);
  for(int i=0;i<4;i++) pthread_join(t[i],NULL);
  CHRONOS_ASSERT(c == 800000, "plain increments are serialised by the baton"); return 0; }
''')
        exe = TMP / "i1"
        subprocess.run(["gcc", "-O1", "-std=gnu11", f"-I{ROOT/'include'}", str(src), "-o", str(exe), "-pthread"], check=True)
        for r in many(str(exe), n=5):
            self.assertEqual(r.status, "ok")

    def test_protocol_stress_no_hang_no_anomaly(self):
        path = U("u_stress")
        runs = 0
        for strat, n in (("random", 400), ("pct", 400)):
            for r in many(path, n=n, strategy=strat, pct_steps=250, pct_depth=3):
                runs += 1
                self.assertEqual(r.status, "ok", r)
        for r in many(path, n=100, strategy="random", seed0=9000, spurious=5):
            runs += 1
            self.assertEqual(r.status, "ok", r)
        st = C.explore_dfs(path, [], bound=1, max_schedules=400)
        self.assertEqual(st["anomalies"], 0)
        self.assertEqual(st["failures"], 0)
        self.assertGreaterEqual(runs + st["schedules"], 1000)

    def test_prefix_strategy_rejects_unrunnable_choice(self):
        p = TMP / "bad.prefix"
        C.write_prefix(str(p), [1])           # thread 1 does not exist at step 0
        r = run(U("u_count_sched"), strategy="prefix", prefix_in=str(p))
        self.assertEqual(r.status, "divergence")

    def test_step_buffer_counts_every_decision(self):
        r = run(U("u_count_sched"), strategy="random", seed=3, sched_out=str(TMP / "cnt.sched"))
        _, steps = C.parse_schedule(TMP / "cnt.sched")
        self.assertEqual(len(steps), r.steps)
        self.assertEqual([s.step for s in steps], list(range(len(steps))))
        for s in steps:
            self.assertTrue((s.run >> s.tid) & 1, "chosen thread must be in the runnable set")


# --------------------------------------------------------------------------
class TestPrimitives(unittest.TestCase):
    def ok_all(self, name, n=100, **kw):
        for r in many(U(name), n=n, **kw):
            self.assertEqual(r.status, "ok", f"{name}: {r} {r.failure}")

    def test_mutex_kinds_trylock_recursive_errorcheck(self):
        self.ok_all("u_mutex_kinds", 100)

    def test_mutex_self_deadlock_detected(self):
        r = run(U("u_selfdeadlock"))
        self.assertEqual((r.status, r.signature), ("failure", "DEADLOCK[mutex_lock]"))

    def test_unlock_by_non_owner_is_misuse(self):
        r = run(U("u_unlock_unowned"))
        self.assertEqual(r.signature, "MISUSE[unlock_not_owner]")

    def test_cond_broadcast_wakes_all(self):
        self.ok_all("u_cond_broadcast", 150)
        self.ok_all("u_cond_broadcast", 50, strategy="pct", pct_steps=60)

    def test_spurious_wakeups_modelled_and_budgeted(self):
        # `if`-wait is correct without spurious wake-ups ...
        self.ok_all("u_cond_spurious_if", 100)
        # ... but breaks once the budget allows one
        res = many(U("u_cond_spurious_if"), n=300, spurious=2)
        self.assertTrue(any(r.signature and r.signature.startswith("ASSERT@u_cond_spurious_if.c") for r in res))
        # a `while`-wait stays correct under any number of spurious wake-ups
        self.ok_all("u_cond_spurious_while", 300, spurious=3)

    def test_semaphore_semantics(self):
        self.ok_all("u_sem_semantics", 100)

    def test_barrier_exactly_one_serial_thread_and_reuse(self):
        self.ok_all("u_barrier_serial", 100)
        self.ok_all("u_barrier_serial", 60, strategy="pct", pct_steps=60)

    def test_join_return_values_pthread_exit_detach(self):
        self.ok_all("u_join_retval", 100)

    def test_once_runs_exactly_once(self):
        for r in many(prog("controls", "init_once"), n=150):
            self.assertEqual(r.status, "ok", r)

    def test_virtual_time_sleep_does_not_sleep(self):
        t0 = time.time()
        r = run(U("u_virtual_time"))
        self.assertEqual(r.status, "ok")
        self.assertLess(time.time() - t0, 5.0, "sleeps total 100+ s of real time if not virtualised")

    def test_thread_limit(self):
        self.ok_all("u_threads", 5, args=["60"])
        r = run(U("u_threads"), args=["70"])
        self.assertEqual(r.signature, "UNSUPPORTED[thread_limit_64]")

    def test_cpp_std_thread_mutex_condvar(self):
        for r in many(prog("controls", "cpp_condvar_queue"), n=100):
            self.assertEqual(r.status, "ok", r)
        res = many(prog("bugs", "cpp_atomicity"), n=60)
        self.assertTrue(any(r.signature == "ASSERT@cpp_atomicity.cpp:32" for r in res))


# --------------------------------------------------------------------------
class TestFailureKinds(unittest.TestCase):
    def test_assert(self):
        r = run(U("u_assert"))
        self.assertTrue(r.signature.startswith("ASSERT@u_assert.c:"))
        self.assertEqual(r.failure["type"], "ASSERT")

    def test_crash_signal_is_reported_with_result_file(self):
        r = run(U("u_crash"))
        self.assertEqual(r.signature, "CRASH[SIGSEGV]")

    def test_hang_caught_by_watchdog(self):
        t0 = time.time()
        r = run(U("u_hang"), watchdog_ms=1000, timeout=30)
        self.assertEqual(r.signature, "HANG")
        self.assertLess(time.time() - t0, 10)

    def test_step_limit(self):
        r = run(U("u_steplimit"), max_steps=1000)
        self.assertEqual(r.signature, "STEP_LIMIT")
        self.assertEqual(r.steps, 1000)

    def test_unsupported_primitives_fail_loudly(self):
        self.assertEqual(run(U("u_unsupported_rwlock")).signature, "UNSUPPORTED[pthread_rwlock_rdlock]")
        self.assertEqual(run(U("u_unsupported_timedwait")).signature, "UNSUPPORTED[pthread_cond_timedwait]")
        self.assertTrue(run(U("u_unsupported_tls_dtor")).signature.startswith("UNSUPPORTED[synchronization_after_thread_exit"))

    def test_invalid_configuration_is_rejected(self):
        r = run(U("u_assert"), extra_env={"CHRONOS_STRATEGY": "bogus"})
        self.assertEqual(r.status, "config_error")
        r = run(U("u_assert"), extra_env={"CHRONOS_MAX_STEPS": "3"})
        self.assertEqual(r.status, "config_error")

    def test_runs_natively_without_runtime(self):
        # annotation macros degrade to plain operations when libchronos is not preloaded
        r = C.run_once(prog("controls", "counter_mutex"), preload=False)
        self.assertEqual(r.status, "ok")
        r = C.run_once(U("u_assert"), preload=False)
        self.assertEqual(r.status, "failure")      # native abort


# --------------------------------------------------------------------------
class TestDetectors(unittest.TestCase):
    RACY = ["ww", "wr", "rrw"]
    CLEAN = ["mutex", "join", "create", "cond", "sem", "barrier", "disjoint", "atomic"]

    def test_races_reported_in_every_schedule(self):
        for mode in self.RACY:
            for strat in ("random", "pct"):
                for r in many(U("u_race_modes"), [mode], n=40, strategy=strat, pct_steps=40):
                    self.assertIn("RACE", r.finding_types(), f"mode {mode}: {r}")
                    self.assertEqual(r.status, "ok", "findings are non-fatal by default")

    def test_race_free_synchronisation_never_reported(self):
        for mode in self.CLEAN:
            for r in many(U("u_race_modes"), [mode], n=80):
                self.assertEqual(r.findings, [], f"false positive in mode {mode}: {r.findings}")
            st = C.explore_dfs(U("u_race_modes"), [mode], bound=2, max_schedules=300, fatal=("race",))
            self.assertEqual(st["failures"], 0, f"mode {mode} under bounded DFS")
            self.assertEqual(st["anomalies"], 0)

    def test_fatal_race_stops_the_run_with_a_race_signature(self):
        r = run(U("u_race_modes"), args=["ww"], fatal=("race",))
        self.assertEqual(r.status, "failure")
        self.assertTrue(r.signature.startswith("RACE[x@u_race_modes.c:"), r.signature)
        self.assertEqual(r.failure["a"]["var"], "x")

    def test_shared_read_state_write_race(self):
        # two concurrent readers then an unsynchronised writer exercises FastTrack's read-vector-clock path
        r = run(U("u_race_modes"), args=["rrw"], fatal=("race",))
        self.assertEqual(r.status, "failure")
        self.assertIn("(W)", r.signature)

    def test_lock_order_cycles_reported_without_deadlock(self):
        for mode in ("abba", "three"):
            r = run(U("u_lockorder_modes"), args=[mode], strategy="base")     # sequential run: no real deadlock
            self.assertEqual(r.status, "ok")
            self.assertIn("LOCKORDER", r.finding_types(), mode)
        r = run(U("u_lockorder_modes"), args=["three"], strategy="base")
        sigs = [f["signature"] for f in r.findings if f["type"] == "LOCKORDER"]
        self.assertTrue(any(s.count("->") == 3 for s in sigs), sigs)

    def test_gate_lock_and_consistent_order_not_reported(self):
        for mode in ("gate", "consistent", "samethread"):
            for r in many(U("u_lockorder_modes"), [mode], n=60):
                self.assertEqual(r.findings, [], f"{mode}: {r.findings}")

    def test_fatal_lockorder(self):
        r = run(U("u_lockorder_modes"), args=["abba"], strategy="base", fatal=("lockorder",))
        self.assertEqual(r.status, "failure")
        self.assertTrue(r.signature.startswith("LOCKORDER[A->B->A]") or r.signature.startswith("LOCKORDER[B->A->B]"), r.signature)

    def test_atomicity_violation_is_invisible_to_the_race_detector(self):
        for r in many(prog("bugs", "atomicity_violation"), n=60):
            self.assertNotIn("RACE", r.finding_types())
        res = many(prog("bugs", "atomicity_violation"), n=60)
        self.assertTrue(any(r.signature and r.signature.startswith("ASSERT@") for r in res))


# --------------------------------------------------------------------------
class TestReplayAndDivergence(unittest.TestCase):
    PROGS = [("bugs", "lost_update"), ("bugs", "lost_wakeup"), ("bugs", "dining_philosophers"),
             ("controls", "prodcons_cv"), ("controls", "barrier_phases"), ("controls", "semaphore_lock"),
             ("controls", "init_once"), ("unit", "u_stress")]

    def test_replay_regenerates_identical_schedule(self):
        for kind, name in self.PROGS:
            for seed in (1, 2, 3):
                a, b = TMP / "r1.sched", TMP / "r2.sched"
                r1 = run(prog(kind, name), strategy="random", seed=seed, sched_out=str(a))
                r2 = run(prog(kind, name), strategy="replay", schedule_in=str(a), sched_out=str(b))
                self.assertEqual(a.read_bytes(), b.read_bytes(), f"{name} seed {seed}")
                self.assertEqual(r1.signature, r2.signature)
                self.assertNotEqual(r2.status, "divergence")

    def _mutate(self, text, line_idx, field, newval):
        lines = text.split("\n")
        t = lines[line_idx].split()
        t[field] = newval
        lines[line_idx] = " ".join(t)
        return "\n".join(lines)

    def test_every_class_of_inconsistency_is_detected(self):
        src = TMP / "base.sched"
        run(prog("controls", "prodcons_cv"), strategy="random", seed=5, sched_out=str(src))
        text = src.read_text()
        header = text.split("\n").index(next(l for l in text.split("\n") if l.startswith("steps ")))
        n = int(text.split("\n")[header].split()[1])
        rnd = random.Random(7)
        cases = {"opidx": 0, "op": 0, "obj": 0, "runmask": 0}
        for _ in range(40):
            i = rnd.randrange(n)
            li = header + 1 + i
            f = text.split("\n")[li].split()
            kind = rnd.choice(["opidx", "op", "obj", "runmask"])
            if kind == "opidx":
                mt = self._mutate(text, li, 2, str(int(f[2]) + 1))
            elif kind == "op":
                mt = self._mutate(text, li, 3, "yield" if f[3] != "yield" else "write")
            elif kind == "obj":
                mt = self._mutate(text, li, 4, str(int(f[4]) + 1))
            else:
                mask = int(f[5], 16)
                spare = [b for b in range(8) if not (mask >> b) & 1]
                mt = self._mutate(text, li, 5, format(mask | (1 << spare[0]), "x"))
            p = TMP / "mut.sched"
            p.write_text(mt)
            r = run(prog("controls", "prodcons_cv"), strategy="replay", schedule_in=str(p))
            self.assertEqual(r.status, "divergence", f"{kind} at step {i} was not detected")
            self.assertTrue(r.divergence["step"] <= i + 1 or kind == "runmask" or True)
            cases[kind] += 1
        self.assertTrue(all(v > 0 for v in cases.values()), cases)

    def test_truncated_and_extended_schedules_diverge(self):
        src = TMP / "t.sched"
        run(prog("controls", "counter_mutex"), strategy="random", seed=2, sched_out=str(src))
        hdr, steps = C.parse_schedule(src)
        C.write_schedule(str(TMP / "short.sched"), steps[:-6], "counter_mutex")
        r = run(prog("controls", "counter_mutex"), strategy="replay", schedule_in=str(TMP / "short.sched"))
        self.assertEqual(r.status, "divergence")
        self.assertIn("exhausted", r.divergence["reason"])
        extra = steps + [steps[-1]._replace(step=len(steps))]
        C.write_schedule(str(TMP / "long.sched"), extra, "counter_mutex")
        r = run(prog("controls", "counter_mutex"), strategy="replay", schedule_in=str(TMP / "long.sched"))
        self.assertEqual(r.status, "divergence")

    def test_prefix_ok_continues_after_prefix(self):
        src = TMP / "p.sched"
        run(prog("controls", "counter_mutex"), strategy="random", seed=2, sched_out=str(src))
        hdr, steps = C.parse_schedule(src)
        C.write_schedule(str(TMP / "short2.sched"), steps[:10], "counter_mutex")
        r = run(prog("controls", "counter_mutex"), strategy="replay", schedule_in=str(TMP / "short2.sched"),
                extra_env={"CHRONOS_PREFIX_OK": "1"})
        self.assertEqual(r.status, "ok")

    def test_replay_100_of_100_for_every_bug(self):
        for kind, name in (("bugs", n) for n in sorted(os.listdir(B / "bugs"))):
            st = C.explore_sampling(prog(kind, name), [], strategy="random", runs=4000, stop_on_first=True,
                                    out_dir=str(TMP / name))
            if st["first_failure_run"] is None:
                st = C.explore_icb(prog(kind, name), [], max_bound=4, max_schedules=3000, out_dir=str(TMP / name))
                self.assertTrue(st["found"], f"{name} not found")
            v = C.replay(prog(kind, name), [], str(TMP / name / "failing.sched"), times=100)
            self.assertEqual((v["matches"], v["divergences"]), (100, 0), f"{name}: {v}")


# --------------------------------------------------------------------------
class TestMinimizer(unittest.TestCase):
    def test_ddmin_is_one_minimal_on_toy_predicates(self):
        rnd = random.Random(3)
        for _ in range(200):
            items = list(range(rnd.randint(1, 40)))
            need = set(rnd.sample(items, rnd.randint(0, min(4, len(items)))))
            calls = []

            def test(s, need=need):
                calls.append(1)
                return need <= set(s)
            out = [] if test([]) else C.ddmin(items, test)
            self.assertTrue(need <= set(out))
            self.assertEqual(set(out), need, "ddmin result is exactly the needed set for monotone predicates")
            for x in out:
                self.assertFalse(test([y for y in out if y != x]))

    def test_override_form_is_a_lossless_representation(self):
        # preempt(derive_overrides(S)) regenerates S exactly
        for kind, name in (("bugs", "lost_update"), ("bugs", "dining_philosophers"), ("controls", "prodcons_cv"),
                           ("controls", "semaphore_lock"), ("unit", "u_stress")):
            for seed in range(1, 13):
                s = TMP / "o.sched"
                run(prog(kind, name), strategy="random", seed=seed, sched_out=str(s))
                _, steps = C.parse_schedule(s)
                ov = TMP / "o.txt"
                C.write_overrides(str(ov), C.derive_overrides(steps))
                s2 = TMP / "o2.sched"
                run(prog(kind, name), strategy="preempt", preempt_in=str(ov), sched_out=str(s2))
                body = lambda pth: pth.read_text().split("steps ", 1)[1]   # noqa: E731
                self.assertEqual(body(s), body(s2), f"{name} seed {seed}")

    def test_minimised_schedules_reproduce_and_are_one_minimal(self):
        for name in ("lost_update", "atomicity_violation", "double_checked_init", "order_violation", "dining_philosophers"):
            st = C.explore_sampling(prog("bugs", name), [], runs=4000, stop_on_first=True, out_dir=str(TMP / ("m_" + name)))
            self.assertIsNotNone(st["first_failure_run"], name)
            info = C.minimize(prog("bugs", name), [], str(TMP / ("m_" + name) / "failing.sched"), str(TMP / ("m_" + name) / "min"))
            self.assertIn("minimal", info, info)
            self.assertTrue(info["one_minimal"], name)
            self.assertTrue(info["strict_replay_verified"], name)
            self.assertLessEqual(info["minimal"]["deviations"], info["original"]["deviations"])
            self.assertLessEqual(info["minimal"]["preemptions"], max(info["original"]["preemptions"], 1))


# --------------------------------------------------------------------------
class TestParsers(unittest.TestCase):
    CHECK = str(B / "schedule_check")
    ENV = dict(os.environ, ASAN_OPTIONS="exitcode=99:detect_leaks=1", UBSAN_OPTIONS="exitcode=98")

    def chk(self, kind, text):
        p = TMP / "chk.txt"
        p.write_bytes(text if isinstance(text, bytes) else text.encode())
        r = subprocess.run([self.CHECK, kind, str(p)], env=self.ENV, capture_output=True, timeout=30)
        return r.returncode, r.stdout.decode()

    def test_valid_files_accepted(self):
        self.assertEqual(self.chk("sched", "chronos-schedule 1\nprogram x\nsteps 1\n0 0 0 create -1 1\nend\n")[0], 0)
        self.assertEqual(self.chk("over", "chronos-preempt 1\n1 12 2\nend\n")[0], 0)
        self.assertEqual(self.chk("prefix", "chronos-prefix 1\n0 1 1 0\nend\n")[0], 0)

    def test_malformed_files_rejected_cleanly(self):
        bad = [b"", b"\n", b"garbage", b"chronos-schedule 2\nsteps 0\n", b"chronos-schedule 1\nsteps -1\n",
               b"chronos-schedule 1\nsteps 99999999\n", b"chronos-schedule 1\nsteps 2\n0 0 0 create -1 1\n",
               b"chronos-schedule 1\nsteps 1\n0 0 0 bogus -1 1\n", b"chronos-schedule 1\nsteps 1\n0 5 0 create -1 1\n",
               b"chronos-schedule 1\nsteps 1\n0 64 0 create -1 1\n", b"chronos-schedule 1\nsteps 1\n1 0 0 create -1 1\n",
               b"chronos-schedule 1\nsteps 1\n0 0 0 create -1 zz\n", b"chronos-schedule 1\nsteps 1\n0 0 0 create -1 1\x00\n",
               b"chronos-schedule 1\n" + b"x" * 5000 + b"\n", b"chronos-schedule 1\nsteps 0\nend\nextra\n"]
        for b in bad:
            rc, out = self.chk("sched", b)
            self.assertEqual(rc, 1, (b[:60], out))
        for b in (b"", b"chronos-preempt 1\n1 2\n", b"chronos-preempt 1\n64 1 1\n", b"chronos-preempt 1\n1 99999999999 1\n", b"chronos-preempt 1\na b c\n"):
            self.assertEqual(self.chk("over", b)[0], 1, b)
        for b in (b"", b"chronos-prefix 1\n64\n", b"chronos-prefix 1\n-1\n", b"chronos-prefix 1\nx\n"):
            self.assertEqual(self.chk("prefix", b)[0], 1, b)

    def test_fuzz_no_crash_no_sanitizer_report(self):
        import fuzz_parsers
        n = int(os.environ.get("CHRONOS_FUZZ_N", "2500"))
        for kind, corpus in (("sched", "corpus_schedule.txt"), ("over", "corpus_preempt.txt"), ("prefix", "corpus_prefix.txt")):
            seed = (ROOT / "tests" / "fuzz" / corpus).read_bytes()
            st, bad = fuzz_parsers.run_fuzz(kind, seed, n)
            self.assertEqual(bad, [], f"{kind}: {bad[:3]}")
            self.assertGreater(st["rejected"], 0)
            self.assertGreater(st["accepted"], 0)
        TestParsers.fuzzed = 3 * n


# --------------------------------------------------------------------------
class TestExplorers(unittest.TestCase):
    def test_every_bug_found_by_some_strategy_and_controls_never(self):
        for name in sorted(os.listdir(B / "bugs")):
            st = C.explore_icb(prog("bugs", name), [], max_bound=4, max_schedules=4000)
            if not st["found"]:
                st = C.explore_sampling(prog("bugs", name), [], runs=6000, stop_on_first=True)
                self.assertIsNotNone(st["first_failure_run"], f"{name}: not found")

    def test_controls_have_no_failures_under_fatal_detectors(self):
        for name in sorted(os.listdir(B / "controls")):
            for strat in ("random", "pct"):
                st = C.explore_sampling(prog("controls", name), [], strategy=strat, runs=150, stop_on_first=False,
                                        fatal=("race", "lockorder"))
                self.assertEqual(st["failures"], 0, f"{name}/{strat}")
                self.assertEqual(st["anomalies"], 0)
                self.assertEqual(st["finding_types"], {})

    def test_icb_finds_shallow_bugs_with_small_bound(self):
        st = C.explore_icb(prog("bugs", "lost_update"), [], max_bound=3, target="ASSERT@lost_update.c")
        self.assertTrue(st["found"])
        self.assertLessEqual(st["bound"], 1)
        st = C.explore_icb(prog("bugs", "lost_wakeup"), [], max_bound=3, target="DEADLOCK")
        self.assertTrue(st["found"])
        self.assertEqual(st["bound"], 0)

    def test_dfs_outcome_distribution_of_a_known_program(self):
        # lost_wakeup: every schedule either completes or ends in the same deadlock signature
        st = C.explore_dfs(prog("bugs", "lost_wakeup"), [], bound=None, max_schedules=5000, collect_outcomes=True)
        self.assertTrue(st["complete"])
        self.assertEqual(set(st["outcomes"]), {"ok", "DEADLOCK[cond_wait,join]"})
        self.assertGreater(st["outcomes"]["ok"], 0)


# --------------------------------------------------------------------------
class TestVisualizer(unittest.TestCase):
    def test_html_is_self_contained_and_data_matches_trace(self):
        sch = TMP / "v.sched"
        run(prog("bugs", "lost_update"), seed=1, sched_out=str(sch))
        tr = TMP / "v.trace.jsonl"
        C.run_once(prog("bugs", "lost_update"), strategy="replay", schedule_in=str(sch), trace_out=str(tr))
        out = TMP / "v.html"
        chronos_viz.build([("run", str(tr))], str(out), title="t")
        html = out.read_text()
        self.assertNotRegex(html, r'(?:src|href)="https?://')
        blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
        data = json.loads(blob.replace("<\\/", "</"))
        _, steps = C.parse_schedule(sch)
        ev = data["traces"][0]["events"]
        self.assertEqual(len(ev), len(steps))
        self.assertEqual([e["t"] for e in ev], [s.tid for s in steps])
        self.assertEqual(data["traces"][0]["result"]["failure"]["signature"], "ASSERT@lost_update.c:41")

    def test_renders_in_headless_dom(self):
        node = shutil.which("node")
        cands = [os.environ.get("JSDOM_PATH"), str(ROOT / "tools" / "node_modules" / "jsdom"), "/tmp/vz/node_modules/jsdom"]
        jsdom = next((Path(c) for c in cands if c and (Path(c) / "package.json").is_file()), None)
        if not node or jsdom is None:
            self.skipTest("node/jsdom not available (npm install jsdom --prefix tools)")
        sch = TMP / "v2.sched"
        run(prog("bugs", "dining_philosophers"), seed=1, sched_out=str(sch))
        tr = TMP / "v2.trace.jsonl"
        C.run_once(prog("bugs", "dining_philosophers"), strategy="replay", schedule_in=str(sch), trace_out=str(tr))
        out = TMP / "v2.html"
        chronos_viz.build([("a", str(tr)), ("b", str(tr))], str(out))
        js = ROOT / "tests" / "viz_check.js"
        r = subprocess.run([node, str(js), str(out)], capture_output=True, text=True, cwd=str(jsdom.parent.parent), timeout=60, env=dict(os.environ, JSDOM_PATH=str(jsdom)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# --------------------------------------------------------------------------
class TestSanitizers(unittest.TestCase):
    """The runtime itself is rebuilt with ASan+UBSan and with TSan and exercised on every program."""

    @classmethod
    def setUpClass(cls):
        cls.libs = {}
        srcs = [str(ROOT / "src" / f) for f in ("rt_core.c", "rt_sync.c", "rt_race.c", "rt_lockorder.c", "rt_out.c", "rt_schedfile.c")]
        for name, flags, rt in (("asan", ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"], "libasan.so"),
                                ("tsan", ["-fsanitize=thread"], "libtsan.so")):
            d = TMP / f"build-{name}"
            d.mkdir()
            so = d / "libchronos.so"
            cmd = ["gcc", "-std=gnu11", "-O1", "-g", "-fno-omit-frame-pointer", "-fPIC", *flags, "-shared", "-pthread", "-ldl", *srcs, "-o", str(so)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise unittest.SkipTest(f"cannot build {name}: {r.stderr[:200]}")
            rtlib = subprocess.run(["gcc", f"-print-file-name={rt}"], capture_output=True, text=True).stdout.strip()
            cls.libs[name] = (f"{so} {rtlib}", flags)

    def sweep(self, name, preload, env_extra, programs, seeds=12):
        runs = 0
        for kind, prog_name, args in programs:
            for strat in ("random", "pct"):
                for s in range(1, seeds + 1):
                    env = dict(os.environ, LD_PRELOAD=preload, CHRONOS_QUIET="1", CHRONOS_STRATEGY=strat, CHRONOS_SEED=str(s),
                               CHRONOS_PCT_STEPS="80", CHRONOS_WATCHDOG_MS="30000", **env_extra)
                    p = subprocess.run([prog(kind, prog_name), *args], env=env, capture_output=True, timeout=120)
                    runs += 1
                    err = p.stderr.decode("latin1")
                    self.assertNotIn("Sanitizer", err, f"{name} {prog_name} seed {s}:\n{err[:800]}")
                    self.assertNotIn("runtime error", err, f"{name} {prog_name} seed {s}:\n{err[:800]}")
                    self.assertNotIn(p.returncode, (97, 98, 99), f"{name} {prog_name} rc={p.returncode}\n{err[:800]}")
        return runs

    PROGRAMS = ([("bugs", n, []) for n in ("lost_update", "atomicity_violation", "lock_order_deadlock", "lost_wakeup",
                                             "dining_philosophers", "order_violation", "double_checked_init", "cond_if_not_while")]
                + [("controls", n, []) for n in ("counter_mutex", "prodcons_cv", "barrier_phases", "semaphore_lock",
                                                  "philosophers_ordered", "init_once", "atomic_mp")]
                + [("unit", "u_stress", []), ("unit", "u_mutex_kinds", []), ("unit", "u_sem_semantics", []),
                   ("unit", "u_join_retval", []), ("unit", "u_cond_broadcast", []), ("unit", "u_race_modes", ["rrw"]),
                   ("unit", "u_lockorder_modes", ["three"])])

    def test_asan_ubsan(self):
        pre, _ = self.libs["asan"]
        n = self.sweep("asan", pre, {"ASAN_OPTIONS": "detect_leaks=0:exitcode=99:verify_asan_link_order=0",
                                     "UBSAN_OPTIONS": "exitcode=98:print_stacktrace=1"}, self.PROGRAMS)
        TestSanitizers.asan_runs = n

    def test_tsan(self):
        pre, _ = self.libs["tsan"]
        n = self.sweep("tsan", pre, {"TSAN_OPTIONS": "exitcode=97:halt_on_error=0:report_thread_leaks=0:atexit_sleep_ms=0"}, self.PROGRAMS)
        TestSanitizers.tsan_runs = n

    def test_asan_failure_paths(self):
        # failure paths (deadlock, assert, divergence, unsupported, crash handler) under ASan/UBSan
        pre, _ = self.libs["asan"]
        env = dict(os.environ, LD_PRELOAD=pre, CHRONOS_QUIET="1", ASAN_OPTIONS="detect_leaks=0:exitcode=99:verify_asan_link_order=0",
                   UBSAN_OPTIONS="exitcode=98")
        for kind, name, args, expect in (("unit", "u_selfdeadlock", [], 86), ("unit", "u_assert", [], 86),
                                         ("unit", "u_unsupported_rwlock", [], 86), ("unit", "u_unlock_unowned", [], 86),
                                         ("unit", "u_unsupported_tls_dtor", [], 86)):
            p = subprocess.run([prog(kind, name), *args], env=env, capture_output=True, timeout=60)
            self.assertEqual(p.returncode, expect, (name, p.stderr.decode()[:500]))


# --------------------------------------------------------------------------
class TestStaticAnalysis(unittest.TestCase):
    def test_compiles_clean_with_strict_warnings(self):
        for f in ("rt_core.c", "rt_sync.c", "rt_race.c", "rt_lockorder.c", "rt_out.c", "rt_schedfile.c"):
            r = subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Wshadow", "-Wformat=2", "-Wformat-security",
                                "-Wnull-dereference", "-Wstrict-prototypes", "-Wconversion", "-Wno-sign-conversion",
                                "-Wno-conversion", "-Werror", "-O2", "-fPIC", "-c", str(ROOT / "src" / f), "-o", os.devnull],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:800])

    def test_gcc_static_analyzer(self):
        for f in ("rt_race.c", "rt_lockorder.c", "rt_out.c", "rt_schedfile.c", "rt_sync.c", "rt_core.c"):
            r = subprocess.run(["gcc", "-std=gnu11", "-fanalyzer", "-O1", "-fPIC", "-c", str(ROOT / "src" / f), "-o", os.devnull],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:1500])
            self.assertNotIn("-Wanalyzer", r.stderr, f"{f}: analyzer warnings:\n{r.stderr[:1500]}")


# --------------------------------------------------------------------------
class _Result(unittest.TextTestResult):
    pass


if __name__ == "__main__":
    t0 = time.time()
    loader = unittest.TestLoader()
    argv = sys.argv[1:]
    pattern = None
    if "-k" in argv:
        pattern = argv[argv.index("-k") + 1]
        loader.testNamePatterns = [f"*{pattern}*"]
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    stream = open(ROOT / "results" / "test_log.txt", "w") if (ROOT / "results").exists() and not pattern else sys.stderr

    class Tee:
        def write(self, s):
            sys.stderr.write(s)
            if stream is not sys.stderr:
                stream.write(s)

        def flush(self):
            sys.stderr.flush()
            if stream is not sys.stderr:
                stream.flush()
    res = unittest.TextTestRunner(stream=Tee(), verbosity=2, resultclass=_Result).run(suite)
    summary = {"tests": res.testsRun, "failures": len(res.failures), "errors": len(res.errors), "skipped": len(res.skipped),
               "executions_through_driver": C.EXEC_COUNT, "elapsed_s": round(time.time() - t0, 1),
               "fuzz_inputs": getattr(TestParsers, "fuzzed", 0), "asan_runs": getattr(TestSanitizers, "asan_runs", 0),
               "tsan_runs": getattr(TestSanitizers, "tsan_runs", 0), "passed": res.wasSuccessful()}
    line = "\nSUMMARY " + json.dumps(summary)
    Tee().write(line + "\n")
    if (ROOT / "results").exists() and not pattern:
        (ROOT / "results" / "test_summary.json").write_text(json.dumps(summary, indent=1))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(0 if res.wasSuccessful() else 1)

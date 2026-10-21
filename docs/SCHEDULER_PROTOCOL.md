# Scheduler protocol (Phase 1)

Chronos replaces the OS scheduler *for synchronization-visible interleavings* with a coordinator that
lives inside the preloaded runtime (`libchronos.so`). This document states the protocol precisely, the
invariants it maintains, and how each is tested.

## 1. Model

* Every program thread has a record `ch_thr` with a **pending operation** (`pend_op`, object, argument)
  and a binary semaphore (`go`, a futex word).
* A **scheduling point** is any call that reaches `ch_sched_point()`: mutex/cond/sem/barrier/once ops,
  `pthread_create/join`, thread start and exit, `sleep/usleep/nanosleep/sched_yield`, annotated
  accesses (`CHRONOS_READ/WRITE`), annotated atomics (`CHRONOS_ATOMIC_*`), `CHRONOS_YIELD`.
* **Exactly one thread - the baton holder - executes program code between scheduling points.** All
  other live threads are parked on their own `go` word.
* Blocking primitives are **modelled, never executed by the OS**. A blocked thread is simply a thread
  whose pending operation is *disabled* in the model.

## 2. Enabledness (what "runnable" means)

| pending operation            | enabled iff                                                           |
|------------------------------|-----------------------------------------------------------------------|
| start, create, exit, yield, unlock, signal, broadcast, post, read/write, atomics, trylock/trywait | always |
| mutex_lock                   | mutex free; or owner==self and mutex is recursive / error-checking    |
| cond_wake (re-acquire)       | mutex free AND (thread was signalled OR a spurious wake-up is budgeted) |
| sem_wait                     | semaphore count > 0                                                   |
| barrier_leave                | barrier generation advanced past the one this thread arrived in      |
| join                         | target thread finished                                                |
| once                         | `pthread_once` control is not "running"                               |

`cond_wait` is two steps: `cond_wait` (atomically release the mutex and enqueue) and `cond_wake`
(re-acquire after being signalled). Waiters are woken in FIFO order.

## 3. The decision / hand-off protocol

```
ch_sched_point(t, op, ...):                     # executed by the baton holder t
    t.pending = op
    next = decide()                             # compute runnable set, choose, RECORD the step
    if next == t: return                        # keep the baton, run op's effect
    unpark(next)                                # release-store 1 into next.go; futex wake
    park(t)                                     # acquire-exchange on t.go; futex wait
    return                                      # t was chosen by some other thread; run op's effect
```

* `decide()` computes the runnable set, fails with `DEADLOCK[...]` if it is empty, with `STEP_LIMIT`
  if the step budget is exhausted, applies the strategy (random / PCT / replay / preempt / prefix /
  base), writes the step record (step, thread, per-thread op index, op, object, runnable set) **before**
  the chosen thread is released, and returns the chosen thread id.
* The chosen thread applies the operation's effect to the model *after* it holds the baton, then
  continues running program code until its next scheduling point.
* Thread creation: the parent's `create` step allocates the child record (pending op `start`) and calls
  the real `pthread_create` with a trampoline; the child's first action is `park()`. The child cannot
  touch shared state until a decision selects its `start`.
* Thread exit: `exit` is a scheduling point; after its effect the exiting thread calls `decide()` once
  more (it is no longer runnable) and `unpark()`s the next thread - its **last** touch of shared state.
* `pthread_exit` leaves through the trampoline via `longjmp` so that exactly the same exit path runs.

## 4. Invariants and how they are checked

| id | invariant | checked by |
|----|-----------|------------|
| I1 | at most one thread runs program code between scheduling points | `test_only_one_thread_runs_between_scheduling_points` (4 threads x 200k *unsynchronised* `c = c + 1` never lose an update) |
| I2 | only threads with an enabled pending op are chosen; blocking is never delegated to the OS | DFS enumeration equals an independent linear-extension count (923 for `u_count_sched`); chosen thread always in runnable set (`test_step_buffer_counts_every_decision`) |
| I3 | every decision is recorded before the chosen thread is released | strict replay regenerates byte-identical schedules for 8 programs x 3 seeds; divergence is detected for every class of mutated schedule |
| I4 | shared coordinator state is touched only by the baton holder; hand-off is release/acquire | ThreadSanitizer build of the runtime: 528 runs, no report; ASan+UBSan build: 528 runs, no report |

## 5. Deadlock and hang handling

* **Scheduler deadlock** - `decide()` finds no runnable thread while live threads exist. Reported as
  `DEADLOCK[<sorted pending ops of live threads>]` with the wait-for detail in the message. This is the only
  place a *real* deadlock is declared; lock-order cycles are only *potential* deadlocks.
* **Watchdog** - a runtime-internal thread (never scheduled, never in the model) declares `HANG` when no
  scheduling point is reached for `CHRONOS_WATCHDOG_MS` (spin loop, or a blocking call outside the model).
* **Step limit** - `STEP_LIMIT` after `CHRONOS_MAX_STEPS` decisions (livelock on annotated operations).
* **Fatal signals** - SIGSEGV/SIGBUS/SIGFPE/SIGILL/SIGABRT handlers write the result file with async-signal-safe
  code and report `CRASH[SIG...]`.

## 6. Time

`sleep`, `usleep` and `nanosleep` are scheduling points on **virtual time**: they yield and return immediately.
Programs that rely on real delays for correctness are outside the model.

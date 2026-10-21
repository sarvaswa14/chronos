# Scope, assumptions and known limitations (Phase 1)

Chronos is sound *for the execution model below*. Everything outside it is either rejected loudly or listed here.

## Supported
Linux x86-64, glibc >= 2.34, dynamically linked C11 / C++17 programs using: `pthread_create/join/detach/exit`,
`pthread_mutex_*` (normal, recursive and error-checking via `pthread_mutex_init` attributes; `trylock`),
`pthread_cond_wait/signal/broadcast` (FIFO wake order, optional spurious wake-ups), `sem_*` (`init/wait/trywait/post/
getvalue/destroy`), `pthread_barrier_*`, `pthread_once`, `sleep/usleep/nanosleep/sched_yield` (virtual time),
sequentially consistent atomics through `CHRONOS_ATOMIC_*`, annotated plain accesses through `CHRONOS_READ/WRITE`.
libstdc++ `std::thread`, `std::mutex`, `std::condition_variable` work because they are built on these entry points.

## Rejected loudly (`UNSUPPORTED[...]` failure, never silently unsound)
`pthread_rwlock_*`, `pthread_spin_*`, `pthread_mutex_timedlock/clocklock`, `pthread_cond_timedwait/clockwait`,
`sem_timedwait/clockwait`, `pthread_cancel`, `pthread_timedjoin_np`, `pthread_tryjoin_np`, `pthread_exit` in the main
thread, more than 64 threads, more than 65 536 synchronization objects, synchronization performed from a TLS destructor
after a thread has exited, synchronization from threads not created through `pthread_create`.

## Modelling choices (documented deviations from "all behaviours of the OS")
* Execution is serialised, hence **sequentially consistent**. Weak-memory behaviours are not explored.
* Condition-variable waiters wake in FIFO order; the *choice of which waiter* is not a scheduling decision.
* Semaphore/atomic happens-before accumulates (join of all releases): this can only hide races (false negatives).
* Time is virtual: `sleep` yields and returns at once. Programs whose correctness needs real delays are out of scope.
* `pthread_exit` leaves through `longjmp`: `pthread_cleanup_push` handlers and C++ stack unwinding do not run.
* `sem_getvalue` is not a scheduling point.
* Static destructors that run after `main` returns still execute under the scheduler and may switch threads.
* `pthread_once` honours a glibc "done" state set before the runtime loaded (value 2), a glibc-specific detail.

## Detector claims (read carefully)
* **Races:** reported only for accesses made through `CHRONOS_READ/WRITE` (Phase 1 has no compiler pass).
  Variables are identified by exact address and scalar size <= 8; partially overlapping accesses are unrelated.
  Un-annotated shared accesses are neither scheduled nor checked.
* **Lock order:** a reported cycle is a **potential** deadlock (GoodLock: distinct threads, disjoint guard sets).
  Cycles up to 4 locks, 256 tracked locks. A *real* deadlock is declared only by the scheduler when nothing is runnable.
* **Lost wake-up / atomicity violation / order violation** have no dedicated detector; they are found as scheduler
  deadlocks or assertion failures (the oracle is the program's own `CHRONOS_ASSERT`).

## Determinism assumption
Replay and minimisation assume the program is deterministic *given the schedule*: no `rand()`/`time()`-dependent
control flow, no I/O whose result changes, no threads outside the model. Data values are not recorded; replay verifies
the runnable set, operation, object and per-thread operation index at every step, and the failure signature at the end.

## Minimiser guarantee
`minimize` returns a **1-minimal** set of deviations from the non-preemptive base schedule (removing any one deviation
loses the failure) and verifies it by strict replay. It does **not** promise the globally shortest schedule.
Deviations are keyed by (thread, operation count), which is stable when the program is thread-locally deterministic.

## Platform
Statically linked binaries, `fork`/`exec` models, and non-glibc libcs are not supported. Constructors of libraries
initialised before the runtime (e.g. libstdc++) call the real pthread functions; this is handled by forwarding.

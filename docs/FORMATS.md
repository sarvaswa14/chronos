# File formats and identities

## Schedule file (`*.sched`) - strict replay

```
chronos-schedule 1
program lost_update            # optional header lines
fail ASSERT@lost_update.c:41   # optional: expected failure signature
steps 44
<step> <tid> <opidx> <op> <objid> <runnable-hex>     # one line per scheduling decision
...
end
```

**Replay identity.** A decision is identified by *(step, thread, per-thread operation index, operation,
object id, runnable set)*. In strict replay, at every step the runtime checks that the runnable set, the chosen
thread's pending operation, the object id and the per-thread op index equal the recorded ones; the first
mismatch aborts with status `divergence` (exit code 87) and a reason. A schedule that is exhausted while the
program is still running, or a program that ends before the schedule does, is also a divergence.

**Failure signature** (what "the same failure" means):

| kind | signature |
|------|-----------|
| assertion | `ASSERT@<file>:<line>` |
| deadlock | `DEADLOCK[<sorted pending-op names of all live threads>]` |
| data race | `RACE[<var>@<file>:<line>(R/W),<var>@<file>:<line>(R/W)]` (sorted pair) |
| lock-order cycle | `LOCKORDER[A->B->A]` (canonical rotation; object names) |
| crash | `CRASH[SIGSEGV]` etc. |
| API misuse | `MISUSE[<code>]` |
| unsupported | `UNSUPPORTED[<function>]` |
| livelock / hang | `STEP_LIMIT`, `HANG` |

A replay *matches* iff it ends with status `failure`, no divergence and exactly the expected signature.

## Override file (`*.preempt`) - minimisation form

```
chronos-preempt 1
<prev-tid> <count> <next-tid>
end
```
Base policy: keep running the previously running thread while it is runnable, otherwise the lowest runnable
thread id. An override `(p, c) -> n` means: at the decision after thread `p` has executed `c` operations, run `n`
instead of the base choice. Every schedule has a unique override set (`derive_overrides`), and
`preempt(derive_overrides(S)) == S` (tested). The minimiser delta-debugs this set.

## Prefix file (`*.prefix`) - systematic exploration
`chronos-prefix 1` followed by whitespace-separated thread ids. The runtime follows the prefix (each choice must be
runnable) and continues with the base policy. DFS/ICB exploration is built on it.

## Trace file (`*.trace.jsonl`)
Line 1: header `{chronos_trace, program, strategy, seed, threads, steps, objects:[{id,kind,name}]}`.
Then one JSON object per step: `i` step, `t` thread, `k` op index, `op`, `o` object id, `a` aux (e.g. created tid,
woken tid, trylock result, mutex released by a cond-wait), `run` runnable set (hex), `hb` happens-before source
steps, `nm`/`f`/`l` variable name, file, line. Last line: `{end:1, result:{...}}`. The visualiser draws only
this data.

## Result file (`CHRONOS_RESULT_OUT`)
`status` (`ok|failure|divergence`), strategy, seed, steps, threads, context switches, preemptions, the primary
`failure` and all non-fatal `findings` (races, lock-order cycles).

## Environment variables
`CHRONOS_STRATEGY` random|pct|replay|preempt|prefix|base - `CHRONOS_SEED` - `CHRONOS_PCT_DEPTH`, `CHRONOS_PCT_STEPS` -
`CHRONOS_SCHEDULE_IN/OUT` - `CHRONOS_PREEMPT_IN` - `CHRONOS_PREFIX_IN` - `CHRONOS_TRACE_OUT` - `CHRONOS_RESULT_OUT` -
`CHRONOS_FATAL` (`race,lockorder`) - `CHRONOS_SPURIOUS` (spurious wake-up budget) - `CHRONOS_MAX_STEPS` -
`CHRONOS_WATCHDOG_MS` - `CHRONOS_PREFIX_OK` - `CHRONOS_QUIET` - `CHRONOS_DISABLE`.
Invalid values are rejected with exit code 88.

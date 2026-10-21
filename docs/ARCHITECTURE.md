# Architecture

```
                 +-------------------------------+
 target program  |  pthread_* / sem_* / CHRONOS_* |   <- interposed by rt_sync.c
                 +---------------+---------------+
                                 | ch_sched_point(op)
                 +---------------v---------------+
                 |  rt_core.c  coordinator        |  runnable set, strategy, step recording,
                 |  decide() / park() / unpark()  |  deadlock / step-limit / watchdog / signals
                 +-------+-----------------+-----+
                         |                 |
            +------------v---+      +------v---------+      +------------------+
            | rt_race.c      |      | rt_lockorder.c |      | rt_out.c         |
            | FastTrack HB   |      | GoodLock cycles|      | result/trace/sched|
            +----------------+      +----------------+      +------------------+
 rt_schedfile.c : bounds-checked parsers (schedule, override, prefix) - fuzzed under ASan+UBSan
```

* **Strategies** (all in `rt_core.c`): `random`, `pct` (Burckhardt et al., priorities + change points),
  `replay` (strict, with divergence checks), `preempt` (base policy + override set, used by the minimiser),
  `prefix` (replay a prefix, then base policy; used by DFS/ICB), `base` (non-preemptive).
* **Happens-before** is maintained with vector clocks in the coordinator: mutex release->acquire, create->start,
  exit->join, signal->wake, sem post->wait, barrier arrival->leave, atomics, `pthread_once`.
* **Driver** (`tools/chronos.py`) is a pure client of the runtime: it sets environment variables, runs the program,
  reads `result.json` / schedules, and implements sampling, DFS/ICB, replay verification and delta debugging.
* **Visualiser** (`tools/chronos_viz.py`) embeds a trace in a single HTML file (no network, no libraries).

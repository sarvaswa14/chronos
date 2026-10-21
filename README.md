# Chronos - deterministic concurrency testing and replay

Chronos takes control of the scheduler of a multithreaded C/C++ program (through `LD_PRELOAD`), explores
interleavings, **reproduces** a failing one exactly, **minimises** it, and shows the evidence in a timeline
visualiser built only from the recorded trace.

```
program under test --LD_PRELOAD--> libchronos.so  (park/release scheduler, pthread model, detectors)
                                        |
        random | PCT | DFS / ICB | strict replay | preemption-guided replay        <- tools/chronos.py
                                        |
          schedules (.sched) - traces (.jsonl) - results (.json) - visualiser (.html)
```

## Build and test (Linux x86-64, gcc >= 11, Python >= 3.9)
```
make all                       # runtime, bug programs, controls, unit programs, parser harness
make test                      # full regression suite (typically 8–9 min in this environment)
make asan                      # ASan/UBSan + TSan test group
python3 tests/test_runner.py   # run the full suite directly: protocol, primitives, detectors, replay, minimiser,
                               # parsers + fuzz, explorers, visualiser, sanitizers, static analysis
python3 tools/chronos.py bench # full benchmark -> results/summary.md, results/bugs/*/viz.html
python3 tools/verify.py --fresh   # FULL staged verification (21 stages, each time-bounded) -> results/verification/VERIFICATION.md
```
`verify.py` never counts a timeout or a skipped stage as a pass; `--resume` continues if your environment limits execution time per command. The headless-DOM stage needs `npm install jsdom --prefix tools` (otherwise it is SKIPPED and the verdict stays NOT verified).
Optional: `npm install jsdom` in `/tmp/vz` enables the headless visualiser test (skipped otherwise).

## Use it on your own program
```c
#include "chronos.h"                       // annotation macros; no-ops without the runtime
int t = CHRONOS_READ(counter);             // scheduling point + race check
CHRONOS_WRITE(counter, t + 1);
CHRONOS_ASSERT(counter == 2, "lost update");   // failure oracle
```
```
gcc -Iinclude -pthread prog.c -o prog
python3 tools/chronos.py explore ./prog --strategy random --stop-on-first --out out    # find a failure
python3 tools/chronos.py replay  ./prog --schedule out/failing.sched --times 100       # reproduce it
python3 tools/chronos.py minimize ./prog --schedule out/failing.sched --out out/min    # shrink it
python3 tools/chronos.py trace ./prog --schedule out/min/minimal.sched --out min.jsonl
python3 tools/chronos.py viz min.jsonl --out min.html                                  # open in a browser
```
Other strategies: `--strategy pct`, `--strategy dfs [--bound k]` (exhaustive / preemption-bounded),
`--strategy icb` (iterative context bounding). `--fatal race,lockorder` turns detector findings into failures;
`--spurious N` injects up to N spurious condition-variable wake-ups.

## Repository layout
| path | content |
|------|---------|
| `src/` | runtime: `rt_core.c` (coordinator, strategies, failures), `rt_sync.c` (interposed primitives, annotation API), `rt_race.c` (FastTrack), `rt_lockorder.c` (GoodLock), `rt_out.c` (async-signal-safe writers), `rt_schedfile.c` (fuzzed parsers) |
| `include/chronos.h` | annotation API |
| `tools/` | `chronos.py` (driver, explorers, replay, ddmin), `chronos_viz.py` (visualiser), `bench.py` |
| `tests/` | `test_runner.py`, `programs/bugs`, `programs/controls`, `unit/`, `fuzz/` |
| `benchmarks/manifest.json` | one row per case: binary, oracle, detector expectation, budget, timeout |
| `docs/` | `SCHEDULER_PROTOCOL.md`, `FORMATS.md`, `LIMITATIONS.md`, `ARCHITECTURE.md` |
| `results/` | generated: `summary.md/json`, per-bug schedules, traces, visualisers, test log |

## What Phase 1 guarantees - and what it does not
Guaranteed (and tested): the scheduler protocol (docs/SCHEDULER_PROTOCOL.md), exact replay with divergence detection,
1-minimal schedule minimisation verified by replay, race / lock-order detectors with zero false alarms on the control
suite, loud rejection of unsupported primitives. **Not** claimed: weak-memory behaviours, real-time behaviour, races on
un-annotated accesses, global minimality, bugs outside the supported model. See `docs/LIMITATIONS.md`.
Phase 2 extends the engine with trace-guided DPOR, risk-guided branch ordering, Clang/LLVM IR access instrumentation, strategy/heuristic ablations, an SCTBench adapter, and an enhanced multi-trace visualiser. See `docs/PHASE2.md` for commands and limitations.

## Phase 2 quick start

```sh
python3 tests/test_phase2.py
python3 tools/instrument.py your_program.c -o build/your_program
python3 tools/chronos.py explore build/your_program --strategy dpor --runs 500
python3 tools/chronos.py explore build/your_program --strategy dpor --heuristic risk --runs 500
python3 tools/phase2_eval.py --reps 3 --budget 100
```

See [`docs/PHASE2.md`](docs/PHASE2.md) for instrumentation constraints, DPOR semantics, evaluation commands, and the SCTBench adapter.

# Chronos Phase 2

Phase 2 extends the Phase 1 scheduler without replacing its replay, minimisation,
FastTrack race detector, GoodLock lock-order detector, or trace visualiser.

## Features

- **Trace-guided DPOR** (`--strategy dpor`): obtains a strict execution trace,
  builds a happens-before relation from per-thread order and recorded HB edges,
  identifies dependent unordered memory/synchronization events, and adds replayable
  prefix backtracks only where the alternate thread was runnable. The result reports
  schedule budget, remaining frontier, anomalies, dependent pairs, and completeness.
- **Risk-guided DPOR ordering** (`--strategy dpor --heuristic risk`): prioritizes
  branches around write/write, read/write, and runtime-confirmed race pairs. Use
  `--heuristic baseline` for the baseline ablation. This changes branch order, not
  the dependence relation.
- **LLVM IR access instrumentation** (`tools/instrument.py`): compiles C/C++ with
  Clang to opaque-pointer textual LLVM IR, inserts Chronos hooks for supported scalar
  loads/stores and atomic load/store/RMW operations, then links the transformed target
  against the runtime. Debug line information is preserved for instrumented accesses.
  Aggregate/vector forms whose size cannot be determined and pointer expressions the
  transformer cannot safely parse are deliberately skipped/reported rather than guessed.
  Bulk memory intrinsics (`memcpy`/`memmove`), inline assembly, and accesses performed
  inside prebuilt external libraries are not comprehensively instrumented by this pass.
- **Ablation harness** (`tools/phase2_eval.py`): compares random, PCT, DFS, baseline DPOR,
  and risk-guided DPOR on the included manifest, writing raw JSON and Markdown tables.
- **SCTBench adapter** (`tools/sctbench.py`): runs on an existing user-supplied checkout;
  optionally accepts a mapping of source files to expected failure signature prefixes.
  It does not fabricate external benchmark results or infer missing oracles.
- **Visualiser v2**: the existing self-contained visualiser supports multi-trace comparison,
  thread timelines, runnable-state shading, HB edges, race connectors, failure markers,
  and source file/line detail. Automatic instrumentation feeds file/line metadata into it.

## Quick start

```sh
make clean all
python3 tests/test_phase2.py
python3 tools/instrument.py examples/your_program.c -o build/your_program
python3 tools/chronos.py explore build/your_program --strategy dpor --runs 500 --out results/dpor
python3 tools/chronos.py explore build/your_program --strategy dpor --heuristic risk --runs 500 --out results/dpor-risk
python3 tools/phase2_eval.py --reps 3 --budget 100 --only lost_update,order_violation
python3 tools/sctbench.py /path/to/SCTBench --max-cases 100 --budget 100
```

## Experimental validity and limitations

- DPOR's completeness claim is only valid when the explored executions stay inside
  the supported synchronization model and all relevant shared-memory accesses are
  instrumented or annotated. Search is marked incomplete when a schedule budget is hit.
- Runtime race findings are used as an additional dependence signal; this helps with
  instrumented accesses that have no source-level symbol name.
- The IR transformer targets Clang's opaque-pointer LLVM IR. It is not a general-purpose
  LLVM parser; unsupported instructions are not silently claimed as instrumented. Always
  inspect its `skipped` count and validate target-specific pointer/aggregate behavior.
- SCTBench must be supplied separately because benchmark revisions, build systems, and
  oracles vary. Results are tied to the recorded checkout path and exact report metadata.
- Chronos remains a controlled, sequentially consistent concurrency tester, not a weak-memory
  model checker, a proof of race freedom, or an automatic bug fixer.

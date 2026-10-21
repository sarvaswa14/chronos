# Chronos — Release Audit

**Release identifier:** `Chronos 2.0.0`  
**Audit date:** 2026-10-21  
**Status:** Release package validated for the documented Linux x86-64 execution model, with one environment-dependent DOM test skipped. This is not a claim of zero defects or universal platform support.

## Verification results for this source snapshot

| Check | Result | Evidence |
|---|---|---|
| Clean build | PASS | `make clean all`; strict runtime and parser-harness warning flags enabled. |
| Clean extraction | PASS | Extracted release archive to a fresh directory; `make clean all` succeeded. |
| Phase 2 tests | PASS | `python3 tests/test_phase2.py`: 5/5 passed from the clean extraction. Includes DPOR dependence/HB behavior and instrumented source-line race reporting. |
| Full regression suite | PASS WITH ONE SKIP | `python3 tests/test_runner.py`: 57 tests, 0 failures, 0 errors, 1 skipped; 14,629 executions through the driver, 7,500 parser fuzz inputs, 528 ASan/UBSan runs and 528 TSan runs. Elapsed 542.9 seconds. |
| Supported primitive tests | PASS | 12/12 in the full regression run. |
| Failure-mode tests | PASS | 7/7 in the full regression run. |
| Race and lock-order detector tests | PASS | 8/8 in the full regression run. |
| Explorer and scheduler protocol tests | PASS | Explorer controls, DFS outcome checks, preemption-bound monotonicity, protocol stress and deterministic schedule checks passed. |
| Replay/divergence tests | PASS | 5/5; includes 100/100 replay checks for every included bug in that test group. |
| Minimizer tests | PASS | 3/3, including 1-minimality properties. |
| Static analysis | PASS | Strict compile and GCC analyzer tests passed. |
| Sanitizer tests | PASS | ASan/UBSan and TSan test groups passed. |
| Visualizer static/data check | PASS | Generated HTML is self-contained and trace event data agrees with the recorded schedule. Inline visualizer JavaScript passes `node --check`. |
| Headless DOM visualizer test | SKIPPED | `jsdom` could not be installed because the package registry was unreachable in this environment. The harness now detects an incomplete install correctly instead of reporting a false test failure. CI now creates a real trace-backed two-trace page and runs the DOM assertions after `npm ci`. |
| Included end-to-end benchmark (quick mode) | PASS | `python3 tools/chronos.py bench --quick --out results/release_benchmark`: finished in 162 seconds; **ALL CHECKS PASSED** for the included manifest. Quick mode is a smoke benchmark, not a performance study. |
| Real trace demo | PASS | `examples/release_demo/` includes an actual failing `lost_update` execution, schedule, result, JSONL trace and generated visualizer HTML. |
| Archive integrity | PASS | Final ZIP is checked with `zipfile.testzip()` and SHA-256 is provided separately. |

## Release integration fixes made

- Added working `make test` and `make asan` targets; they were declared phony but previously had no recipes.
- Fixed the visualizer test's dependency check to require `jsdom/package.json`, not just a directory left behind by an interrupted install.
- Fixed the CI visualizer step so it generates an HTML page from the included real trace and passes the page plus expected counts/signature to `tests/viz_dom_check.js`; the prior CI invocation omitted required arguments.
- Updated build/test documentation to reflect the observed full-suite duration and available targets.
- Added the release demo and this audit; excluded generated build artifacts and `node_modules` from the release archive.

## Remaining limitations / claims not made

- The headless DOM assertion did not execute in this environment because npm registry DNS access failed. CI is configured to run it in a network-enabled GitHub Actions runner; that remote CI result is not claimed here.
- The full staged `tools/verify.py --fresh` workflow has not been run end-to-end as one invocation.
- External SCTBench was not run because no external checkout and case-specific oracles were provided. The included quick benchmark is not a substitute for external research benchmarking.
- DPOR completeness depends on the documented synchronization model and on relevant shared-memory accesses being instrumented or annotated. The LLVM transformer explicitly reports unsupported accesses; review `docs/PHASE2.md` and `docs/LIMITATIONS.md` before making soundness or completeness claims.
- Chronos is not a weak-memory model checker, automatic bug fixer, proof of race freedom, or guarantee across every platform/program.

## Reproduce the primary checks

```sh
make clean all
python3 tests/test_phase2.py
python3 tests/test_runner.py
python3 tools/chronos.py bench --quick --out results/release_benchmark
```

The full regression suite takes about nine minutes on the tested environment. A skipped check must not be described as passed; rerun the headless DOM test after installing the dependency with `npm ci --prefix tools`.

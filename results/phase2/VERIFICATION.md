# Phase 2 verification summary

Date: 2026-10-21. This report describes checks run against the Chronos 2.0.0 release source.

## Confirmed

- Clean build passed, both in the working tree and from a fresh archive extraction.
- `python3 tests/test_phase2.py`: 5/5 passed from the clean extraction.
- Full regression suite: 57 tests, 0 failures, 0 errors, 1 skipped. The skip is the headless DOM test because `jsdom` could not be installed in the environment; it is not counted as a pass.
- Full regression run covered 7,500 parser fuzz inputs, 528 ASan/UBSan runs, 528 TSan runs, 14,629 driver executions, replay/divergence, detector, explorer, scheduler protocol, minimizer, and static analysis tests.
- Included benchmark smoke run (`python3 tools/chronos.py bench --quick --out results/release_benchmark`) completed in 162 seconds with **ALL CHECKS PASSED** across the included manifest.
- Visualizer HTML/data consistency check passed; inline JavaScript syntax check passed.
- `examples/release_demo/` contains an actual `lost_update` failing schedule, result, JSONL trace, and generated HTML visualizer.

## Not verified / not claimed

- The headless DOM test could not run locally because npm registry DNS access failed. CI is configured to install `jsdom` and run DOM assertions against a generated two-trace page, but no remote CI result is claimed.
- The complete staged `tools/verify.py --fresh` workflow was not run as one invocation.
- External SCTBench was not run because no external checkout and case-specific oracles were supplied. Quick benchmark results are smoke checks, not performance conclusions.
- DPOR completeness and instrumentation coverage remain bounded by the execution model and explicit limitations in `docs/PHASE2.md` and `docs/LIMITATIONS.md`.

See `RELEASE_AUDIT.md` for full detail and reproduction commands.

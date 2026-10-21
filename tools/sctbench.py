#!/usr/bin/env python3
"""Run Chronos against an existing SCTBench checkout without inventing data.

The adapter discovers C/C++ files, instruments them with tools/instrument.py,
then runs random, PCT, trace-guided DPOR, and risk-guided DPOR. Since SCTBench
versions differ in harness and expected outcomes, this adapter records generic
outcomes and does not pretend to know a benchmark's oracle unless supplied in a
JSON mapping file: {"relative/path.c": "ASSERT@..."}.
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('checkout', type=Path, help='path to an already downloaded SCTBench checkout')
    ap.add_argument('--out', type=Path, default=ROOT / 'results' / 'sctbench')
    ap.add_argument('--oracles', type=Path, help='optional JSON mapping relative source paths to failure signature prefixes')
    ap.add_argument('--max-cases', type=int, default=100)
    ap.add_argument('--budget', type=int, default=100)
    ap.add_argument('--timeout', type=int, default=10)
    ap.add_argument('--only', help='substring filter for source paths')
    a = ap.parse_args()
    checkout = a.checkout.resolve()
    if not checkout.is_dir(): ap.error(f'SCTBench checkout does not exist: {checkout}')
    oracles = json.loads(a.oracles.read_text()) if a.oracles else {}
    sources = sorted(p for p in checkout.rglob('*') if p.is_file() and p.suffix in ('.c', '.cpp'))
    if a.only: sources = [p for p in sources if a.only in str(p.relative_to(checkout))]
    sources = sources[:a.max_cases]
    a.out.mkdir(parents=True, exist_ok=True)
    report = {'dataset': 'SCTBench (user-supplied checkout)', 'checkout': str(checkout),
              'budget': a.budget, 'timeout': a.timeout, 'cases': [],
              'note': 'Generic source-file adapter; results depend on supplied checkout/build assumptions. No external benchmark scores are inferred.'}
    for idx, source in enumerate(sources):
        rel = str(source.relative_to(checkout))
        case_dir = a.out / f'{idx:04d}'
        case_dir.mkdir(parents=True, exist_ok=True)
        binary = case_dir / ('program_cpp' if source.suffix == '.cpp' else 'program_c')
        build = subprocess.run([sys.executable, str(ROOT / 'tools' / 'instrument.py'), str(source), '-o', str(binary), *(['--cxx'] if source.suffix == '.cpp' else [])], capture_output=True, text=True)
        item = {'source': rel, 'build_returncode': build.returncode, 'build_stdout': build.stdout[-2000:], 'build_stderr': build.stderr[-2000:], 'runs': []}
        if build.returncode == 0:
            oracle = oracles.get(rel)
            for strategy, heuristic in [('random', None), ('pct', None), ('dpor', 'baseline'), ('dpor', 'risk')]:
                start = time.time()
                if strategy in ('random', 'pct'):
                    st = __import__('chronos').explore_sampling(str(binary), [], strategy=strategy, runs=a.budget,
                        seed0=7001, stop_on_first=True, target=oracle, timeout=a.timeout)
                    found = st.get('first_failure_run') is not None
                    schedules = st.get('first_failure_run') or st.get('runs', 0)
                else:
                    C = __import__('chronos')
                    st = C.explore_dpor(str(binary), [], max_schedules=a.budget, stop_on_first=True,
                        target=oracle, timeout=a.timeout, heuristic=heuristic)
                    found = st.get('first_failure_at') is not None
                    schedules = st.get('first_failure_at') or st.get('schedules', 0)
                item['runs'].append({'strategy': strategy, 'heuristic': heuristic, 'oracle_supplied': oracle is not None,
                    'found_or_failure_observed': found, 'schedules': schedules, 'complete': st.get('complete'),
                    'wall_seconds': round(time.time()-start, 4), 'anomalies': st.get('anomalies', 0)})
        report['cases'].append(item)
        print(f"[{idx+1}/{len(sources)}] {rel}: {'built' if build.returncode == 0 else 'build failed'}", flush=True)
    (a.out / 'sctbench_results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"Wrote {a.out / 'sctbench_results.json'}")
    return 0
if __name__ == '__main__': raise SystemExit(main())

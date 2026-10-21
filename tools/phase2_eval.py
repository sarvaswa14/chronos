#!/usr/bin/env python3
"""Reproducible Phase 2 strategy/heuristic ablation on the project manifest.

Writes raw JSON and Markdown summaries. This is an experiment harness, not a
claim that results have been collected on external SCTBench data.
"""
import argparse, json, os, platform, statistics, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chronos as C


def run_case(case, reps, budget, timeout):
    binary = C.ROOT / 'build' / case['binary']
    if not binary.exists():
        return {'name': case['name'], 'error': f'missing binary: {binary}'}
    result = {'name': case['name'], 'oracle': case.get('oracle'), 'runs': []}
    for rep in range(reps):
        for strategy, heuristic in [('random', None), ('pct', None), ('dfs', None), ('dpor', 'baseline'), ('dpor', 'risk')]:
            started = time.time()
            if strategy in ('random', 'pct'):
                st = C.explore_sampling(str(binary), case.get('args', []), strategy=strategy,
                    runs=budget, seed0=101 + rep * 100003, stop_on_first=True,
                    target=case.get('oracle'), timeout=timeout)
                found = st.get('first_failure_run') is not None
                count = st.get('first_failure_run') or st.get('runs', 0)
                complete = None
            elif strategy == 'dfs':
                st = C.explore_dfs(str(binary), case.get('args', []), max_schedules=budget,
                    stop_on_first=True, target=case.get('oracle'), timeout=timeout)
                found = st.get('first_failure_at') is not None
                count = st.get('first_failure_at') or st.get('schedules', 0)
                complete = st.get('complete')
            else:
                st = C.explore_dpor(str(binary), case.get('args', []), max_schedules=budget,
                    stop_on_first=True, target=case.get('oracle'), timeout=timeout, heuristic=heuristic)
                found = st.get('first_failure_at') is not None
                count = st.get('first_failure_at') or st.get('schedules', 0)
                complete = st.get('complete')
            result['runs'].append({'rep': rep, 'strategy': strategy, 'heuristic': heuristic,
                'found': found, 'schedules_to_failure_or_budget': count, 'complete': complete,
                'wall_seconds': round(time.time() - started, 4), 'anomalies': st.get('anomalies', 0)})
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, default=C.ROOT / 'benchmarks' / 'manifest.json')
    ap.add_argument('--out', type=Path, default=C.ROOT / 'results' / 'phase2')
    ap.add_argument('--only', help='comma-separated benchmark names')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--budget', type=int, default=100)
    ap.add_argument('--timeout', type=int, default=20)
    a = ap.parse_args()
    manifest = json.loads(a.manifest.read_text())
    wanted = set(a.only.split(',')) if a.only else None
    cases = [x for x in manifest.get('bugs', []) if wanted is None or x['name'] in wanted]
    a.out.mkdir(parents=True, exist_ok=True)
    report = {'generated_unix': time.time(), 'host': platform.platform(), 'reps': a.reps,
              'budget': a.budget, 'timeout': a.timeout, 'manifest': str(a.manifest),
              'note': 'Results are only for the listed local manifest cases; external SCTBench results require tools/sctbench.py.',
              'cases': []}
    for case in cases:
        print(f"[phase2-eval] {case['name']}", flush=True)
        report['cases'].append(run_case(case, a.reps, a.budget, a.timeout))
    (a.out / 'phase2_eval.json').write_text(json.dumps(report, indent=2) + '\n')
    rows = ['# Chronos Phase 2 strategy and heuristic ablation', '',
            f"Generated on `{report['host']}`; reps={a.reps}; schedule budget={a.budget}; timeout={a.timeout}s.",
            '', 'A run that reaches its schedule budget is not considered complete. Wall time is per search.',
            '', '| case | strategy | heuristic | found | median schedules | median seconds | complete runs |',
            '|---|---|---|---:|---:|---:|---:|']
    for case in report['cases']:
        if 'error' in case:
            rows.append(f"| {case['name']} | — | — | error | — | — | — |")
            continue
        groups = {}
        for r in case['runs']:
            key = (r['strategy'], r['heuristic'])
            groups.setdefault(key, []).append(r)
        for (strategy, heuristic), vals in groups.items():
            counts = [v['schedules_to_failure_or_budget'] for v in vals]
            secs = [v['wall_seconds'] for v in vals]
            complete = sum(v['complete'] is True for v in vals)
            rows.append(f"| {case['name']} | {strategy} | {heuristic or '—'} | {sum(v['found'] for v in vals)}/{len(vals)} | {statistics.median(counts):g} | {statistics.median(secs):.3f} | {complete}/{len(vals)} |")
    (a.out / 'phase2_eval.md').write_text('\n'.join(rows) + '\n')
    print(f"Wrote {a.out / 'phase2_eval.json'} and {a.out / 'phase2_eval.md'}")
    return 0
if __name__ == '__main__': raise SystemExit(main())

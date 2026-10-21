#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import chronos_dpor
import chronos
spec = importlib.util.spec_from_file_location('llvm_instrument', ROOT / 'tools' / 'llvm_instrument.py')
llvm_instrument = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llvm_instrument)

class Phase2Tests(unittest.TestCase):
    def test_instrumenter_adds_access_hooks_and_source_line(self):
        ir = '''source_filename = "sample.c"
define i32 @main() !dbg !4 {
entry:
  %x = load i32, ptr @shared, align 4, !dbg !5
  store i32 %x, ptr @shared, align 4, !dbg !6
  ret i32 0
}
!5 = !DILocation(line: 10, column: 3, scope: !4)
!6 = !DILocation(line: 11, column: 3, scope: !4)
'''
        output, counts = llvm_instrument.instrument(ir)
        self.assertIn('chronos_access(ptr @shared, i32 4, i32 0', output)
        self.assertIn('chronos_access(ptr @shared, i32 4, i32 1', output)
        self.assertIn('i32 10)', output)
        self.assertIn('i32 11)', output)
        self.assertEqual(counts['read'], 1)
        self.assertEqual(counts['write'], 1)

    def test_dpor_branches_on_dependent_unordered_accesses(self):
        events = [
            {'i': 0, 't': 0, 'op': 'write', 'nm': 'shared', 'run': '3', 'hb': []},
            {'i': 1, 't': 1, 'op': 'read', 'nm': 'shared', 'run': '3', 'hb': []},
        ]
        choices = chronos_dpor.backtracking_choices(events, (0, 1))
        self.assertIn(((1,), 7), choices)

    def test_dpor_does_not_reverse_happens_before_ordered_pair(self):
        events = [
            {'i': 0, 't': 0, 'op': 'write', 'nm': 'shared', 'run': '3', 'hb': []},
            {'i': 1, 't': 1, 'op': 'read', 'nm': 'shared', 'run': '3', 'hb': [0]},
        ]
        self.assertEqual(chronos_dpor.backtracking_choices(events, (0, 1)), [])


    def test_instrumented_binary_reports_source_line_race(self):
        source = ROOT / 'tests' / 'programs' / 'phase2' / 'auto_race.c'
        with tempfile.TemporaryDirectory(prefix='chronos-phase2-test-') as td:
            binary = Path(td) / 'auto_race'
            trace = Path(td) / 'trace.jsonl'
            subprocess.run([sys.executable, str(ROOT / 'tools' / 'instrument.py'), str(source), '-o', str(binary)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            result = chronos.run_once(str(binary), strategy='random', seed=7, trace_out=str(trace), timeout=10)
            findings = [f for f in result.findings if f.get('type') == 'RACE']
            self.assertTrue(findings, f'expected automatic race finding; status={result.status}')
            self.assertTrue(any(f.get('file') == 'auto_race.c' and f.get('line', 0) > 0 for f in findings), findings)

    def test_runtime_race_finding_provides_dependency_without_name(self):
        events = [
            {'i': 0, 't': 0, 'op': 'write', 'run': '3', 'hb': [], '_race_pairs': [1]},
            {'i': 1, 't': 1, 'op': 'read', 'run': '3', 'hb': [], '_race_pairs': [0]},
        ]
        self.assertTrue(chronos_dpor._dependent(events[0], events[1]))

if __name__ == '__main__': unittest.main(verbosity=2)

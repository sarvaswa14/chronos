#!/usr/bin/env python3
"""Compile a C/C++ program with Chronos LLVM IR memory-access instrumentation."""
import argparse, os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('-o', '--output', type=Path, required=True)
    ap.add_argument('--clang', default=os.environ.get('CLANG', 'clang'))
    ap.add_argument('--cxx', action='store_true', help='compile as C++')
    ap.add_argument('--opt', default='-O1')
    ap.add_argument('--include', action='append', default=[])
    ap.add_argument('--link-arg', action='append', default=[])
    a = ap.parse_args()
    clang = shutil.which(a.clang)
    if not clang:
        ap.error(f'Clang executable not found: {a.clang}')
    if not a.source.is_file(): ap.error(f'source file not found: {a.source}')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='chronos-instrument-') as td:
        raw = Path(td) / 'raw.ll'; inst = Path(td) / 'instrumented.ll'
        cmd = [clang, '-S', '-emit-llvm', '-g', a.opt, '-pthread']
        cmd += ['-x', 'c++' if a.cxx else 'c']
        for inc in a.include: cmd += ['-I', inc]
        cmd += [str(a.source.resolve()), '-o', str(raw)]
        subprocess.run(cmd, check=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name('llvm_instrument.py')), str(raw), '-o', str(inst)], check=True)
        lib = ROOT / 'build' / 'libchronos.so'
        if not lib.exists():
            raise SystemExit('Chronos runtime missing; run `make all` first')
        link = [clang, str(inst), '-o', str(a.output.resolve()), '-pthread', '-L', str(lib.parent), '-lchronos', f'-Wl,-rpath,{lib.parent}'] + a.link_arg
        subprocess.run(link, check=True)
    print(f'Instrumented executable: {a.output.resolve()}')
    print('Run under Chronos with: python3 tools/chronos.py explore <executable> --strategy dpor')
    return 0
if __name__ == '__main__': raise SystemExit(main())

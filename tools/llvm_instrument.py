#!/usr/bin/env python3
"""Instrument textual LLVM IR loads/stores with Chronos hooks.

This is a small, dependency-light LLVM IR transformation pass. It targets the
opaque-pointer IR emitted by Clang 15+ and deliberately rejects pointer forms
it cannot safely parse rather than silently rewriting them.
"""
import argparse
import re
import sys
from pathlib import Path

PTR = r'(?:%[-A-Za-z$._0-9]+|@[-A-Za-z$._0-9]+|null)'

def type_size(ty):
    ty = ty.strip()
    m = re.fullmatch(r'i(\d+)', ty)
    if m:
        return max(1, (int(m.group(1)) + 7) // 8)
    if ty == 'ptr':
        return 8
    if ty == 'half': return 2
    if ty in ('float',): return 4
    if ty in ('double',): return 8
    if ty in ('x86_fp80',): return 16
    m = re.fullmatch(r'<\s*(\d+)\s+x\s+(i\d+|half|float|double|ptr)\s*>', ty)
    if m:
        elem = type_size(m.group(2))
        return int(m.group(1)) * elem if elem else None
    m = re.fullmatch(r'\[\s*(\d+)\s+x\s+(i\d+|half|float|double|ptr)\s*\]', ty)
    if m:
        elem = type_size(m.group(2))
        return int(m.group(1)) * elem if elem else None
    return None

def _llvm_bytes(value):
    raw = value.encode('utf-8') + b'\0'
    return '[' + str(len(raw)) + ' x i8] c"' + ''.join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\' else f'\\{b:02X}' for b in raw) + '"'

def _str_ptr(symbol, value):
    ty = '[' + str(len(value.encode('utf-8') + b'\0')) + ' x i8]'
    return f'ptr getelementptr inbounds ({ty}, ptr @{symbol}, i64 0, i64 0)'

def instrument(text):
    lines = text.splitlines()
    counts = {'read': 0, 'write': 0, 'atomic': 0, 'skipped': 0}
    # LLVM debug metadata lets the runtime attach race findings to source lines.
    loc_lines = {}
    for line in lines:
        m = re.match(r'!([0-9]+)\s*=\s*!DILocation\(line:\s*([0-9]+)', line)
        if m: loc_lines[m.group(1)] = int(m.group(2))
    source_match = next((re.search(r'^source_filename\s*=\s*"(.*)"', l) for l in lines if l.startswith('source_filename')), None)
    source_name = source_match.group(1) if source_match else ''
    source_name = source_name.replace('\\\\', '\\').replace('\\"', '"')
    strings = {}
    if source_name:
        strings['.chronos.file'] = source_name
    def pointer_name(ptr):
        if ptr.startswith('@'):
            return ptr[1:].split('.', 1)[0]
        return ''
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith((';', '!', 'declare ', 'define ', 'attributes ', 'source_filename', 'target ')):
            out.append(line); continue
        dbg = re.search(r'!dbg\s*!([0-9]+)', line)
        line_no = loc_lines.get(dbg.group(1), 0) if dbg else 0
        file_arg = _str_ptr('.chronos.file', source_name) if source_name else 'ptr null'
        m = re.search(r'\batomicrmw\s+\w+\s+ptr\s+(' + PTR + r')\s*,', line)
        if m:
            out.append(f'  call void @chronos_atomic_point(ptr {m.group(1)}, i32 2, ptr null, {file_arg}, i32 {line_no})')
            counts['atomic'] += 1; out.append(line); continue
        m = re.search(r'\bcmpxchg\s+ptr\s+(' + PTR + r')\s*,', line)
        if m:
            out.append(f'  call void @chronos_atomic_point(ptr {m.group(1)}, i32 2, ptr null, {file_arg}, i32 {line_no})')
            counts['atomic'] += 1; out.append(line); continue
        m = re.search(r'\bload\s+atomic\s+[^,]+,\s*ptr\s+(' + PTR + r')\b', line)
        if m:
            out.append(f'  call void @chronos_atomic_point(ptr {m.group(1)}, i32 0, ptr null, {file_arg}, i32 {line_no})')
            counts['atomic'] += 1; out.append(line); continue
        m = re.search(r'\bstore\s+atomic\s+[^,]+,\s*ptr\s+(' + PTR + r')\b', line)
        if m:
            out.append(f'  call void @chronos_atomic_point(ptr {m.group(1)}, i32 1, ptr null, {file_arg}, i32 {line_no})')
            counts['atomic'] += 1; out.append(line); continue
        m = re.search(r'\bload\s+(?:(?:volatile)\s+)?([^,]+),\s*ptr\s+(' + PTR + r')(?=\s|,)', line)
        if m and ' atomic ' not in (' ' + line + ' '):
            size, ptr = type_size(m.group(1)), m.group(2)
            if size:
                nm = pointer_name(ptr)
                if nm: strings['.chronos.name.' + nm] = nm
                name_arg = _str_ptr('.chronos.name.' + nm, nm) if nm else 'ptr null'
                out.append(f'  call void @chronos_access(ptr {ptr}, i32 {size}, i32 0, {name_arg}, {file_arg}, i32 {line_no})')
                counts['read'] += 1
            else: counts['skipped'] += 1
            out.append(line); continue
        m = re.search(r'\bstore\s+(?:(?:volatile)\s+)?([^,]+)\s+.+?,\s*ptr\s+(' + PTR + r')(?=\s|,)', line)
        if m and ' atomic ' not in (' ' + line + ' '):
            size, ptr = type_size(m.group(1)), m.group(2)
            if size:
                nm = pointer_name(ptr)
                if nm: strings['.chronos.name.' + nm] = nm
                name_arg = _str_ptr('.chronos.name.' + nm, nm) if nm else 'ptr null'
                out.append(f'  call void @chronos_access(ptr {ptr}, i32 {size}, i32 1, {name_arg}, {file_arg}, i32 {line_no})')
                counts['write'] += 1
            else: counts['skipped'] += 1
            out.append(line); continue
        out.append(line)
    # Add private constants before declarations/functions. All symbols are
    # compiler-generated and have stable names within this module.
    globals_out = []
    for symbol, value in sorted(strings.items()):
        ty = _llvm_bytes(value)
        globals_out.append(f'@{symbol} = private unnamed_addr constant {ty}, align 1')
    insert_at = next((i for i, l in enumerate(out) if l.startswith('declare ') or l.startswith('define ')), len(out))
    out[insert_at:insert_at] = globals_out + ([''] if globals_out else [])
    insert_at = next((i for i, l in enumerate(out) if l.startswith('define ')), len(out))
    declarations = []
    if not any('declare' in l and '@chronos_access(' in l for l in out):
        declarations.append('declare extern_weak void @chronos_access(ptr, i32, i32, ptr, ptr, i32)')
    if not any('declare' in l and '@chronos_atomic_point(' in l for l in out):
        declarations.append('declare extern_weak void @chronos_atomic_point(ptr, i32, ptr, ptr, i32)')
    out[insert_at:insert_at] = declarations + ([''] if declarations else [])
    return '\n'.join(out) + '\n', counts

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('-o', '--output', type=Path, required=True)
    a = ap.parse_args()
    text, counts = instrument(a.input.read_text(encoding='utf-8'))
    a.output.write_text(text, encoding='utf-8')
    print('Chronos LLVM instrumentation:', ', '.join(f'{k}={v}' for k, v in counts.items()))
    if counts['skipped']:
        print('warning: some aggregate/unsupported typed accesses were left uninstrumented', file=sys.stderr)

if __name__ == '__main__': main()

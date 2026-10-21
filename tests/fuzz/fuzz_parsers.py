#!/usr/bin/env python3
"""Mutation fuzzer for the three untrusted-text parsers of the runtime
(schedule, override and prefix files). The parsers are compiled into
build/schedule_check with AddressSanitizer + UBSan; every mutated input must
either be accepted (exit 0) or rejected cleanly (exit 1). Any other exit
status (sanitizer abort, signal, timeout) is a bug."""
import os, random, subprocess, sys, tempfile, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "build" / "schedule_check"
ENV = dict(os.environ, ASAN_OPTIONS="exitcode=99:detect_leaks=1", UBSAN_OPTIONS="exitcode=98:print_stacktrace=1")

HUGE = [b"99999999999999999999999999", b"-1", b"-0", b"0x7fffffff", b"4294967296", b"18446744073709551616", b"", b"1e9", b"+5", b" 7", b"00000000000000000000000012"]


def mutate(data: bytes, rnd: random.Random) -> bytes:
    b = bytearray(data)
    for _ in range(rnd.randint(1, 4)):
        k = rnd.randrange(10)
        if k == 0 and b:                       # flip bytes
            for _ in range(rnd.randint(1, 5)):
                b[rnd.randrange(len(b))] = rnd.randrange(256)
        elif k == 1:                           # insert random bytes
            pos = rnd.randrange(len(b) + 1)
            b[pos:pos] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 12)))
        elif k == 2 and b:                     # delete a chunk
            i = rnd.randrange(len(b)); j = min(len(b), i + rnd.randint(1, 40)); del b[i:j]
        elif k == 3 and b:                     # truncate
            del b[rnd.randrange(len(b)):]
        elif k == 4:                           # duplicate a line
            lines = bytes(b).split(b"\n")
            if lines:
                i = rnd.randrange(len(lines)); lines.insert(i, lines[i]); b = bytearray(b"\n".join(lines))
        elif k == 5:                           # replace a token by a nasty number
            toks = bytes(b).replace(b"\n", b" \n ").split(b" ")
            if toks:
                toks[rnd.randrange(len(toks))] = rnd.choice(HUGE)
                b = bytearray(b" ".join(toks).replace(b" \n ", b"\n"))
        elif k == 6:                           # NUL byte
            pos = rnd.randrange(len(b) + 1); b[pos:pos] = b"\x00"
        elif k == 7:                           # garbage line
            b += b"\n" + bytes(rnd.randrange(32, 127) for _ in range(rnd.randint(1, 60))) + b"\n"
        elif k == 8:                           # overlong line
            b += b"\n" + b"7" * rnd.randint(300, 900) + b"\n"
        elif k == 9:                           # wrong declared count
            b = bytearray(bytes(b).replace(b"steps ", b"steps 1" if rnd.random() < .5 else b"steps 0"))
    return bytes(b)


def run_fuzz(kind, seed_text: bytes, iterations: int, seed=1234):
    rnd = random.Random(seed)
    stats = collections.Counter()
    bad = []
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "in.txt")
        for i in range(iterations):
            data = mutate(seed_text, rnd) if i else seed_text
            with open(f, "wb") as fh:
                fh.write(data)
            try:
                p = subprocess.run([str(CHECK), kind, f], env=ENV, capture_output=True, timeout=20)
            except subprocess.TimeoutExpired:
                bad.append((i, "timeout", data[:200])); continue
            out = p.stdout.decode("latin1")
            if p.returncode == 0 and out.startswith("OK"):
                stats["accepted"] += 1
            elif p.returncode == 1 and out.startswith("REJECT"):
                stats["rejected"] += 1
            else:
                bad.append((i, f"rc={p.returncode}", data[:200], p.stderr.decode("latin1")[:300]))
    return stats, bad


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "tools"))
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    sched = (ROOT / "tests" / "fuzz" / "corpus_schedule.txt").read_bytes()
    over = (ROOT / "tests" / "fuzz" / "corpus_preempt.txt").read_bytes()
    pref = (ROOT / "tests" / "fuzz" / "corpus_prefix.txt").read_bytes()
    for kind, seed in (("sched", sched), ("over", over), ("prefix", pref)):
        st, bad = run_fuzz(kind, seed, n)
        print(kind, dict(st), "BAD:", len(bad))
        for b in bad[:5]:
            print("  ", b)

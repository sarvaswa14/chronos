#!/usr/bin/env python3
"""Figures for the Phase 1 slides/report, drawn from results/summary.json and recorded traces only."""
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
OUT = RES / "figures"
OUT.mkdir(parents=True, exist_ok=True)
CAT = {"start": "life", "exit": "life", "create": "life", "join": "life", "mutex_lock": "lock", "mutex_trylock": "lock", "mutex_unlock": "lock",
       "cond_wait": "cond", "cond_wake": "cond", "cond_signal": "cond", "cond_broadcast": "cond", "sem_wait": "sem", "sem_trywait": "sem",
       "sem_post": "sem", "barrier_wait": "bar", "barrier_leave": "bar", "once": "once", "read": "mem", "write": "mem",
       "atomic_load": "atom", "atomic_store": "atom", "atomic_rmw": "atom", "yield": "yield"}
COL = {"life": "#7a8696", "lock": "#2e7d32", "cond": "#6a1b9a", "sem": "#00838f", "bar": "#ad1457", "once": "#5d4037", "mem": "#1565c0", "atom": "#ef6c00", "yield": "#9e9e9e"}
NAME = {"life": "thread lifecycle", "lock": "mutex", "cond": "condition variable", "sem": "semaphore", "bar": "barrier", "once": "once", "mem": "annotated read/write", "atom": "atomic", "yield": "yield"}
INK, MUTED = "#1b2430", "#5d6b7c"


def load(path):
    ev, res, hdr = [], {}, None
    for line in open(path):
        o = json.loads(line)
        if "chronos_trace" in o: hdr = o
        elif "end" in o: res = o["result"]
        else: ev.append(o)
    return hdr, ev, res


def has_bit(hexs, t): return (int(hexs, 16) >> t) & 1


def timeline(ax, trace, title):
    hdr, ev, res = load(trace)
    n, N = hdr["threads"], len(ev)
    for t in range(n):
        ax.add_patch(Rectangle((-0.5, t - 0.45), N, 0.9, color="#f1f3f6" if t % 2 == 0 else "#ffffff", zorder=0))
    for i in range(1, N):
        if ev[i]["t"] != ev[i - 1]["t"]:
            pre = has_bit(ev[i]["run"], ev[i - 1]["t"])
            ax.plot([i - 0.5, i - 0.5], [-0.5, n - 0.5], color="#c62828" if pre else "#9aa7b6", lw=1, ls="--" if pre else ":", zorder=1)
    for i, e in enumerate(ev):
        for s in e.get("hb", []):
            a = ev[s]
            if a["t"] != e["t"]:
                ax.annotate("", xy=(i, e["t"]), xytext=(s, a["t"]), arrowprops=dict(arrowstyle="-|>", color="#2457d6", alpha=.45, lw=.9, shrinkA=3, shrinkB=3), zorder=2)
    racing = set()
    for f in res.get("findings", []):
        if f["type"] == "RACE":
            racing |= {f["a"]["step"], f["b"]["step"]}
    for i, e in enumerate(ev):
        c = COL[CAT.get(e["op"], "yield")]
        ax.add_patch(Rectangle((i - 0.38, e["t"] - 0.28), 0.76, 0.56, color=c, ec="#c62828" if i in racing else "none", lw=2.2 if i in racing else 0, zorder=3))
    ax.set_xlim(-0.6, N + 0.4); ax.set_ylim(n - 0.5, -0.6)
    ax.set_yticks(range(n)); ax.set_yticklabels([f"T{t}" + (" main" if t == 0 else "") for t in range(n)], color=INK)
    ax.set_xlabel("scheduling step", color=MUTED)
    sw, pr = res.get("switches", 0), res.get("preemptions", 0)
    ax.set_title(f"{title}  -  {N} steps, {sw} context switches, {pr} preemptive", loc="left", fontsize=10.5, color=INK)
    for sp in ("top", "right", "left"): ax.spines[sp].set_visible(False)
    ax.tick_params(length=0, colors=MUTED)
    if res.get("failure"):
        ax.text(N - 0.2, 0.0 if False else n - 0.55, "x " + res["failure"]["signature"], ha="right", va="bottom", fontsize=8.5, color="#c62828", weight="bold")


def fig_timeline(name, label):
    d = RES / "bugs" / name
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.2), constrained_layout=True)
    timeline(axes[0], d / "failing.trace.jsonl", "Random failing run")
    timeline(axes[1], d / "minimal.trace.jsonl", "After delta-debugging")
    handles = [Rectangle((0, 0), 1, 1, color=COL[k]) for k in COL]
    fig.legend(handles, [NAME[k] for k in COL], ncol=9, loc="lower center", fontsize=7.5, frameon=False, bbox_to_anchor=(0.5, -0.045))
    fig.savefig(OUT / f"timeline_{name}.png", dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_find(S):
    bugs = S["bugs"]
    names = [b["name"] for b in bugs]
    fig, ax = plt.subplots(figsize=(11, 4.4), constrained_layout=True)
    w = 0.27
    series = [("random", [b["random"]["schedules_to_first_failure"]["median"] for b in bugs], "#2457d6"),
              ("PCT", [b["pct"]["schedules_to_first_failure"]["median"] for b in bugs], "#ef6c00"),
              ("ICB (preemption-bounded DFS)", [b["icb"]["schedules_to_first_failure"] if b["icb"]["found"] else None for b in bugs], "#2e7d32")]
    for k, (lab, vals, c) in enumerate(series):
        xs = [i + (k - 1) * w for i in range(len(names))]
        ys = [v if v is not None else 0.0 for v in vals]
        ax.bar(xs, ys, w, label=lab, color=c)
        for x, v in zip(xs, vals):
            ax.text(x, (v or 1) * 1.12, "not found" if v is None else (f"{v:g}"), ha="center", va="bottom", fontsize=7.5, color=INK, rotation=0 if v is not None else 90)
    ax.set_yscale("log"); ax.set_ylim(0.8, 600)
    ax.set_xticks(range(len(names))); ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=8, color=INK)
    ax.set_ylabel("schedules executed until first failure\n(median of 20 runs; log scale)", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    fig.savefig(OUT / "find_comparison.png", dpi=170, facecolor="white"); plt.close(fig)


def fig_min(S):
    bugs = [b for b in S["bugs"] if "minimal" in b["minimize"]]
    names = [b["name"] for b in bugs]
    fig, ax = plt.subplots(figsize=(11, 4.0), constrained_layout=True)
    w = 0.38
    o = [b["minimize"]["original"]["switches"] for b in bugs]
    m = [b["minimize"]["minimal"]["switches"] for b in bugs]
    xs = range(len(names))
    ax.bar([x - w / 2 for x in xs], o, w, color="#9aa7b6", label="failing schedule found by random search")
    ax.bar([x + w / 2 for x in xs], m, w, color="#c62828", label="1-minimal schedule (verified by strict replay)")
    for x, a, b in zip(xs, o, m):
        ax.text(x - w / 2, a + .4, str(a), ha="center", fontsize=8, color=INK); ax.text(x + w / 2, b + .4, str(b), ha="center", fontsize=8, color=INK)
    ax.set_xticks(list(xs)); ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=8, color=INK)
    ax.set_ylabel("context switches", color=MUTED)
    ax.legend(frameon=False, fontsize=9)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    fig.savefig(OUT / "minimization.png", dpi=170, facecolor="white"); plt.close(fig)


if __name__ == "__main__":
    S = json.load(open(RES / "summary.json"))
    for name in ("lost_update", "atomicity_violation", "lock_order_deadlock"):
        fig_timeline(name, name)
    fig_find(S); fig_min(S)
    print("figures:", sorted(p.name for p in OUT.iterdir()))

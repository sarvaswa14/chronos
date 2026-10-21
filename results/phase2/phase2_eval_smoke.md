# Chronos Phase 2 strategy and heuristic ablation

Generated on `Linux-6.18.55-x86_64-with-glibc2.41`; reps=1; schedule budget=20; timeout=5s.

A run that reaches its schedule budget is not considered complete. Wall time is per search.

| case | strategy | heuristic | found | median schedules | median seconds | complete runs |
|---|---|---|---:|---:|---:|---:|
| lost_update | random | — | 1/1 | 2 | 0.044 | 0/1 |
| lost_update | pct | — | 1/1 | 11 | 0.122 | 0/1 |
| lost_update | dfs | — | 0/1 | 20 | 0.512 | 0/1 |
| lost_update | dpor | baseline | 0/1 | 20 | 0.617 | 0/1 |
| lost_update | dpor | risk | 1/1 | 2 | 0.025 | 0/1 |

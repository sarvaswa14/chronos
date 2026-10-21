# Chronos Phase 2 strategy and heuristic ablation

Generated on `Linux-6.18.55-x86_64-with-glibc2.41`; reps=1; schedule budget=10; timeout=20s.

A run that reaches its schedule budget is not considered complete. Wall time is per search.

| case | strategy | heuristic | found | median schedules | median seconds | complete runs |
|---|---|---|---:|---:|---:|---:|
| lost_update | random | — | 1/1 | 2 | 0.038 | 0/1 |
| lost_update | pct | — | 0/1 | 10 | 0.098 | 0/1 |
| lost_update | dfs | — | 0/1 | 10 | 0.113 | 0/1 |
| lost_update | dpor | baseline | 0/1 | 10 | 0.239 | 0/1 |
| lost_update | dpor | risk | 1/1 | 2 | 0.018 | 0/1 |
| order_violation | random | — | 1/1 | 1 | 0.009 | 0/1 |
| order_violation | pct | — | 1/1 | 1 | 0.013 | 0/1 |
| order_violation | dfs | — | 1/1 | 2 | 0.014 | 0/1 |
| order_violation | dpor | baseline | 1/1 | 2 | 0.016 | 0/1 |
| order_violation | dpor | risk | 1/1 | 2 | 0.010 | 0/1 |

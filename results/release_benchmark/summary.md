# Chronos Phase 1 - benchmark results

Generated 2026-10-21 09:14:01 - Linux 6.18.55, 5 CPU - repetitions per strategy: 5 - mode: quick

## 1. Bug finding

Schedules executed until the first failure (median over independent repetitions; `found` = repetitions that found the bug within the budget). ICB = deterministic preemption-bounded search.

| bug | class | native fails | random found | random median | PCT found | PCT median | ICB bound | ICB schedules | random fail-rate | PCT fail-rate |
|---|---|---|---|---|---|---|---|---|---|---|
| lost_update | lost update (non-atomic read-modify-write) | 0/30 | 5/5 | 1 | 5/5 | 23 | 1 | 24 | 62.0% | 11.0% |
| atomicity_violation | atomicity violation (race-free) | 0/30 | 5/5 | 2 | 5/5 | 6 | 1 | 24 | 75.0% | 13.0% |
| lock_order_deadlock | lock-order inversion (AB/BA) | 0/30 | 5/5 | 1 | 5/5 | 22 | 1 | 14 | 25.0% | 3.0% |
| lost_wakeup | lost wake-up (condvar without predicate) | 1/30 | 5/5 | 3 | 5/5 | 1 | 0 | 2 | 31.0% | 32.0% |
| dining_philosophers | circular wait over 3 locks | 0/30 | 5/5 | 4 | 5/5 | 68 | 1 | 151 | 9.0% | 4.0% |
| order_violation | order violation (use before init) | 0/30 | 5/5 | 3 | 5/5 | 2 | 1 | 2 | 45.0% | 55.0% |
| double_checked_init | broken double-checked initialisation | 1/30 | 5/5 | 1 | 5/5 | 6 | 1 | 10 | 69.0% | 14.0% |
| cond_if_not_while | condvar `if` instead of `while` | 0/30 | 5/5 | 8 | 0/3 | None | 1 | 84 | 9.0% | 0.0% |
| cpp_atomicity | atomicity violation in C++ (std::thread/mutex) | 3/30 | 5/5 | 1 | 5/5 | 4 | 1 | 15 | 33.0% | 16.0% |

## 2. Replay and minimisation

| bug | failure signature | steps | replay matches | context switches orig -> min | deviations orig -> min | preemptions orig -> min | 1-minimal | strict-replay verified | test runs |
|---|---|---|---|---|---|---|---|---|---|
| lost_update | `ASSERT@lost_update.c:41` | 44 | 100/100 | 27 -> 4 | 22 -> 1 | 21 -> 1 | yes | yes | 8 |
| atomicity_violation | `ASSERT@atomicity_violation.c:45` | 34 | 100/100 | 13 -> 5 | 10 -> 2 | 9 -> 1 | yes | yes | 7 |
| lock_order_deadlock | `DEADLOCK[join,mutex_lock,mutex_lock]` | 6 | 100/100 | 5 -> 2 | 5 -> 1 | 4 -> 1 | yes | yes | 5 |
| lost_wakeup | `DEADLOCK[cond_wait,join]` | 11 | 100/100 | 4 -> 2 | 3 -> 1 | 2 -> 0 | yes | yes | 5 |
| dining_philosophers | `DEADLOCK[join,mutex_lock,mutex_lock,mutex_lock]` | 9 | 100/100 | 5 -> 5 | 5 -> 5 | 4 -> 4 | yes | yes | 16 |
| order_violation | `ASSERT@order_violation.c:23` | 8 | 100/100 | 5 -> 1 | 5 -> 1 | 5 -> 1 | yes | yes | 5 |
| double_checked_init | `ASSERT@double_checked_init.c:36` | 24 | 100/100 | 9 -> 6 | 6 -> 1 | 5 -> 1 | yes | yes | 7 |
| cond_if_not_while | `ASSERT@cond_if_not_while.c:21` | 22 | 100/100 | 10 -> 4 | 8 -> 2 | 5 -> 1 | yes | yes | 7 |
| cpp_atomicity | `ASSERT@cpp_atomicity.cpp:32` | 22 | 100/100 | 11 -> 4 | 8 -> 1 | 7 -> 1 | yes | yes | 6 |

## 3. Detector findings (non-fatal, over the rate runs)

| bug | expected RACE | RACE seen (runs) | expected LOCKORDER | LOCKORDER seen (runs) | verdict |
|---|---|---|---|---|---|
| lost_update | yes | 200/200 | no | 0/200 | OK |
| atomicity_violation | no | 0/200 | no | 0/200 | OK |
| lock_order_deadlock | no | 0/200 | yes | 172/200 | OK |
| lost_wakeup | no | 0/200 | no | 0/200 | OK |
| dining_philosophers | no | 0/200 | yes | 187/200 | OK |
| order_violation | yes | 100/200 | no | 0/200 | OK |
| double_checked_init | yes | 200/200 | no | 0/200 | OK |
| cond_if_not_while | no | 0/200 | no | 0/200 | OK |
| cpp_atomicity | no | 0/200 | no | 0/200 | OK |

## 4. Controls (correct programs; race and lock-order findings made fatal)

| control | schedules executed | failures | detector findings | anomalies | verdict |
|---|---|---|---|---|---|
| counter_mutex | 500 | 0 | 0 | 0 | PASS |
| prodcons_cv | 600 | 0 | 0 | 0 | PASS |
| barrier_phases | 500 | 0 | 0 | 0 | PASS |
| semaphore_lock | 500 | 0 | 0 | 0 | PASS |
| philosophers_ordered | 500 | 0 | 0 | 0 | PASS |
| init_once | 428 | 0 | 0 | 0 | PASS |
| atomic_mp | 218 | 0 | 0 | 0 | PASS |
| cpp_condvar_queue | 600 | 0 | 0 | 0 | PASS |

## 5. Acceptance checks

- [x] **lost_update**: bug found by at least one strategy
- [x] **lost_update**: strict replay 100/100 with matching signature
- [x] **lost_update**: minimised schedule reproduces and is 1-minimal
- [x] **lost_update**: minimised schedule strictly replays
- [x] **lost_update**: minimised schedule not larger than original
- [x] **lost_update**: detector findings match expectation
- [x] **atomicity_violation**: bug found by at least one strategy
- [x] **atomicity_violation**: strict replay 100/100 with matching signature
- [x] **atomicity_violation**: minimised schedule reproduces and is 1-minimal
- [x] **atomicity_violation**: minimised schedule strictly replays
- [x] **atomicity_violation**: minimised schedule not larger than original
- [x] **atomicity_violation**: detector findings match expectation
- [x] **lock_order_deadlock**: bug found by at least one strategy
- [x] **lock_order_deadlock**: strict replay 100/100 with matching signature
- [x] **lock_order_deadlock**: minimised schedule reproduces and is 1-minimal
- [x] **lock_order_deadlock**: minimised schedule strictly replays
- [x] **lock_order_deadlock**: minimised schedule not larger than original
- [x] **lock_order_deadlock**: detector findings match expectation
- [x] **lost_wakeup**: bug found by at least one strategy
- [x] **lost_wakeup**: strict replay 100/100 with matching signature
- [x] **lost_wakeup**: minimised schedule reproduces and is 1-minimal
- [x] **lost_wakeup**: minimised schedule strictly replays
- [x] **lost_wakeup**: minimised schedule not larger than original
- [x] **lost_wakeup**: detector findings match expectation
- [x] **dining_philosophers**: bug found by at least one strategy
- [x] **dining_philosophers**: strict replay 100/100 with matching signature
- [x] **dining_philosophers**: minimised schedule reproduces and is 1-minimal
- [x] **dining_philosophers**: minimised schedule strictly replays
- [x] **dining_philosophers**: minimised schedule not larger than original
- [x] **dining_philosophers**: detector findings match expectation
- [x] **order_violation**: bug found by at least one strategy
- [x] **order_violation**: strict replay 100/100 with matching signature
- [x] **order_violation**: minimised schedule reproduces and is 1-minimal
- [x] **order_violation**: minimised schedule strictly replays
- [x] **order_violation**: minimised schedule not larger than original
- [x] **order_violation**: detector findings match expectation
- [x] **double_checked_init**: bug found by at least one strategy
- [x] **double_checked_init**: strict replay 100/100 with matching signature
- [x] **double_checked_init**: minimised schedule reproduces and is 1-minimal
- [x] **double_checked_init**: minimised schedule strictly replays
- [x] **double_checked_init**: minimised schedule not larger than original
- [x] **double_checked_init**: detector findings match expectation
- [x] **cond_if_not_while**: bug found by at least one strategy
- [x] **cond_if_not_while**: strict replay 100/100 with matching signature
- [x] **cond_if_not_while**: minimised schedule reproduces and is 1-minimal
- [x] **cond_if_not_while**: minimised schedule strictly replays
- [x] **cond_if_not_while**: minimised schedule not larger than original
- [x] **cond_if_not_while**: detector findings match expectation
- [x] **cpp_atomicity**: bug found by at least one strategy
- [x] **cpp_atomicity**: strict replay 100/100 with matching signature
- [x] **cpp_atomicity**: minimised schedule reproduces and is 1-minimal
- [x] **cpp_atomicity**: minimised schedule strictly replays
- [x] **cpp_atomicity**: minimised schedule not larger than original
- [x] **cpp_atomicity**: detector findings match expectation
- [x] **counter_mutex**: no failure, no finding, no anomaly in 500 schedules
- [x] **prodcons_cv**: no failure, no finding, no anomaly in 600 schedules
- [x] **barrier_phases**: no failure, no finding, no anomaly in 500 schedules
- [x] **semaphore_lock**: no failure, no finding, no anomaly in 500 schedules
- [x] **philosophers_ordered**: no failure, no finding, no anomaly in 500 schedules
- [x] **init_once**: no failure, no finding, no anomaly in 428 schedules
- [x] **atomic_mp**: no failure, no finding, no anomaly in 218 schedules
- [x] **cpp_condvar_queue**: no failure, no finding, no anomaly in 600 schedules

**Overall: ALL CHECKS PASSED**

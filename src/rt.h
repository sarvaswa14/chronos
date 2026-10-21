/* rt.h - internal declarations of the Chronos runtime (libchronos.so).
 *
 * Execution model (see docs/SCHEDULER_PROTOCOL.md):
 *   - exactly one thread (the "baton holder") runs program code at a time;
 *   - every synchronization operation and every annotated memory access is a
 *     scheduling point at which the baton holder declares a *pending
 *     operation* and the coordinator picks the next thread to run among the
 *     threads whose pending operation is enabled;
 *   - blocking primitives (mutex, condvar, semaphore, barrier, join, once)
 *     are modelled by the coordinator, never executed by the OS, so a
 *     blocked thread is simply a thread whose pending operation is disabled.
 */
#ifndef CH_RT_H
#define CH_RT_H

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <pthread.h>
#include <setjmp.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>

#define CH_MAXT 64            /* max threads (runnable set is a 64-bit mask) */
#define CH_LO_MAXLOCKS 256    /* lock-order graph capacity                   */
#define CH_MAX_FINDINGS 64
#define CH_SCHEMA_VERSION 1

/* ---- operations ------------------------------------------------------- */
typedef enum {
  OP_START = 0,
  OP_EXIT,
  OP_CREATE,
  OP_JOIN,
  OP_MUTEX_LOCK,
  OP_MUTEX_TRYLOCK,
  OP_MUTEX_UNLOCK,
  OP_COND_WAIT,
  OP_COND_WAKE,
  OP_COND_SIGNAL,
  OP_COND_BROADCAST,
  OP_SEM_WAIT,
  OP_SEM_TRYWAIT,
  OP_SEM_POST,
  OP_BARRIER_WAIT,
  OP_BARRIER_LEAVE,
  OP_ONCE,
  OP_READ,
  OP_WRITE,
  OP_ATOMIC_LOAD,
  OP_ATOMIC_STORE,
  OP_ATOMIC_RMW,
  OP_YIELD,
  OP__COUNT
} ch_op;

extern const char *const ch_op_names[OP__COUNT];
int ch_op_from_name(const char *s); /* -1 if unknown */

/* ---- failure kinds ------------------------------------------------------ */
typedef enum {
  F_ASSERT = 0,
  F_DEADLOCK,
  F_RACE,
  F_LOCKORDER,
  F_CRASH,
  F_MISUSE,
  F_UNSUPPORTED,
  F_STEP_LIMIT,
  F_HANG,
  F__COUNT
} ch_fkind;
extern const char *const ch_fkind_names[F__COUNT];

/* ---- sync objects --------------------------------------------------------- */
typedef enum { OBJ_MUTEX, OBJ_COND, OBJ_SEM, OBJ_BARRIER, OBJ_ONCE, OBJ_ATOMIC } ch_okind;
extern const char *const ch_okind_names[6];

enum { MTX_NORMAL = 0, MTX_RECURSIVE = 1, MTX_ERRORCHECK = 2 };

typedef struct ch_obj {
  const void *addr;
  int id;
  ch_okind kind;
  int live;
  char name[40];
  struct ch_obj *next; /* hash chain */
  uint32_t vc[CH_MAXT]; /* synchronisation clock */
  int last_rel_step;    /* step index of last release (HB edge source) */
  union {
    struct { int owner, count, type; } mtx;
    struct { int waiters[CH_MAXT]; int nw; } cond;
    struct { int count; } sem;
    struct { int needed, arrived, gen; } bar;
    struct { int state, owner; } once; /* 0 new, 1 running, 2 done */
  } u;
} ch_obj;

/* ---- threads -------------------------------------------------------------- */
typedef enum { TS_UNUSED = 0, TS_ACTIVE, TS_FINISHED } ch_tstate;

typedef struct ch_thr {
  int tid;
  ch_tstate state;
  _Atomic uint32_t go; /* binary semaphore (futex word) */
  pthread_t real;
  int have_real, detached, joined;
  uint32_t opcnt; /* operations executed so far by this thread */
  /* pending operation */
  int pend_op;
  ch_obj *pend_obj, *pend_obj2;
  int pend_arg;
  const void *pend_addr;
  unsigned pend_size;
  const char *pend_name, *pend_file;
  int pend_line;
  /* condition-variable wake bookkeeping */
  int signaled, wake_src;
  int create_step, exit_step; /* HB edge sources */
  uint32_t wake_vc[CH_MAXT];
  uint32_t vc[CH_MAXT];
  void *(*start)(void *);
  void *arg;
  void *retval;
  jmp_buf exit_jmp; /* pthread_exit unwinds to the trampoline (no cleanup handlers) */
  /* lock-order tracking: stack of held lock ids */
  int held[64];
  int nheld;
  uint64_t prio; /* PCT priority */
} ch_thr;

/* ---- recorded step -------------------------------------------------------- */
typedef struct {
  uint32_t step;
  int16_t tid;
  uint32_t opidx; /* index of this op within its thread */
  uint8_t op;
  int32_t obj;    /* object id or -1 */
  int32_t aux;
  uint64_t run;   /* runnable set at the decision */
  int32_t hb[3];  /* happens-before source steps (-1 = none) */
  const char *name, *file;
  int line;
} ch_step;

/* ---- findings --------------------------------------------------------------- */
typedef struct {
  ch_fkind kind;
  char sig[200];
  char msg[240];
  int tid, step;
  const char *file;
  int line;
  /* race details */
  struct { int tid, step, is_write, line; const char *file, *name; } a, b;
} ch_finding;

/* ---- configuration ----------------------------------------------------------- */
typedef enum { ST_RANDOM, ST_PCT, ST_REPLAY, ST_PREEMPT, ST_PREFIX, ST_BASE } ch_strategy;

typedef struct {
  ch_strategy strategy;
  uint64_t seed;
  int pct_depth, pct_steps;
  uint32_t max_steps;
  uint32_t fatal_mask;     /* bit per ch_fkind */
  int spurious_budget;
  int watchdog_ms;
  int prefix_ok;           /* replay: continue with base policy after schedule */
  const char *sched_in, *preempt_in, *prefix_in, *trace_out, *sched_out, *result_out;
  int disable;
} ch_config;

/* ---- schedule / preemption files (rt_schedfile.c) --------------------------- */
typedef struct {
  uint32_t step;
  int tid;
  uint32_t opidx;
  int op;
  int obj;
  uint64_t run;
} ch_sched_rec;

typedef struct {
  ch_sched_rec *recs;
  uint32_t n;
  char program[128];
  char fail[200];
} ch_sched;

typedef struct { uint8_t *v; uint32_t n; } ch_prefix;

typedef struct { int prev, cnt, next; } ch_override;
typedef struct { ch_override *v; uint32_t n; } ch_overrides;

/* return 0 on success, else a negative code and a message in err */
int ch_sched_parse(const char *text, size_t len, ch_sched *out, char *err, size_t errsz);
int ch_sched_load(const char *path, ch_sched *out, char *err, size_t errsz);
void ch_sched_free(ch_sched *s);
int ch_over_parse(const char *text, size_t len, ch_overrides *out, char *err, size_t errsz);
int ch_over_load(const char *path, ch_overrides *out, char *err, size_t errsz);
void ch_over_free(ch_overrides *o);
int ch_prefix_parse(const char *text, size_t len, ch_prefix *out, char *err, size_t errsz);
int ch_prefix_load(const char *path, ch_prefix *out, char *err, size_t errsz);
void ch_prefix_free(ch_prefix *p);

/* ---- core (rt_core.c) ---------------------------------------------------------- */
extern ch_config g_cfg;
extern ch_thr g_thr[CH_MAXT];
extern int g_nthreads;
extern ch_step *g_steps;
extern uint32_t g_nsteps;
extern int g_active;
extern __thread ch_thr *tls_self;
extern ch_finding g_findings[CH_MAX_FINDINGS];
extern int g_nfindings;
extern ch_finding g_failure;
extern int g_have_failure;
extern uint32_t g_spurious_left;

ch_thr *ch_self(void);                 /* NULL when inactive/foreign thread */
void ch_sched_point(ch_thr *t, int op, ch_obj *o, ch_obj *o2, int arg,
                    const void *addr, unsigned size, const char *name,
                    const char *file, int line);
void ch_yield_to_scheduler(ch_thr *t); /* decide + hand off + park */
void ch_thread_finish(ch_thr *t, void *rv);
ch_thr *ch_thread_new(ch_thr *parent, void *(*start)(void *), void *arg);
int ch_runnable(const ch_thr *t);
void ch_fail(ch_fkind k, const char *sig, const char *file, int line, const char *fmt, ...)
    __attribute__((format(printf, 5, 6), noreturn));
void ch_report(ch_finding *f); /* record finding; fails if kind is fatal */
void ch_unsupported(const char *what) __attribute__((noreturn));
void ch_misuse(const char *code, const char *fmt, ...)
    __attribute__((format(printf, 2, 3), noreturn));
const char *ch_base(const char *path);
int ch_cur_step(void); /* index of the step being executed */
ch_step *ch_cur_step_rec(void);

/* ---- objects / vector clocks (rt_core.c) ----------------------------------------- */
ch_obj *ch_obj_get(const void *addr, ch_okind kind, int create);
void ch_obj_destroy(const void *addr);
ch_obj *ch_obj_by_id(int id);
int ch_nobjs(void);
void ch_vc_join(uint32_t *dst, const uint32_t *src);
void ch_hb_acquire(ch_thr *t, ch_obj *o);  /* t.vc |= o.vc, records edge */
void ch_hb_release(ch_thr *t, ch_obj *o);  /* o.vc = t.vc; tick           */
void ch_hb_edge(int src_step);

/* ---- race detector (rt_race.c) ------------------------------------------------------ */
void ch_race_access(ch_thr *t, const void *addr, unsigned size, int is_write,
                    const char *name, const char *file, int line);

/* ---- lock order (rt_lockorder.c) ------------------------------------------------------- */
void ch_lo_acquire(ch_thr *t, ch_obj *m);
void ch_lo_release(ch_thr *t, ch_obj *m);
void ch_lo_push(ch_thr *t, ch_obj *m); /* held-set only (trylock: no order edge) */
void ch_name_object(const void *addr, const char *name);

/* ---- output (rt_out.c) ------------------------------------------------------------------- */
enum { FIN_NORMAL = 0, FIN_FAIL = 1, FIN_SIGNAL = 2, FIN_WATCHDOG = 3 };
void ch_finalize(int mode);
void ch_set_divergence(uint32_t step, const char *reason);
extern int g_divergence;
extern char g_prog[256];
extern char g_div_reason[200];
extern uint32_t g_div_step;
extern int g_quiet;
extern uint32_t g_replay_n;
extern int g_replay_on;

/* real libc entry points (resolved lazily) */
extern int (*real_pthread_create)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);
extern int (*real_pthread_join)(pthread_t, void **);
extern void (*real_pthread_exit)(void *);
extern int (*real_pthread_mutexattr_gettype)(const pthread_mutexattr_t *, int *);
void ch_resolve_real(void);
void *ch_trampoline(void *p);

#endif /* CH_RT_H */

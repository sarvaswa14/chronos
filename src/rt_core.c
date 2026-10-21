/* rt_core.c - the Chronos coordinator.
 *
 * Invariants (checked by the test-suite, argued in docs/SCHEDULER_PROTOCOL.md):
 *   I1  At most one thread executes program code between scheduling points.
 *   I2  A thread is chosen only if its pending operation is enabled in the
 *       model; blocking is never delegated to the OS.
 *   I3  Every decision is recorded (step, thread, op index, op, object,
 *       runnable set) before the chosen thread is released.
 *   I4  Shared coordinator state is touched only by the baton holder; hand-off
 *       is a release-store/acquire-exchange on the target's futex word.
 */
#include "rt.h"

#include <dlfcn.h>
#include <linux/futex.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

/* ---- globals ------------------------------------------------------------- */
ch_config g_cfg;
ch_thr g_thr[CH_MAXT];
int g_nthreads;
ch_step *g_steps;
uint32_t g_nsteps;
int g_active;
static int g_exiting;
__thread ch_thr *tls_self;
ch_finding g_findings[CH_MAX_FINDINGS];
int g_nfindings;
ch_finding g_failure;
int g_have_failure;
uint32_t g_spurious_left;
uint32_t g_replay_n;
int g_replay_on;

static ch_sched g_sched;
static ch_overrides g_over;
static ch_prefix g_prefix;
static int g_last_tid;
static uint32_t g_last_opcnt;
static _Atomic uint32_t g_progress;
static uint64_t g_rng;
static uint32_t g_cp[64];
static int g_ncp, g_next_cp;

int (*real_pthread_create)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);
int (*real_pthread_join)(pthread_t, void **);
void (*real_pthread_exit)(void *);
int (*real_pthread_mutexattr_gettype)(const pthread_mutexattr_t *, int *);
static int (*real_nanosleep_fn)(const struct timespec *, struct timespec *);

void ch_resolve_real(void) {
  if (real_pthread_create) return;
  real_pthread_create = (int (*)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *))dlsym(RTLD_NEXT, "pthread_create");
  real_pthread_join = (int (*)(pthread_t, void **))dlsym(RTLD_NEXT, "pthread_join");
  real_pthread_exit = (void (*)(void *))dlsym(RTLD_NEXT, "pthread_exit");
  real_pthread_mutexattr_gettype =
      (int (*)(const pthread_mutexattr_t *, int *))dlsym(RTLD_NEXT, "pthread_mutexattr_gettype");
  real_nanosleep_fn = (int (*)(const struct timespec *, struct timespec *))dlsym(RTLD_NEXT, "nanosleep");
}

const char *ch_base(const char *path) {
  if (!path) return "";
  const char *b = path;
  for (const char *p = path; *p; p++)
    if (*p == '/') b = p + 1;
  return b;
}

/* ---- rng -------------------------------------------------------------------- */
static uint64_t splitmix(uint64_t *x) {
  uint64_t z = (*x += 0x9e3779b97f4a7c15ULL);
  z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
  z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
  return z ^ (z >> 31);
}
static uint64_t rnd64(void) {
  uint64_t x = g_rng;
  x ^= x >> 12;
  x ^= x << 25;
  x ^= x >> 27;
  g_rng = x;
  return x * 2685821657736338717ULL;
}

/* ---- futex binary semaphore ---------------------------------------------------- */
static void park(ch_thr *t) {
  for (;;) {
    if (atomic_exchange_explicit(&t->go, 0, memory_order_acq_rel) == 1) return;
    syscall(SYS_futex, &t->go, FUTEX_WAIT_PRIVATE, 0, NULL, NULL, 0);
  }
}
static void unpark(ch_thr *t) {
  atomic_store_explicit(&t->go, 1, memory_order_release);
  syscall(SYS_futex, &t->go, FUTEX_WAKE_PRIVATE, 1, NULL, NULL, 0);
}

/* ---- failure handling ---------------------------------------------------------------- */
static int fatal_kind(ch_fkind k) { return k != F_RACE && k != F_LOCKORDER ? 1 : (int)((g_cfg.fatal_mask >> k) & 1u); }

static void fill_failure(ch_finding *f, ch_fkind k, const char *sig, const char *file, int line, const char *msg) {
  memset(f, 0, sizeof *f);
  f->kind = k;
  snprintf(f->sig, sizeof f->sig, "%s", sig);
  snprintf(f->msg, sizeof f->msg, "%s", msg);
  f->tid = tls_self ? tls_self->tid : -1;
  f->step = (int)g_nsteps;
  f->file = file;
  f->line = line;
}

void ch_fail(ch_fkind k, const char *sig, const char *file, int line, const char *fmt, ...) {
  char msg[240];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(msg, sizeof msg, fmt, ap);
  va_end(ap);
  if (!g_have_failure) {
    fill_failure(&g_failure, k, sig, file, line, msg);
    g_have_failure = 1;
  }
  ch_finalize(FIN_FAIL);
  _exit(86); /* not reached */
}

void ch_report(ch_finding *f) {
  for (int i = 0; i < g_nfindings; i++)
    if (strcmp(g_findings[i].sig, f->sig) == 0) {
      if (!fatal_kind(f->kind)) return;
    }
  if (g_nfindings < CH_MAX_FINDINGS) {
    int dup = 0;
    for (int i = 0; i < g_nfindings; i++)
      if (strcmp(g_findings[i].sig, f->sig) == 0) dup = 1;
    if (!dup) g_findings[g_nfindings++] = *f;
  }
  if (fatal_kind(f->kind)) {
    if (!g_have_failure) {
      g_failure = *f;
      g_have_failure = 1;
    }
    ch_finalize(FIN_FAIL);
    _exit(86);
  }
}

void ch_unsupported(const char *what) {
  char sig[200];
  snprintf(sig, sizeof sig, "UNSUPPORTED[%s]", what);
  ch_fail(F_UNSUPPORTED, sig, NULL, 0, "unsupported operation: %s (see docs/LIMITATIONS.md)", what);
}

void ch_misuse(const char *code, const char *fmt, ...) {
  char sig[200], msg[200];
  snprintf(sig, sizeof sig, "MISUSE[%s]", code);
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(msg, sizeof msg, fmt, ap);
  va_end(ap);
  ch_fail(F_MISUSE, sig, NULL, 0, "%s", msg);
}

static void ch_diverge(const char *fmt, ...) __attribute__((format(printf, 1, 2), noreturn));
static void ch_diverge(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(g_div_reason, sizeof g_div_reason, fmt, ap);
  va_end(ap);
  g_divergence = 1;
  g_div_step = g_nsteps;
  ch_finalize(FIN_FAIL);
  _exit(87);
}

/* ---- objects ---------------------------------------------------------------------------- */
#define OBJ_HASH 1024
#define OBJ_MAX 65536
static ch_obj *g_objs[OBJ_MAX];
static int g_nobjs;
static ch_obj *g_hash[OBJ_HASH];
static struct { const void *addr; char name[40]; } g_pending_names[64];
static int g_npending;

static unsigned hash_addr(const void *p) {
  uintptr_t x = (uintptr_t)p;
  x ^= x >> 17;
  x *= 0x9e3779b97f4a7c15ULL;
  return (unsigned)((x >> 32) % OBJ_HASH);
}

ch_obj *ch_obj_get(const void *addr, ch_okind kind, int create) {
  unsigned h = hash_addr(addr);
  for (ch_obj *o = g_hash[h]; o; o = o->next)
    if (o->addr == addr && o->live) {
      if (o->kind == kind) return o;
      o->live = 0; /* address reuse with a different kind: retire old object */
    }
  if (!create) return NULL;
  if (g_nobjs >= OBJ_MAX) ch_unsupported("too_many_sync_objects");
  ch_obj *o = (ch_obj *)calloc(1, sizeof *o);
  if (!o) ch_fail(F_UNSUPPORTED, "UNSUPPORTED[out_of_memory]", NULL, 0, "out of memory");
  o->addr = addr;
  o->kind = kind;
  o->live = 1;
  o->id = g_nobjs;
  o->last_rel_step = -1;
  snprintf(o->name, sizeof o->name, "%s#%d", ch_okind_names[kind], o->id);
  for (int i = 0; i < g_npending; i++)
    if (g_pending_names[i].addr == addr) {
      snprintf(o->name, sizeof o->name, "%s", g_pending_names[i].name);
      g_pending_names[i].addr = NULL;
    }
  if (kind == OBJ_MUTEX) o->u.mtx.owner = -1;
  o->next = g_hash[h];
  g_hash[h] = o;
  g_objs[g_nobjs++] = o;
  return o;
}

void ch_obj_destroy(const void *addr) {
  unsigned h = hash_addr(addr);
  for (ch_obj *o = g_hash[h]; o; o = o->next)
    if (o->addr == addr && o->live) o->live = 0;
}
ch_obj *ch_obj_by_id(int id) { return (id >= 0 && id < g_nobjs) ? g_objs[id] : NULL; }
int ch_nobjs(void) { return g_nobjs; }

void ch_name_object(const void *addr, const char *name) {
  unsigned h = hash_addr(addr);
  for (ch_obj *o = g_hash[h]; o; o = o->next)
    if (o->addr == addr && o->live) {
      snprintf(o->name, sizeof o->name, "%s", name);
      return;
    }
  if (g_npending < 64) {
    g_pending_names[g_npending].addr = addr;
    snprintf(g_pending_names[g_npending].name, sizeof g_pending_names[0].name, "%s", name);
    g_npending++;
  }
}

/* ---- vector clocks ------------------------------------------------------------------------- */
void ch_vc_join(uint32_t *dst, const uint32_t *src) {
  for (int i = 0; i < g_nthreads; i++)
    if (src[i] > dst[i]) dst[i] = src[i];
}
int ch_cur_step(void) { return (int)g_nsteps - 1; }
ch_step *ch_cur_step_rec(void) { return g_nsteps ? &g_steps[g_nsteps - 1] : NULL; }

void ch_hb_edge(int src) {
  ch_step *s = ch_cur_step_rec();
  if (!s || src < 0) return;
  for (int i = 0; i < 3; i++) {
    if (s->hb[i] == src) return;
    if (s->hb[i] < 0) {
      s->hb[i] = src;
      return;
    }
  }
}
void ch_hb_acquire(ch_thr *t, ch_obj *o) {
  ch_vc_join(t->vc, o->vc);
  ch_hb_edge(o->last_rel_step);
}
void ch_hb_release(ch_thr *t, ch_obj *o) {
  ch_vc_join(o->vc, t->vc);
  t->vc[t->tid]++;
  o->last_rel_step = ch_cur_step();
}

/* ---- enabledness ---------------------------------------------------------------------------- */
static int op_enabled(const ch_thr *t) {
  switch (t->pend_op) {
    case OP_MUTEX_LOCK: {
      const ch_obj *m = t->pend_obj;
      if (m->u.mtx.owner < 0) return 1;
      if (m->u.mtx.owner == t->tid) return m->u.mtx.type != MTX_NORMAL; /* recursive ok, errorcheck -> EDEADLK */
      return 0;
    }
    case OP_COND_WAKE: {
      const ch_obj *m = t->pend_obj2;
      if (m->u.mtx.owner >= 0) return 0;
      return t->signaled || g_spurious_left > 0;
    }
    case OP_SEM_WAIT: return t->pend_obj->u.sem.count > 0;
    case OP_BARRIER_LEAVE: return t->pend_obj->u.bar.gen > t->pend_arg;
    case OP_JOIN: return g_thr[t->pend_arg].state == TS_FINISHED;
    case OP_ONCE: return t->pend_obj->u.once.state != 1;
    default: return 1;
  }
}

int ch_runnable(const ch_thr *t) { return t->state == TS_ACTIVE && op_enabled(t); }

static uint64_t runnable_mask(void) {
  uint64_t m = 0;
  for (int i = 0; i < g_nthreads; i++)
    if (ch_runnable(&g_thr[i])) m |= 1ULL << i;
  return m;
}

/* ---- strategies ------------------------------------------------------------------------------ */
static int lowest(uint64_t mask) { return __builtin_ctzll(mask); }

static int base_pick(uint64_t mask) {
  if (g_last_tid >= 0 && ((mask >> g_last_tid) & 1u)) return g_last_tid;
  return lowest(mask);
}

static int pick_random(uint64_t mask) {
  int n = __builtin_popcountll(mask);
  int k = (int)(rnd64() % (uint64_t)n);
  for (int i = 0; i < CH_MAXT; i++)
    if ((mask >> i) & 1u) {
      if (k == 0) return i;
      k--;
    }
  return lowest(mask);
}

static int pick_pct(uint64_t mask) {
  while (g_next_cp < g_ncp && g_cp[g_next_cp] <= g_nsteps) {
    if (g_cp[g_next_cp] == g_nsteps && g_last_tid >= 0) g_thr[g_last_tid].prio = (uint64_t)(g_next_cp + 1);
    g_next_cp++;
  }
  int best = -1;
  for (int i = 0; i < g_nthreads; i++)
    if ((mask >> i) & 1u) {
      if (best < 0 || g_thr[i].prio > g_thr[best].prio) best = i;
    }
  return best;
}

static int pick_preempt(uint64_t mask) {
  for (uint32_t i = 0; i < g_over.n; i++) {
    const ch_override *o = &g_over.v[i];
    if (o->prev == g_last_tid && (uint32_t)o->cnt == g_last_opcnt && ((mask >> o->next) & 1u)) return o->next;
  }
  return base_pick(mask);
}

static int pick_replay(uint64_t mask) {
  if (g_nsteps >= g_sched.n) {
    if (g_cfg.prefix_ok) return base_pick(mask);
    ch_diverge("schedule exhausted after %u steps but the program is still running", g_sched.n);
  }
  const ch_sched_rec *r = &g_sched.recs[g_nsteps];
  if (r->run != mask)
    ch_diverge("runnable set mismatch at step %u: schedule has 0x%llx, execution has 0x%llx", g_nsteps,
               (unsigned long long)r->run, (unsigned long long)mask);
  const ch_thr *t = &g_thr[r->tid];
  if (t->pend_op != r->op)
    ch_diverge("operation mismatch at step %u: schedule expects %s by t%d, thread is at %s", g_nsteps,
               ch_op_names[r->op], r->tid, ch_op_names[t->pend_op]);
  int oid = t->pend_obj ? t->pend_obj->id : -1;
  if (oid != r->obj)
    ch_diverge("object mismatch at step %u: schedule expects object %d, thread is at %d", g_nsteps, r->obj, oid);
  if (t->opcnt != r->opidx)
    ch_diverge("op index mismatch at step %u: schedule expects #%u of t%d, thread is at #%u", g_nsteps, r->opidx,
               r->tid, t->opcnt);
  return r->tid;
}

static int pick_prefix(uint64_t mask) {
  if (g_nsteps < g_prefix.n) {
    int t = g_prefix.v[g_nsteps];
    if (!((mask >> t) & 1u))
      ch_diverge("prefix choice t%d is not runnable at step %u (runnable 0x%llx)", t, g_nsteps, (unsigned long long)mask);
    return t;
  }
  return base_pick(mask);
}

static void build_deadlock_failure(void) {
  const char *names[CH_MAXT];
  int n = 0;
  char detail[200];
  size_t dl = 0;
  detail[0] = 0;
  for (int i = 0; i < g_nthreads; i++) {
    const ch_thr *t = &g_thr[i];
    if (t->state != TS_ACTIVE) continue;
    const char *nm = t->pend_op == OP_COND_WAKE ? "cond_wait" : ch_op_names[t->pend_op];
    names[n++] = nm;
    if (dl < sizeof detail - 40) {
      const char *on = t->pend_obj ? t->pend_obj->name : "";
      int w = snprintf(detail + dl, sizeof detail - dl, "%st%d:%s(%s)", dl ? " " : "", t->tid, nm, on);
      if (w > 0) dl += (size_t)w;
    }
  }
  /* sort names for a schedule-independent signature */
  for (int i = 1; i < n; i++)
    for (int j = i; j > 0 && strcmp(names[j - 1], names[j]) > 0; j--) {
      const char *tmp = names[j];
      names[j] = names[j - 1];
      names[j - 1] = tmp;
    }
  char sig[200];
  size_t sl = (size_t)snprintf(sig, sizeof sig, "DEADLOCK[");
  for (int i = 0; i < n && sl < sizeof sig - 24; i++) {
    int w = snprintf(sig + sl, sizeof sig - sl, "%s%s", i ? "," : "", names[i]);
    if (w > 0) sl += (size_t)w;
  }
  snprintf(sig + sl, sizeof sig - sl, "]");
  ch_fail(F_DEADLOCK, sig, NULL, 0, "no runnable thread; blocked: %s", detail);
}

/* choose the next thread and record the step; may not return */
static int decide(void) {
  uint64_t mask = runnable_mask();
  if (mask == 0) build_deadlock_failure();
  if (g_nsteps >= g_cfg.max_steps)
    ch_fail(F_STEP_LIMIT, "STEP_LIMIT", NULL, 0, "execution exceeded %u scheduling steps (possible livelock)", g_cfg.max_steps);
  int pick;
  switch (g_cfg.strategy) {
    case ST_RANDOM: pick = pick_random(mask); break;
    case ST_PCT: pick = pick_pct(mask); break;
    case ST_REPLAY: pick = pick_replay(mask); break;
    case ST_PREEMPT: pick = pick_preempt(mask); break;
    case ST_PREFIX: pick = pick_prefix(mask); break;
    default: pick = base_pick(mask); break;
  }
  ch_thr *t = &g_thr[pick];
  if (t->pend_op == OP_COND_WAKE && !t->signaled) {
    if (g_spurious_left == 0) ch_fail(F_MISUSE, "MISUSE[internal_spurious]", NULL, 0, "internal: spurious wake without budget");
  }
  ch_step *s = &g_steps[g_nsteps];
  memset(s, 0, sizeof *s);
  s->step = g_nsteps;
  s->tid = (int16_t)pick;
  s->opidx = t->opcnt;
  s->op = (uint8_t)t->pend_op;
  s->obj = t->pend_obj ? t->pend_obj->id : -1;
  s->run = mask;
  s->hb[0] = s->hb[1] = s->hb[2] = -1;
  if (t->pend_op == OP_READ || t->pend_op == OP_WRITE) {
    s->name = t->pend_name;
  } else if (t->pend_obj) {
    s->name = t->pend_obj->name;
  }
  s->file = t->pend_file;
  s->line = t->pend_line;
  t->opcnt++;
  g_nsteps++;
  g_last_tid = pick;
  g_last_opcnt = t->opcnt;
  atomic_store_explicit(&g_progress, g_nsteps, memory_order_relaxed);
  return pick;
}

void ch_yield_to_scheduler(ch_thr *self) {
  int next = decide();
  if (next == self->tid) return;
  unpark(&g_thr[next]);
  park(self);
}

void ch_sched_point(ch_thr *t, int op, ch_obj *o, ch_obj *o2, int arg, const void *addr, unsigned size,
                    const char *name, const char *file, int line) {
  t->pend_op = op;
  t->pend_obj = o;
  t->pend_obj2 = o2;
  t->pend_arg = arg;
  t->pend_addr = addr;
  t->pend_size = size;
  t->pend_name = name;
  t->pend_file = file;
  t->pend_line = line;
  ch_yield_to_scheduler(t);
}

/* ---- threads -------------------------------------------------------------------------------------- */
ch_thr *ch_thread_new(ch_thr *parent, void *(*start)(void *), void *arg) {
  if (g_nthreads >= CH_MAXT) ch_unsupported("thread_limit_64");
  ch_thr *t = &g_thr[g_nthreads];
  memset(t, 0, sizeof *t);
  t->tid = g_nthreads++;
  t->state = TS_ACTIVE;
  t->start = start;
  t->arg = arg;
  t->pend_op = OP_START;
  t->prio = (1ULL << 32) | (rnd64() & 0xffffffffULL);
  t->create_step = ch_cur_step();
  t->exit_step = -1;
  if (parent) {
    memcpy(t->vc, parent->vc, sizeof t->vc);
    parent->vc[parent->tid]++;
  }
  t->vc[t->tid] = 1;
  return t;
}

void ch_thread_finish(ch_thr *t, void *rv) {
  t->retval = rv;
  ch_sched_point(t, OP_EXIT, NULL, NULL, 0, NULL, 0, NULL, NULL, 0);
  t->state = TS_FINISHED;
  t->exit_step = ch_cur_step();
  t->vc[t->tid]++;
  int next = decide(); /* t is finished and therefore excluded */
  unpark(&g_thr[next]); /* last touch of shared state */
}

ch_thr *ch_self(void) {
  if (!g_active) return NULL;
  if (!tls_self) {
    if (g_exiting) return NULL;
    ch_unsupported("call_from_thread_not_created_via_pthread_create");
  }
  if (g_exiting) return NULL;
  if (tls_self->state == TS_FINISHED) ch_unsupported("synchronization_after_thread_exit(cleanup_handler_or_tls_destructor)");
  return tls_self;
}

/* ---- watchdog & signals ------------------------------------------------------------------------------ */
static void on_signal(int sig) {
  if (!g_have_failure) {
    char sig_s[64];
    const char *nm = sig == SIGSEGV ? "SIGSEGV" : sig == SIGBUS ? "SIGBUS" : sig == SIGFPE ? "SIGFPE" : sig == SIGILL ? "SIGILL" : "SIGABRT";
    snprintf(sig_s, sizeof sig_s, "CRASH[%s]", nm);
    fill_failure(&g_failure, F_CRASH, sig_s, NULL, 0, "fatal signal");
    g_have_failure = 1;
  }
  ch_finalize(FIN_SIGNAL);
  _exit(86);
}

static uint64_t now_ms(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static void *watchdog_main(void *unused) {
  (void)unused;
  uint32_t last = atomic_load(&g_progress);
  uint64_t last_t = now_ms();
  struct timespec nap = {0, 50 * 1000 * 1000};
  for (;;) {
    if (real_nanosleep_fn) real_nanosleep_fn(&nap, NULL);
    uint32_t cur = atomic_load(&g_progress);
    uint64_t now = now_ms();
    if (cur != last) {
      last = cur;
      last_t = now;
    } else if (now - last_t > (uint64_t)g_cfg.watchdog_ms) {
      if (!g_have_failure) {
        char msg[200];
        snprintf(msg, sizeof msg, "no scheduling point reached for %d ms after %u steps (spin loop or blocking call outside the model?)", g_cfg.watchdog_ms, cur);
        fill_failure(&g_failure, F_HANG, "HANG", NULL, 0, msg);
        g_failure.tid = -1;
        g_have_failure = 1;
      }
      ch_finalize(FIN_WATCHDOG);
      return NULL;
    }
  }
}

/* ---- initialisation ----------------------------------------------------------------------------------------- */
static int env_int(const char *name, int def, int lo, int hi) {
  const char *v = getenv(name);
  if (!v || !*v) return def;
  char *end = NULL;
  long x = strtol(v, &end, 10);
  if (*end != '\0' || x < lo || x > hi) {
    fprintf(stderr, "chronos: invalid %s=%s (expected %d..%d)\n", name, v, lo, hi);
    _exit(88);
  }
  return (int)x;
}

static void config_error(const char *what) {
  fprintf(stderr, "chronos: %s\n", what);
  _exit(88);
}

static void parse_config(void) {
  memset(&g_cfg, 0, sizeof g_cfg);
  g_cfg.disable = env_int("CHRONOS_DISABLE", 0, 0, 1);
  const char *s = getenv("CHRONOS_STRATEGY");
  if (!s || !strcmp(s, "random")) g_cfg.strategy = ST_RANDOM;
  else if (!strcmp(s, "pct")) g_cfg.strategy = ST_PCT;
  else if (!strcmp(s, "replay")) g_cfg.strategy = ST_REPLAY;
  else if (!strcmp(s, "preempt")) g_cfg.strategy = ST_PREEMPT;
  else if (!strcmp(s, "prefix")) g_cfg.strategy = ST_PREFIX;
  else if (!strcmp(s, "base")) g_cfg.strategy = ST_BASE;
  else config_error("CHRONOS_STRATEGY must be random|pct|replay|preempt|prefix|base");
  const char *sd = getenv("CHRONOS_SEED");
  g_cfg.seed = 1;
  if (sd && *sd) {
    char *end = NULL;
    g_cfg.seed = strtoull(sd, &end, 10);
    if (*end) config_error("invalid CHRONOS_SEED");
  }
  g_cfg.pct_depth = env_int("CHRONOS_PCT_DEPTH", 3, 1, 64);
  g_cfg.pct_steps = env_int("CHRONOS_PCT_STEPS", 200, 2, 2000000);
  g_cfg.max_steps = (uint32_t)env_int("CHRONOS_MAX_STEPS", 200000, 10, 2000000);
  g_cfg.spurious_budget = env_int("CHRONOS_SPURIOUS", 0, 0, 1000000);
  g_cfg.watchdog_ms = env_int("CHRONOS_WATCHDOG_MS", 20000, 0, 3600000);
  g_cfg.prefix_ok = env_int("CHRONOS_PREFIX_OK", 0, 0, 1);
  g_quiet = env_int("CHRONOS_QUIET", 0, 0, 1);
  g_cfg.sched_in = getenv("CHRONOS_SCHEDULE_IN");
  g_cfg.preempt_in = getenv("CHRONOS_PREEMPT_IN");
  g_cfg.prefix_in = getenv("CHRONOS_PREFIX_IN");
  g_cfg.trace_out = getenv("CHRONOS_TRACE_OUT");
  g_cfg.sched_out = getenv("CHRONOS_SCHEDULE_OUT");
  g_cfg.result_out = getenv("CHRONOS_RESULT_OUT");
  g_cfg.fatal_mask = 0;
  const char *fa = getenv("CHRONOS_FATAL");
  if (fa && *fa) {
    char buf[128];
    snprintf(buf, sizeof buf, "%s", fa);
    for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
      if (!strcmp(tok, "race")) g_cfg.fatal_mask |= 1u << F_RACE;
      else if (!strcmp(tok, "lockorder")) g_cfg.fatal_mask |= 1u << F_LOCKORDER;
      else config_error("CHRONOS_FATAL accepts only: race,lockorder");
    }
  }
}

static void pct_init(void) {
  int k = g_cfg.pct_depth - 1;
  g_ncp = 0;
  int guard = 0;
  while (g_ncp < k && guard++ < 10000) {
    uint32_t cp = 1 + (uint32_t)(rnd64() % (uint64_t)g_cfg.pct_steps);
    int dup = 0;
    for (int i = 0; i < g_ncp; i++)
      if (g_cp[i] == cp) dup = 1;
    if (!dup && g_ncp < 64) g_cp[g_ncp++] = cp;
    if (g_ncp >= 64) break;
  }
  for (int i = 1; i < g_ncp; i++)
    for (int j = i; j > 0 && g_cp[j - 1] > g_cp[j]; j--) {
      uint32_t tmp = g_cp[j];
      g_cp[j] = g_cp[j - 1];
      g_cp[j - 1] = tmp;
    }
}

__attribute__((constructor(101))) static void chronos_init(void) {
  ch_resolve_real();
  parse_config();
  if (g_cfg.disable) return;
  size_t bytes = (size_t)g_cfg.max_steps * sizeof(ch_step);
  g_steps = (ch_step *)mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
  if (g_steps == MAP_FAILED) config_error("cannot allocate step buffer");
  uint64_t sm = g_cfg.seed;
  g_rng = splitmix(&sm) | 1u;
  g_spurious_left = (uint32_t)g_cfg.spurious_budget;
  const char *pn = getenv("CHRONOS_PROGRAM");
  if (pn && *pn) snprintf(g_prog, sizeof g_prog, "%s", ch_base(pn));
  else {
    char buf[256];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n > 0) {
      buf[n] = 0;
      snprintf(g_prog, sizeof g_prog, "%s", ch_base(buf));
    } else
      snprintf(g_prog, sizeof g_prog, "unknown");
  }
  char err[200];
  if (g_cfg.strategy == ST_REPLAY) {
    if (!g_cfg.sched_in) config_error("CHRONOS_STRATEGY=replay requires CHRONOS_SCHEDULE_IN");
    if (ch_sched_load(g_cfg.sched_in, &g_sched, err, sizeof err) != 0) {
      fprintf(stderr, "chronos: cannot load schedule: %s\n", err);
      _exit(88);
    }
    g_replay_on = 1;
    g_replay_n = g_sched.n;
  }
  if (g_cfg.strategy == ST_PREEMPT) {
    if (!g_cfg.preempt_in) config_error("CHRONOS_STRATEGY=preempt requires CHRONOS_PREEMPT_IN");
    if (ch_over_load(g_cfg.preempt_in, &g_over, err, sizeof err) != 0) {
      fprintf(stderr, "chronos: cannot load overrides: %s\n", err);
      _exit(88);
    }
  }
  if (g_cfg.strategy == ST_PREFIX) {
    if (!g_cfg.prefix_in) config_error("CHRONOS_STRATEGY=prefix requires CHRONOS_PREFIX_IN");
    if (ch_prefix_load(g_cfg.prefix_in, &g_prefix, err, sizeof err) != 0) {
      fprintf(stderr, "chronos: cannot load prefix: %s\n", err);
      _exit(88);
    }
  }
  if (g_cfg.strategy == ST_PCT) pct_init();
  /* main thread */
  ch_thr *m = &g_thr[0];
  memset(m, 0, sizeof *m);
  m->tid = 0;
  m->state = TS_ACTIVE;
  m->vc[0] = 1;
  m->prio = (1ULL << 32) | (rnd64() & 0xffffffffULL);
  m->create_step = -1;
  m->exit_step = -1;
  m->pend_op = OP_START;
  g_nthreads = 1;
  tls_self = m;
  g_last_tid = 0;
  g_last_opcnt = 0;
  struct sigaction sa;
  memset(&sa, 0, sizeof sa);
  sa.sa_handler = on_signal;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = SA_RESETHAND | SA_NODEFER;
  sigaction(SIGSEGV, &sa, NULL);
  sigaction(SIGBUS, &sa, NULL);
  sigaction(SIGFPE, &sa, NULL);
  sigaction(SIGILL, &sa, NULL);
  sigaction(SIGABRT, &sa, NULL);
  if (g_cfg.watchdog_ms > 0 && real_pthread_create) {
    pthread_t wd;
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
    real_pthread_create(&wd, &at, watchdog_main, NULL);
    pthread_attr_destroy(&at);
  }
  __atomic_store_n(&g_active, 1, __ATOMIC_RELEASE);
}

__attribute__((destructor)) static void chronos_fini(void) {
  if (!g_active) return;
  ch_finalize(FIN_NORMAL);
}

/* the thread trampoline lives here so it can use park() */
typedef struct { ch_thr *t; } tramp_arg;

void *ch_trampoline(void *p) {
  ch_thr *t = (ch_thr *)p;
  tls_self = t;
  park(t); /* wait until decide() picks our OP_START */
  ch_hb_edge(t->create_step);
  void *rv;
  if (setjmp(t->exit_jmp) == 0)
    rv = t->start(t->arg);
  else
    rv = t->retval; /* left through pthread_exit() */
  ch_thread_finish(t, rv);
  return rv;
}

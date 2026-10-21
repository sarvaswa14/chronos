/* rt_sync.c - interposed synchronization primitives (pthread mutex, condvar,
 * semaphore, barrier, once, create/join) plus the annotation API.
 *
 * Every wrapper has the same shape:
 *     1. no runtime / foreign context  -> forward to the real libc function
 *     2. declare a pending operation   -> ch_sched_point()  (may switch thread)
 *     3. when chosen, apply the operation's effect to the *model*
 *        (never to the OS object) and update vector clocks / HB edges.
 */
#include "rt.h"

#include <dlfcn.h>
#include <pthread.h>
#include <sched.h>
#include <semaphore.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define REAL_CALL(name, rettype, params, args)                                     \
  ({                                                                               \
    typedef rettype (*fn_t) params;                                                \
    static fn_t f;                                                                 \
    if (!f) f = (fn_t)dlsym(RTLD_NEXT, #name);                                     \
    f args;                                                                        \
  })

#define SP(t, op, o, o2, arg) ch_sched_point((t), (op), (o), (o2), (arg), NULL, 0, NULL, NULL, 0)

static void set_aux(int v) {
  ch_step *s = ch_cur_step_rec();
  if (s) s->aux = v;
}

/* ===== threads ============================================================ */
int pthread_create(pthread_t *th, const pthread_attr_t *attr, void *(*start)(void *), void *arg) {
  ch_thr *p = ch_self();
  if (!p) {
    ch_resolve_real();
    return real_pthread_create(th, attr, start, arg);
  }
  SP(p, OP_CREATE, NULL, NULL, 0);
  ch_thr *c = ch_thread_new(p, start, arg);
  set_aux(c->tid);
  int ds = PTHREAD_CREATE_JOINABLE;
  if (attr) pthread_attr_getdetachstate(attr, &ds);
  c->detached = (ds == PTHREAD_CREATE_DETACHED);
  pthread_t real;
  int rc = real_pthread_create(&real, attr, ch_trampoline, c);
  if (rc != 0) {
    c->state = TS_FINISHED; /* never started: must not be schedulable */
    return rc;
  }
  c->real = real;
  c->have_real = 1;
  *th = real;
  return 0;
}

static ch_thr *find_thread(pthread_t th) {
  for (int i = 0; i < g_nthreads; i++)
    if (g_thr[i].have_real && pthread_equal(g_thr[i].real, th)) return &g_thr[i];
  return NULL;
}

int pthread_join(pthread_t th, void **ret) {
  ch_thr *t = ch_self();
  if (!t) {
    ch_resolve_real();
    return real_pthread_join(th, ret);
  }
  ch_thr *tg = find_thread(th);
  if (!tg) ch_misuse("join_unknown_thread", "pthread_join on a thread unknown to Chronos");
  if (tg == t) ch_misuse("join_self", "thread joins itself");
  if (tg->detached) ch_misuse("join_detached", "pthread_join on a detached thread");
  if (tg->joined) ch_misuse("join_twice", "thread joined twice");
  SP(t, OP_JOIN, NULL, NULL, tg->tid);
  tg->joined = 1;
  ch_vc_join(t->vc, tg->vc);
  ch_hb_edge(tg->exit_step);
  set_aux(tg->tid);
  return real_pthread_join(tg->real, ret);
}

int pthread_detach(pthread_t th) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_detach, int, (pthread_t), (th));
  ch_thr *tg = find_thread(th);
  if (!tg) ch_misuse("detach_unknown_thread", "pthread_detach on a thread unknown to Chronos");
  if (tg->joined || tg->detached) ch_misuse("detach_twice_or_after_join", "thread already joined or detached");
  tg->detached = 1;
  return REAL_CALL(pthread_detach, int, (pthread_t), (th));
}

void pthread_exit(void *rv) {
  ch_thr *t = ch_self();
  if (!t) {
    ch_resolve_real();
    real_pthread_exit(rv);
    abort(); /* not reached */
  }
  if (t->tid == 0) ch_unsupported("pthread_exit_in_main_thread");
  /* Leave through the trampoline: real pthread_exit() would unwind the stack and call
   * synchronization primitives (libgcc) after this thread gave up the baton. Consequence,
   * documented in LIMITATIONS.md: pthread_cleanup handlers are not run. */
  t->retval = rv;
  longjmp(t->exit_jmp, 1);
}

/* ===== mutex =============================================================== */
int pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *attr) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_mutex_init, int, (pthread_mutex_t *, const pthread_mutexattr_t *), (m, attr));
  ch_obj *o = ch_obj_get(m, OBJ_MUTEX, 1);
  memset(&o->u, 0, sizeof o->u);
  memset(o->vc, 0, sizeof o->vc);
  o->last_rel_step = -1;
  o->u.mtx.owner = -1;
  if (attr && real_pthread_mutexattr_gettype) {
    int ty = 0;
    real_pthread_mutexattr_gettype(attr, &ty);
    o->u.mtx.type = ty == PTHREAD_MUTEX_RECURSIVE ? MTX_RECURSIVE : ty == PTHREAD_MUTEX_ERRORCHECK ? MTX_ERRORCHECK : MTX_NORMAL;
  }
  return 0;
}

int pthread_mutex_destroy(pthread_mutex_t *m) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_mutex_destroy, int, (pthread_mutex_t *), (m));
  ch_obj *o = ch_obj_get(m, OBJ_MUTEX, 0);
  if (o && o->u.mtx.owner >= 0) ch_misuse("destroy_locked_mutex", "pthread_mutex_destroy on a locked mutex (%s)", o->name);
  ch_obj_destroy(m);
  return 0;
}

int pthread_mutex_lock(pthread_mutex_t *m) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_mutex_lock, int, (pthread_mutex_t *), (m));
  ch_obj *o = ch_obj_get(m, OBJ_MUTEX, 1);
  SP(t, OP_MUTEX_LOCK, o, NULL, 0);
  if (o->u.mtx.owner == t->tid) {
    if (o->u.mtx.type == MTX_RECURSIVE) {
      o->u.mtx.count++;
      return 0;
    }
    return EDEADLK; /* error-checking mutex; normal mutex is never enabled here */
  }
  o->u.mtx.owner = t->tid;
  o->u.mtx.count = 1;
  ch_hb_acquire(t, o);
  ch_lo_acquire(t, o);
  return 0;
}

int pthread_mutex_trylock(pthread_mutex_t *m) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_mutex_trylock, int, (pthread_mutex_t *), (m));
  ch_obj *o = ch_obj_get(m, OBJ_MUTEX, 1);
  SP(t, OP_MUTEX_TRYLOCK, o, NULL, 0);
  if (o->u.mtx.owner < 0) {
    o->u.mtx.owner = t->tid;
    o->u.mtx.count = 1;
    ch_hb_acquire(t, o);
    ch_lo_push(t, o);
    set_aux(0);
    return 0;
  }
  if (o->u.mtx.owner == t->tid && o->u.mtx.type == MTX_RECURSIVE) {
    o->u.mtx.count++;
    set_aux(0);
    return 0;
  }
  set_aux(EBUSY);
  return EBUSY;
}

int pthread_mutex_unlock(pthread_mutex_t *m) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_mutex_unlock, int, (pthread_mutex_t *), (m));
  ch_obj *o = ch_obj_get(m, OBJ_MUTEX, 1);
  SP(t, OP_MUTEX_UNLOCK, o, NULL, 0);
  if (o->u.mtx.owner != t->tid) {
    if (o->u.mtx.type == MTX_ERRORCHECK) return EPERM;
    ch_misuse("unlock_not_owner", "t%d unlocks mutex %s that it does not hold (owner: %d)", t->tid, o->name, o->u.mtx.owner);
  }
  if (o->u.mtx.type == MTX_RECURSIVE && o->u.mtx.count > 1) {
    o->u.mtx.count--;
    return 0;
  }
  o->u.mtx.owner = -1;
  o->u.mtx.count = 0;
  ch_hb_release(t, o);
  ch_lo_release(t, o);
  return 0;
}

/* ===== condition variables ================================================== */
int pthread_cond_init(pthread_cond_t *cv, const pthread_condattr_t *attr) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_cond_init, int, (pthread_cond_t *, const pthread_condattr_t *), (cv, attr));
  ch_obj *o = ch_obj_get(cv, OBJ_COND, 1);
  memset(&o->u, 0, sizeof o->u);
  return 0;
}

int pthread_cond_destroy(pthread_cond_t *cv) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_cond_destroy, int, (pthread_cond_t *), (cv));
  ch_obj *o = ch_obj_get(cv, OBJ_COND, 0);
  if (o && o->u.cond.nw > 0) ch_misuse("destroy_cond_with_waiters", "pthread_cond_destroy while threads wait on %s", o->name);
  ch_obj_destroy(cv);
  return 0;
}

int pthread_cond_wait(pthread_cond_t *cv, pthread_mutex_t *m) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_cond_wait, int, (pthread_cond_t *, pthread_mutex_t *), (cv, m));
  ch_obj *c = ch_obj_get(cv, OBJ_COND, 1);
  ch_obj *mo = ch_obj_get(m, OBJ_MUTEX, 1);
  SP(t, OP_COND_WAIT, c, mo, 0);
  set_aux(mo->id); /* trace: which mutex this wait releases */
  if (mo->u.mtx.owner != t->tid) ch_misuse("cond_wait_mutex_not_held", "t%d calls pthread_cond_wait without holding %s", t->tid, mo->name);
  if (mo->u.mtx.type == MTX_RECURSIVE && mo->u.mtx.count > 1) ch_misuse("cond_wait_recursive_mutex", "cond_wait with a recursively held mutex");
  /* atomically release the mutex and enqueue */
  mo->u.mtx.owner = -1;
  mo->u.mtx.count = 0;
  ch_hb_release(t, mo);
  ch_lo_release(t, mo);
  c->u.cond.waiters[c->u.cond.nw++] = t->tid;
  t->signaled = 0;
  t->wake_src = -1;
  t->pend_op = OP_COND_WAKE;
  t->pend_obj = c;
  t->pend_obj2 = mo;
  t->pend_arg = 0;
  t->pend_name = NULL;
  t->pend_file = NULL;
  t->pend_line = 0;
  ch_yield_to_scheduler(t); /* returns once chosen for OP_COND_WAKE */
  if (!t->signaled) {       /* spurious wake-up (budgeted) */
    for (int i = 0; i < c->u.cond.nw; i++)
      if (c->u.cond.waiters[i] == t->tid) {
        for (int k = i; k + 1 < c->u.cond.nw; k++) c->u.cond.waiters[k] = c->u.cond.waiters[k + 1];
        c->u.cond.nw--;
        break;
      }
    g_spurious_left--;
    set_aux(-1);
  } else {
    ch_vc_join(t->vc, t->wake_vc);
    ch_hb_edge(t->wake_src);
    set_aux(t->wake_src);
  }
  mo->u.mtx.owner = t->tid;
  mo->u.mtx.count = 1;
  ch_hb_acquire(t, mo);
  ch_lo_acquire(t, mo);
  return 0;
}

static void wake_one(ch_thr *t, ch_obj *c) {
  int w = c->u.cond.waiters[0];
  for (int k = 0; k + 1 < c->u.cond.nw; k++) c->u.cond.waiters[k] = c->u.cond.waiters[k + 1];
  c->u.cond.nw--;
  ch_thr *wt = &g_thr[w];
  wt->signaled = 1;
  memcpy(wt->wake_vc, t->vc, sizeof wt->wake_vc);
  wt->wake_src = ch_cur_step();
}

int pthread_cond_signal(pthread_cond_t *cv) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_cond_signal, int, (pthread_cond_t *), (cv));
  ch_obj *c = ch_obj_get(cv, OBJ_COND, 1);
  SP(t, OP_COND_SIGNAL, c, NULL, 0);
  if (c->u.cond.nw > 0) {
    set_aux(c->u.cond.waiters[0]);
    wake_one(t, c);
    t->vc[t->tid]++;
  } else
    set_aux(-1); /* signal with no waiter: lost */
  return 0;
}

int pthread_cond_broadcast(pthread_cond_t *cv) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_cond_broadcast, int, (pthread_cond_t *), (cv));
  ch_obj *c = ch_obj_get(cv, OBJ_COND, 1);
  SP(t, OP_COND_BROADCAST, c, NULL, 0);
  int n = c->u.cond.nw;
  while (c->u.cond.nw > 0) wake_one(t, c);
  if (n) t->vc[t->tid]++;
  set_aux(n);
  return 0;
}

/* ===== semaphores ============================================================== */
int sem_init(sem_t *s, int pshared, unsigned int value) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sem_init, int, (sem_t *, int, unsigned int), (s, pshared, value));
  if (value > 2147483647u) {
    errno = EINVAL;
    return -1;
  }
  ch_obj *o = ch_obj_get(s, OBJ_SEM, 1);
  memset(&o->u, 0, sizeof o->u);
  memset(o->vc, 0, sizeof o->vc);
  o->last_rel_step = -1;
  o->u.sem.count = (int)value;
  return 0;
}

int sem_destroy(sem_t *s) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sem_destroy, int, (sem_t *), (s));
  ch_obj_destroy(s);
  return 0;
}

static ch_obj *sem_obj(sem_t *s) {
  ch_obj *o = ch_obj_get(s, OBJ_SEM, 0);
  if (!o) ch_misuse("sem_uninitialized", "semaphore used before sem_init");
  return o;
}

int sem_wait(sem_t *s) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sem_wait, int, (sem_t *), (s));
  ch_obj *o = sem_obj(s);
  SP(t, OP_SEM_WAIT, o, NULL, 0);
  o->u.sem.count--;
  ch_hb_acquire(t, o);
  return 0;
}

int sem_trywait(sem_t *s) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sem_trywait, int, (sem_t *), (s));
  ch_obj *o = sem_obj(s);
  SP(t, OP_SEM_TRYWAIT, o, NULL, 0);
  if (o->u.sem.count > 0) {
    o->u.sem.count--;
    ch_hb_acquire(t, o);
    set_aux(0);
    return 0;
  }
  set_aux(EAGAIN);
  errno = EAGAIN;
  return -1;
}

int sem_post(sem_t *s) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sem_post, int, (sem_t *), (s));
  ch_obj *o = sem_obj(s);
  SP(t, OP_SEM_POST, o, NULL, 0);
  o->u.sem.count++;
  ch_hb_release(t, o);
  return 0;
}

int sem_getvalue(sem_t *s, int *sval) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sem_getvalue, int, (sem_t *, int *), (s, sval));
  *sval = sem_obj(s)->u.sem.count; /* not a scheduling point (documented) */
  return 0;
}

/* ===== barriers ================================================================== */
int pthread_barrier_init(pthread_barrier_t *b, const pthread_barrierattr_t *attr, unsigned count) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_barrier_init, int, (pthread_barrier_t *, const pthread_barrierattr_t *, unsigned), (b, attr, count));
  if (count == 0) return EINVAL;
  ch_obj *o = ch_obj_get(b, OBJ_BARRIER, 1);
  memset(&o->u, 0, sizeof o->u);
  memset(o->vc, 0, sizeof o->vc);
  o->last_rel_step = -1;
  o->u.bar.needed = (int)count;
  return 0;
}

int pthread_barrier_destroy(pthread_barrier_t *b) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_barrier_destroy, int, (pthread_barrier_t *), (b));
  ch_obj_destroy(b);
  return 0;
}

int pthread_barrier_wait(pthread_barrier_t *b) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_barrier_wait, int, (pthread_barrier_t *), (b));
  ch_obj *o = ch_obj_get(b, OBJ_BARRIER, 0);
  if (!o) ch_misuse("barrier_uninitialized", "barrier used before pthread_barrier_init");
  SP(t, OP_BARRIER_WAIT, o, NULL, 0);
  o->u.bar.arrived++;
  ch_hb_release(t, o);
  if (o->u.bar.arrived == o->u.bar.needed) {
    o->u.bar.arrived = 0;
    o->u.bar.gen++;
    ch_vc_join(t->vc, o->vc);
    set_aux(1);
    return PTHREAD_BARRIER_SERIAL_THREAD;
  }
  t->pend_op = OP_BARRIER_LEAVE;
  t->pend_obj = o;
  t->pend_obj2 = NULL;
  t->pend_arg = o->u.bar.gen;
  t->pend_name = NULL;
  t->pend_file = NULL;
  t->pend_line = 0;
  ch_yield_to_scheduler(t);
  ch_hb_acquire(t, o);
  return 0;
}

/* ===== once ======================================================================= */
int pthread_once(pthread_once_t *ctl, void (*fn)(void)) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(pthread_once, int, (pthread_once_t *, void (*)(void)), (ctl, fn));
  ch_obj *o = ch_obj_get(ctl, OBJ_ONCE, 1);
  SP(t, OP_ONCE, o, NULL, 0);
  /* glibc encodes "done" as value 2; honour initialisation done before the
   * runtime was loaded (e.g. by libstdc++ constructors). */
  if (o->u.once.state == 2 || ((*(volatile int *)ctl) & 2)) {
    o->u.once.state = 2;
    ch_hb_acquire(t, o);
    return 0;
  }
  o->u.once.state = 1;
  o->u.once.owner = t->tid;
  fn();
  o->u.once.state = 2;
  *(volatile int *)ctl = 2;
  ch_hb_release(t, o);
  return 0;
}

/* ===== time-based calls: virtual time =============================================== */
int sched_yield(void) {
  ch_thr *t = ch_self();
  if (!t) return (int)syscall(SYS_sched_yield);
  SP(t, OP_YIELD, NULL, NULL, 0);
  return 0;
}
unsigned int sleep(unsigned int s) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(sleep, unsigned int, (unsigned int), (s));
  SP(t, OP_YIELD, NULL, NULL, 0);
  return 0;
}
int usleep(useconds_t us) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(usleep, int, (useconds_t), (us));
  SP(t, OP_YIELD, NULL, NULL, 0);
  return 0;
}
int nanosleep(const struct timespec *req, struct timespec *rem) {
  ch_thr *t = ch_self();
  if (!t) return REAL_CALL(nanosleep, int, (const struct timespec *, struct timespec *), (req, rem));
  SP(t, OP_YIELD, NULL, NULL, 0);
  if (rem) {
    rem->tv_sec = 0;
    rem->tv_nsec = 0;
  }
  return 0;
}

/* ===== unsupported primitives: fail loudly instead of running unsoundly ================= */
#define UNSUP(ret, name, params, args)                                                          \
  ret name params {                                                                             \
    if (!g_active) return REAL_CALL(name, ret, params, args);                                   \
    ch_unsupported(#name);                                                                      \
  }
UNSUP(int, pthread_rwlock_rdlock, (pthread_rwlock_t * l), (l))
UNSUP(int, pthread_rwlock_wrlock, (pthread_rwlock_t * l), (l))
UNSUP(int, pthread_rwlock_tryrdlock, (pthread_rwlock_t * l), (l))
UNSUP(int, pthread_rwlock_trywrlock, (pthread_rwlock_t * l), (l))
UNSUP(int, pthread_rwlock_unlock, (pthread_rwlock_t * l), (l))
UNSUP(int, pthread_spin_lock, (pthread_spinlock_t * l), (l))
UNSUP(int, pthread_spin_trylock, (pthread_spinlock_t * l), (l))
UNSUP(int, pthread_spin_unlock, (pthread_spinlock_t * l), (l))
UNSUP(int, pthread_mutex_timedlock, (pthread_mutex_t * m, const struct timespec *ts), (m, ts))
UNSUP(int, pthread_mutex_clocklock, (pthread_mutex_t * m, clockid_t c, const struct timespec *ts), (m, c, ts))
UNSUP(int, pthread_cond_timedwait, (pthread_cond_t * c, pthread_mutex_t *m, const struct timespec *ts), (c, m, ts))
UNSUP(int, pthread_cond_clockwait, (pthread_cond_t * c, pthread_mutex_t *m, clockid_t k, const struct timespec *ts), (c, m, k, ts))
UNSUP(int, sem_timedwait, (sem_t * s, const struct timespec *ts), (s, ts))
UNSUP(int, sem_clockwait, (sem_t * s, clockid_t c, const struct timespec *ts), (s, c, ts))
UNSUP(int, pthread_cancel, (pthread_t th), (th))
UNSUP(int, pthread_timedjoin_np, (pthread_t th, void **r, const struct timespec *ts), (th, r, ts))
UNSUP(int, pthread_tryjoin_np, (pthread_t th, void **r), (th, r))

/* ===== annotation API (chronos.h) =================================================== */
void chronos_access(const void *addr, unsigned size, int is_write, const char *name, const char *file, int line) {
  ch_thr *t = ch_self();
  if (!t) return;
  ch_sched_point(t, is_write ? OP_WRITE : OP_READ, NULL, NULL, 0, addr, size, name, file, line);
  ch_race_access(t, addr, size, is_write, name, file, line);
}

void chronos_atomic_point(const void *addr, int kind, const char *name, const char *file, int line) {
  ch_thr *t = ch_self();
  if (!t) return;
  ch_obj *o = ch_obj_get(addr, OBJ_ATOMIC, 1);
  if (name && strncmp(o->name, "atomic#", 7) == 0) snprintf(o->name, sizeof o->name, "%s", name);
  int op = kind == 0 ? OP_ATOMIC_LOAD : kind == 1 ? OP_ATOMIC_STORE : OP_ATOMIC_RMW;
  ch_sched_point(t, op, o, NULL, 0, addr, 0, name, file, line);
  if (op != OP_ATOMIC_STORE) ch_hb_acquire(t, o);
  if (op != OP_ATOMIC_LOAD) ch_hb_release(t, o);
}

void chronos_assert_fail(const char *expr, const char *msg, const char *file, int line) {
  char sig[200];
  snprintf(sig, sizeof sig, "ASSERT@%s:%d", ch_base(file), line);
  if (!g_active) {
    fprintf(stderr, "ASSERTION FAILED %s:%d: %s (%s)\n", file, line, expr, msg);
    abort();
  }
  ch_fail(F_ASSERT, sig, file, line, "assertion '%s' failed: %s", expr, msg);
}

void chronos_name(const void *obj, const char *name) {
  if (g_active && name) ch_name_object(obj, name);
}

void chronos_yield(void) {
  ch_thr *t = ch_self();
  if (!t) return;
  SP(t, OP_YIELD, NULL, NULL, 0);
}

/* data-race detector truth table; mode in argv[1].
 * Racy modes: ww wr rrw.  Race-free modes: mutex join create cond sem barrier disjoint atomic */
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include <string.h>
#include "chronos.h"
static int x, y, ready;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static sem_t sm;
static pthread_barrier_t bar;
static const char *mode;
static void *writer1(void *a) { (void)a; CHRONOS_WRITE(x, 1); return NULL; }
static void *writer2(void *a) { (void)a; CHRONOS_WRITE(x, 2); return NULL; }
static void *reader(void *a) { (void)a; (void)CHRONOS_READ(x); return NULL; }
static void *locked_w(void *a) { (void)a; pthread_mutex_lock(&mu); CHRONOS_WRITE(x, 3); pthread_mutex_unlock(&mu); return NULL; }
static void *locked_r(void *a) { (void)a; pthread_mutex_lock(&mu); (void)CHRONOS_READ(x); pthread_mutex_unlock(&mu); return NULL; }
static void *cond_prod(void *a) { (void)a; CHRONOS_WRITE(x, 7); pthread_mutex_lock(&mu); ready = 1; pthread_cond_signal(&cv); pthread_mutex_unlock(&mu); return NULL; }
static void *cond_cons(void *a) { (void)a; pthread_mutex_lock(&mu); while (!ready) pthread_cond_wait(&cv, &mu); pthread_mutex_unlock(&mu); (void)CHRONOS_READ(x); return NULL; }
static void *sem_prod(void *a) { (void)a; CHRONOS_WRITE(x, 8); sem_post(&sm); return NULL; }
static void *sem_cons(void *a) { (void)a; sem_wait(&sm); (void)CHRONOS_READ(x); return NULL; }
static void *bar_a(void *a) { (void)a; CHRONOS_WRITE(x, 9); pthread_barrier_wait(&bar); return NULL; }
static void *bar_b(void *a) { (void)a; pthread_barrier_wait(&bar); (void)CHRONOS_READ(x); return NULL; }
static void *wy(void *a) { (void)a; CHRONOS_WRITE(y, 1); return NULL; }
static void *atom_w(void *a) { (void)a; CHRONOS_WRITE(x, 5); CHRONOS_ATOMIC_STORE(&ready, 1); return NULL; }
static void *atom_r(void *a) { (void)a; if (CHRONOS_ATOMIC_LOAD(&ready)) (void)CHRONOS_READ(x); return NULL; }
static void run2(void *(*f)(void *), void *(*g)(void *)) {
  pthread_t a, b;
  pthread_create(&a, NULL, f, NULL);
  pthread_create(&b, NULL, g, NULL);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
}
int main(int argc, char **argv) {
  mode = argc > 1 ? argv[1] : "ww";
  sem_init(&sm, 0, 0);
  pthread_barrier_init(&bar, NULL, 2);
  if (!strcmp(mode, "ww")) run2(writer1, writer2);
  else if (!strcmp(mode, "wr")) run2(writer1, reader);
  else if (!strcmp(mode, "rrw")) { pthread_t a, b, c; pthread_create(&a, NULL, reader, NULL); pthread_create(&b, NULL, reader, NULL); pthread_create(&c, NULL, writer1, NULL); pthread_join(a, NULL); pthread_join(b, NULL); pthread_join(c, NULL); }
  else if (!strcmp(mode, "mutex")) run2(locked_w, locked_r);
  else if (!strcmp(mode, "join")) { pthread_t a; pthread_create(&a, NULL, writer1, NULL); pthread_join(a, NULL); (void)CHRONOS_READ(x); }
  else if (!strcmp(mode, "create")) { CHRONOS_WRITE(x, 4); pthread_t a; pthread_create(&a, NULL, reader, NULL); pthread_join(a, NULL); }
  else if (!strcmp(mode, "cond")) run2(cond_prod, cond_cons);
  else if (!strcmp(mode, "sem")) run2(sem_prod, sem_cons);
  else if (!strcmp(mode, "barrier")) run2(bar_a, bar_b);
  else if (!strcmp(mode, "disjoint")) run2(writer1, wy);
  else if (!strcmp(mode, "atomic")) run2(atom_w, atom_r);
  else { fprintf(stderr, "bad mode\n"); return 2; }
  printf("done %s\n", mode);
  return 0;
}

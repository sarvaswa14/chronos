/* BUG 4 - lost wake-up: the waiter waits unconditionally (no predicate), so a
 * signal sent before the wait is lost and the waiter blocks forever.
 * Oracle: DEADLOCK[cond_wait,join]. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int data;
static int notified;

static void *waiter(void *arg) {
  (void)arg;
  pthread_mutex_lock(&mu);
  pthread_cond_wait(&cv, &mu); /* BUG: should be `while (!notified) wait` */
  int d = CHRONOS_READ(data);
  pthread_mutex_unlock(&mu);
  printf("waiter saw %d\n", d);
  return NULL;
}
static void *signaler(void *arg) {
  (void)arg;
  pthread_mutex_lock(&mu);
  CHRONOS_WRITE(data, 7);
  notified = 1;
  pthread_cond_signal(&cv);
  pthread_mutex_unlock(&mu);
  return NULL;
}

int main(void) {
  CHRONOS_NAME(&cv, "cv");
  CHRONOS_NAME(&mu, "mu");
  pthread_t w, s;
  pthread_create(&w, NULL, waiter, NULL);
  pthread_create(&s, NULL, signaler, NULL);
  pthread_join(w, NULL);
  pthread_join(s, NULL);
  return 0;
}

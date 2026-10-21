/* waits with `if`: fails only when a spurious wake-up is injected
 * (CHRONOS_SPURIOUS>0). Without the budget it must pass. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int ready;
static void *waiter(void *a) {
  (void)a;
  pthread_mutex_lock(&mu);
  if (!ready) pthread_cond_wait(&cv, &mu);
  CHRONOS_ASSERT(ready == 1, "woke up although the predicate is false");
  pthread_mutex_unlock(&mu);
  return NULL;
}
static void *signaler(void *a) {
  (void)a;
  pthread_mutex_lock(&mu);
  ready = 1;
  pthread_cond_signal(&cv);
  pthread_mutex_unlock(&mu);
  return NULL;
}
int main(void) {
  pthread_t w, s;
  pthread_create(&w, NULL, waiter, NULL);
  pthread_create(&s, NULL, signaler, NULL);
  pthread_join(w, NULL);
  pthread_join(s, NULL);
  printf("ok\n");
  return 0;
}

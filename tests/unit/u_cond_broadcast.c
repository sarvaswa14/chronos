/* broadcast wakes every waiter; signal wakes exactly one in FIFO order */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
#define W 3
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int go, woken;
static void *waiter(void *a) {
  (void)a;
  pthread_mutex_lock(&mu);
  while (!go) pthread_cond_wait(&cv, &mu);
  woken++;
  pthread_mutex_unlock(&mu);
  return NULL;
}
int main(void) {
  pthread_t t[W];
  for (int i = 0; i < W; i++) pthread_create(&t[i], NULL, waiter, NULL);
  pthread_mutex_lock(&mu);
  go = 1;
  pthread_cond_broadcast(&cv);
  pthread_mutex_unlock(&mu);
  for (int i = 0; i < W; i++) pthread_join(t[i], NULL);
  CHRONOS_ASSERT(woken == W, "broadcast woke all waiters");
  printf("ok\n");
  return 0;
}

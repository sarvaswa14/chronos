/* CONTROL - dining philosophers with a global lock order (lower index first).
 * No deadlock, no lock-order cycle. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
#define P 3
static pthread_mutex_t fork_mu[P] = {PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER};
static int meals;
static pthread_mutex_t meals_mu = PTHREAD_MUTEX_INITIALIZER;
static void *philosopher(void *arg) {
  int i = (int)(long)arg;
  int a = i, b = (i + 1) % P;
  if (a > b) { int t = a; a = b; b = t; }
  pthread_mutex_lock(&fork_mu[a]);
  pthread_mutex_lock(&fork_mu[b]);
  pthread_mutex_lock(&meals_mu);
  meals++;
  pthread_mutex_unlock(&meals_mu);
  pthread_mutex_unlock(&fork_mu[b]);
  pthread_mutex_unlock(&fork_mu[a]);
  return NULL;
}
int main(void) {
  pthread_t th[P];
  for (long i = 0; i < P; i++) pthread_create(&th[i], NULL, philosopher, (void *)i);
  for (int i = 0; i < P; i++) pthread_join(th[i], NULL);
  CHRONOS_ASSERT(meals == P, "everyone ate");
  printf("meals=%d\n", meals);
  return 0;
}

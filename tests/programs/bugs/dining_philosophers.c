/* BUG 5 - dining philosophers where everyone takes the left fork first.
 * Oracle: DEADLOCK[join,mutex_lock,mutex_lock,mutex_lock]. The lock-order
 * detector finds a 3-lock / 3-thread cycle. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

#define P 3
static pthread_mutex_t fork_mu[P] = {PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER};
static int meals;

static void *philosopher(void *arg) {
  int i = (int)(long)arg;
  pthread_mutex_lock(&fork_mu[i]);
  pthread_mutex_lock(&fork_mu[(i + 1) % P]);
  meals++;
  pthread_mutex_unlock(&fork_mu[(i + 1) % P]);
  pthread_mutex_unlock(&fork_mu[i]);
  return NULL;
}

int main(void) {
  static const char *names[P] = {"fork0", "fork1", "fork2"};
  for (int i = 0; i < P; i++) CHRONOS_NAME(&fork_mu[i], names[i]);
  pthread_t th[P];
  for (long i = 0; i < P; i++) pthread_create(&th[i], NULL, philosopher, (void *)i);
  for (int i = 0; i < P; i++) pthread_join(th[i], NULL);
  printf("meals=%d\n", meals);
  return 0;
}

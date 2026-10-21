/* CONTROL - correct mutex-protected counter (3 threads). Must never fail,
 * never report a race or a lock-order cycle. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
#define T 3
#define N 3
static int counter;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static void *worker(void *arg) {
  (void)arg;
  for (int i = 0; i < N; i++) {
    pthread_mutex_lock(&mu);
    int t = CHRONOS_READ(counter);
    CHRONOS_WRITE(counter, t + 1);
    pthread_mutex_unlock(&mu);
  }
  return NULL;
}
int main(void) {
  pthread_t th[T];
  for (int i = 0; i < T; i++) pthread_create(&th[i], NULL, worker, NULL);
  for (int i = 0; i < T; i++) pthread_join(th[i], NULL);
  CHRONOS_ASSERT(counter == T * N, "counter");
  printf("counter=%d\n", counter);
  return 0;
}

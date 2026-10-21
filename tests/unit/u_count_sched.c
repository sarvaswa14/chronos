/* 2 workers x 3 independent annotated writes. The number of distinct
 * schedules is the number of linear extensions of a small partial order which
 * the test-suite computes independently (tests/test_runner.py). */
#include <pthread.h>
#include "chronos.h"
static int v[2][3];
static void *worker(void *arg) {
  int id = (int)(long)arg;
  for (int k = 0; k < 3; k++) CHRONOS_WRITE(v[id][k], k);
  return NULL;
}
int main(void) {
  pthread_t a, b;
  pthread_create(&a, NULL, worker, (void *)0L);
  pthread_create(&b, NULL, worker, (void *)1L);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
  return 0;
}

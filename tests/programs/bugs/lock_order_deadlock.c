/* BUG 3 - lock-order inversion (classic AB/BA deadlock).
 * Oracle: DEADLOCK[...] when the unlucky schedule is hit. The lock-order
 * detector reports the cycle as a *potential* deadlock even on schedules that
 * happen to finish. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

static pthread_mutex_t A = PTHREAD_MUTEX_INITIALIZER, B = PTHREAD_MUTEX_INITIALIZER;
static int work_a, work_b;

static void *t1(void *arg) {
  (void)arg;
  pthread_mutex_lock(&A);
  work_a++;
  pthread_mutex_lock(&B);
  work_b++;
  pthread_mutex_unlock(&B);
  pthread_mutex_unlock(&A);
  return NULL;
}
static void *t2(void *arg) {
  (void)arg;
  pthread_mutex_lock(&B);
  work_b++;
  pthread_mutex_lock(&A);
  work_a++;
  pthread_mutex_unlock(&A);
  pthread_mutex_unlock(&B);
  return NULL;
}

int main(void) {
  CHRONOS_NAME(&A, "A");
  CHRONOS_NAME(&B, "B");
  pthread_t x, y;
  pthread_create(&x, NULL, t1, NULL);
  pthread_create(&y, NULL, t2, NULL);
  pthread_join(x, NULL);
  pthread_join(y, NULL);
  printf("done %d %d\n", work_a, work_b);
  return 0;
}

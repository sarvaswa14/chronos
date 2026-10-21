/* exactly one thread per generation receives PTHREAD_BARRIER_SERIAL_THREAD */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
#define T 3
static pthread_barrier_t b;
static int serial_count;
static void *worker(void *a) {
  (void)a;
  for (int gen = 0; gen < 2; gen++)
    if (pthread_barrier_wait(&b) == PTHREAD_BARRIER_SERIAL_THREAD) CHRONOS_ATOMIC_FETCH_ADD(&serial_count, 1);
  return NULL;
}
int main(void) {
  pthread_barrier_init(&b, NULL, T);
  pthread_t t[T];
  for (int i = 0; i < T; i++) pthread_create(&t[i], NULL, worker, NULL);
  for (int i = 0; i < T; i++) pthread_join(t[i], NULL);
  CHRONOS_ASSERT(serial_count == 2, "one serial thread per generation");
  printf("ok\n");
  return 0;
}

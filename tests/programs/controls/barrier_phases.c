/* CONTROL - two-phase computation separated by a reusable barrier.
 * Phase 1: each thread writes its own slot. Phase 2: each thread reads ALL
 * slots. The barrier provides the happens-before edges: no race allowed. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
#define T 3
static int slot[T];
static int sums[T];
static pthread_barrier_t bar;
static void *worker(void *arg) {
  int id = (int)(long)arg;
  CHRONOS_WRITE(slot[id], id + 1);
  pthread_barrier_wait(&bar);
  int s = 0;
  for (int i = 0; i < T; i++) s += CHRONOS_READ(slot[i]);
  CHRONOS_WRITE(sums[id], s);
  pthread_barrier_wait(&bar); /* reuse: second generation */
  CHRONOS_WRITE(slot[id], 0);
  return NULL;
}
int main(void) {
  pthread_barrier_init(&bar, NULL, T);
  pthread_t th[T];
  for (long i = 0; i < T; i++) pthread_create(&th[i], NULL, worker, (void *)i);
  for (int i = 0; i < T; i++) pthread_join(th[i], NULL);
  for (int i = 0; i < T; i++) CHRONOS_ASSERT(sums[i] == 6, "each thread must see all three phase-1 writes");
  pthread_barrier_destroy(&bar);
  printf("ok\n");
  return 0;
}

/* CONTROL - correct producer/consumer with predicate loops (the fixed version
 * of cond_if_not_while). Must pass under every schedule, with spurious
 * wake-ups enabled as well. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
static int count, consumed;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t not_empty = PTHREAD_COND_INITIALIZER;
static void *consumer(void *arg) {
  (void)arg;
  pthread_mutex_lock(&mu);
  while (CHRONOS_READ(count) == 0) pthread_cond_wait(&not_empty, &mu);
  int c = CHRONOS_READ(count);
  CHRONOS_ASSERT(c > 0, "consumer popped from empty queue");
  CHRONOS_WRITE(count, c - 1);
  consumed++;
  pthread_mutex_unlock(&mu);
  return NULL;
}
static void *producer(void *arg) {
  (void)arg;
  for (int i = 0; i < 2; i++) {
    pthread_mutex_lock(&mu);
    int c = CHRONOS_READ(count);
    CHRONOS_WRITE(count, c + 1);
    pthread_cond_signal(&not_empty);
    pthread_mutex_unlock(&mu);
  }
  return NULL;
}
int main(void) {
  pthread_t c1, c2, p;
  pthread_create(&c1, NULL, consumer, NULL);
  pthread_create(&c2, NULL, consumer, NULL);
  pthread_create(&p, NULL, producer, NULL);
  pthread_join(c1, NULL);
  pthread_join(c2, NULL);
  pthread_join(p, NULL);
  CHRONOS_ASSERT(consumed == 2 && count == 0, "all items consumed");
  printf("consumed=%d\n", consumed);
  return 0;
}

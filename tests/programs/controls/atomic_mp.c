/* CONTROL - message passing through a sequentially consistent atomic flag,
 * plus an atomic counter. Data written before the flag store is visible to a
 * reader that observes the flag, with a happens-before edge: no race. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
static int data;
static int flag;
static int hits;
static void *producer(void *arg) {
  (void)arg;
  CHRONOS_WRITE(data, 99);
  CHRONOS_ATOMIC_STORE(&flag, 1);
  CHRONOS_ATOMIC_FETCH_ADD(&hits, 1);
  return NULL;
}
static void *consumer(void *arg) {
  (void)arg;
  if (CHRONOS_ATOMIC_LOAD(&flag)) {
    int d = CHRONOS_READ(data);
    CHRONOS_ASSERT(d == 99, "data visible after flag");
  }
  CHRONOS_ATOMIC_FETCH_ADD(&hits, 1);
  return NULL;
}
int main(void) {
  pthread_t p, c;
  pthread_create(&p, NULL, producer, NULL);
  pthread_create(&c, NULL, consumer, NULL);
  pthread_join(p, NULL);
  pthread_join(c, NULL);
  CHRONOS_ASSERT(__atomic_load_n(&hits, __ATOMIC_SEQ_CST) == 2, "both threads counted");
  printf("ok\n");
  return 0;
}

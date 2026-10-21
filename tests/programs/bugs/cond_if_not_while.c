/* BUG 8 - incorrect condition-variable usage: `if` instead of `while`.
 * A woken consumer does not re-check the predicate, so a second consumer that
 * stole the item makes it pop from an empty queue.
 * Oracle: ASSERT@cond_if_not_while.c. (The correct version of this program is
 * the control prodcons_cv.) */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

static int count;
static int consumed;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t not_empty = PTHREAD_COND_INITIALIZER;

static void *consumer(void *arg) {
  (void)arg;
  pthread_mutex_lock(&mu);
  if (CHRONOS_READ(count) == 0) /* BUG: must be a while loop */
    pthread_cond_wait(&not_empty, &mu);
  int c = CHRONOS_READ(count);
  CHRONOS_ASSERT(c > 0, "consumer popped from an empty queue");
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
  CHRONOS_NAME(&mu, "mu");
  CHRONOS_NAME(&not_empty, "not_empty");
  pthread_t c1, c2, p;
  pthread_create(&c1, NULL, consumer, NULL);
  pthread_create(&c2, NULL, consumer, NULL);
  pthread_create(&p, NULL, producer, NULL);
  pthread_join(c1, NULL);
  pthread_join(c2, NULL);
  pthread_join(p, NULL);
  printf("consumed=%d\n", consumed);
  return 0;
}

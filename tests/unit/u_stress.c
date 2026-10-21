/* protocol stress: 2 producers + 2 consumers over a bounded ring buffer with
 * two condition variables, a start barrier, a semaphore-guarded statistic and
 * pthread_once. Deterministic total; hundreds of scheduling steps per run. */
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include "chronos.h"
#define CAP 3
#define ITEMS 12
static int ring[CAP], head, tail, cnt, done_flag;
static long total;
static int started;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t not_full = PTHREAD_COND_INITIALIZER, not_empty = PTHREAD_COND_INITIALIZER;
static pthread_barrier_t start_bar;
static sem_t stat_sem;
static long stat_ops;
static pthread_once_t once = PTHREAD_ONCE_INIT;
static void init_once(void) { started = 1; }
static void note(void) { sem_wait(&stat_sem); long s = CHRONOS_READ(stat_ops); CHRONOS_WRITE(stat_ops, s + 1); sem_post(&stat_sem); }
static void *producer(void *arg) {
  (void)arg;
  pthread_once(&once, init_once);
  pthread_barrier_wait(&start_bar);
  for (int i = 1; i <= ITEMS; i++) {
    pthread_mutex_lock(&mu);
    while (cnt == CAP) pthread_cond_wait(&not_full, &mu);
    ring[tail] = i; tail = (tail + 1) % CAP; cnt++;
    pthread_cond_signal(&not_empty);
    pthread_mutex_unlock(&mu);
    note();
  }
  return NULL;
}
static void *consumer(void *arg) {
  (void)arg;
  pthread_barrier_wait(&start_bar);
  for (;;) {
    pthread_mutex_lock(&mu);
    while (cnt == 0 && !done_flag) pthread_cond_wait(&not_empty, &mu);
    if (cnt == 0 && done_flag) { pthread_mutex_unlock(&mu); break; }
    int v = ring[head]; head = (head + 1) % CAP; cnt--;
    total += v;
    pthread_cond_signal(&not_full);
    pthread_mutex_unlock(&mu);
    note();
  }
  return NULL;
}
int main(void) {
  sem_init(&stat_sem, 0, 1);
  pthread_barrier_init(&start_bar, NULL, 4);
  pthread_t p1, p2, c1, c2;
  pthread_create(&p1, NULL, producer, NULL);
  pthread_create(&p2, NULL, producer, NULL);
  pthread_create(&c1, NULL, consumer, NULL);
  pthread_create(&c2, NULL, consumer, NULL);
  pthread_join(p1, NULL);
  pthread_join(p2, NULL);
  pthread_mutex_lock(&mu);
  done_flag = 1;
  pthread_cond_broadcast(&not_empty);
  pthread_mutex_unlock(&mu);
  pthread_join(c1, NULL);
  pthread_join(c2, NULL);
  CHRONOS_ASSERT(total == 2L * ITEMS * (ITEMS + 1) / 2, "every item consumed exactly once");
  CHRONOS_ASSERT(stat_ops == 4 * ITEMS, "statistic counted every produce and consume");
  CHRONOS_ASSERT(started == 1, "once ran");
  printf("ok total=%ld\n", total);
  return 0;
}

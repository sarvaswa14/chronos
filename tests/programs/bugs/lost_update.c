/* BUG 1 - lost update (data race / non-atomic read-modify-write).
 * Two workers increment a shared counter with separate read and write steps.
 * Oracle: ASSERT@lost_update.c (final counter != 2*N); detectors also flag RACE.
 * Irrelevant activity (a log mutex + per-thread statistics) makes random
 * failing schedules long, which is what the minimizer is for. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

#define N 3
static int counter;
static int stats[3];
static pthread_mutex_t log_mu = PTHREAD_MUTEX_INITIALIZER;
static int log_lines;

static void noise(int id) {
  pthread_mutex_lock(&log_mu);
  log_lines++;
  pthread_mutex_unlock(&log_mu);
  int s = CHRONOS_READ(stats[id]);
  CHRONOS_WRITE(stats[id], s + 1);
}

static void *worker(void *arg) {
  int id = (int)(long)arg;
  for (int i = 0; i < N; i++) {
    noise(id);
    int t = CHRONOS_READ(counter);
    CHRONOS_WRITE(counter, t + 1);
  }
  return NULL;
}

int main(void) {
  CHRONOS_NAME(&log_mu, "log_mu");
  pthread_t a, b;
  pthread_create(&a, NULL, worker, (void *)1L);
  pthread_create(&b, NULL, worker, (void *)2L);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
  CHRONOS_ASSERT(counter == 2 * N, "lost update: counter != 2*N");
  printf("counter=%d\n", counter);
  return 0;
}

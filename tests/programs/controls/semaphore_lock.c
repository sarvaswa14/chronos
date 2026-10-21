/* CONTROL - a binary semaphore used as a lock around a shared counter, plus a
 * counting semaphore handing out tokens. HB must flow through sem_post ->
 * sem_wait, so there must be no race report. */
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include "chronos.h"
#define T 3
static int counter, tokens_taken;
static sem_t lock_sem, tokens;
static void *worker(void *arg) {
  (void)arg;
  for (int i = 0; i < 2; i++) {
    sem_wait(&lock_sem);
    int t = CHRONOS_READ(counter);
    CHRONOS_WRITE(counter, t + 1);
    sem_post(&lock_sem);
  }
  if (sem_trywait(&tokens) == 0) {
    sem_wait(&lock_sem);
    int k = CHRONOS_READ(tokens_taken);
    CHRONOS_WRITE(tokens_taken, k + 1);
    sem_post(&lock_sem);
  }
  return NULL;
}
int main(void) {
  sem_init(&lock_sem, 0, 1);
  sem_init(&tokens, 0, 2);
  pthread_t th[T];
  for (int i = 0; i < T; i++) pthread_create(&th[i], NULL, worker, NULL);
  for (int i = 0; i < T; i++) pthread_join(th[i], NULL);
  CHRONOS_ASSERT(counter == 2 * T, "counter");
  CHRONOS_ASSERT(tokens_taken == 2, "exactly two tokens are handed out");
  printf("ok\n");
  return 0;
}

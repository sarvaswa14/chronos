/* BUG 7 - broken double-checked initialisation: the second check inside the
 * critical section is missing, so two threads can both initialise.
 * Oracle: ASSERT@double_checked_init.c (init_count != 1). */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

static int instance;
static int init_count;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t stat_mu = PTHREAD_MUTEX_INITIALIZER;
static int requests;

static void *get_instance(void *arg) {
  (void)arg;
  pthread_mutex_lock(&stat_mu);
  requests++;
  pthread_mutex_unlock(&stat_mu);
  if (CHRONOS_READ(instance) == 0) { /* unsynchronised first check */
    pthread_mutex_lock(&mu);
    /* BUG: no re-check of `instance` here */
    int c = CHRONOS_READ(init_count);
    CHRONOS_WRITE(init_count, c + 1);
    CHRONOS_WRITE(instance, 1);
    pthread_mutex_unlock(&mu);
  }
  return NULL;
}

int main(void) {
  pthread_t a, b;
  pthread_create(&a, NULL, get_instance, NULL);
  pthread_create(&b, NULL, get_instance, NULL);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
  CHRONOS_ASSERT(init_count == 1, "singleton initialised more than once");
  printf("init_count=%d\n", init_count);
  return 0;
}

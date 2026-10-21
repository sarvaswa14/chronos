/* BUG 6 - order violation: the worker is started BEFORE the shared
 * configuration is initialised and assumes it is ready.
 * Oracle: ASSERT@order_violation.c. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

static int config_ready;
static int config_val;
static pthread_mutex_t log_mu = PTHREAD_MUTEX_INITIALIZER;
static int log_lines;

static void log_line(void) {
  pthread_mutex_lock(&log_mu);
  log_lines++;
  pthread_mutex_unlock(&log_mu);
}

static void *worker(void *arg) {
  (void)arg;
  log_line();
  int r = CHRONOS_READ(config_ready);
  CHRONOS_ASSERT(r == 1, "worker used the configuration before it was initialised");
  int v = CHRONOS_READ(config_val);
  log_line();
  printf("config=%d\n", v);
  return NULL;
}

int main(void) {
  pthread_t w;
  pthread_create(&w, NULL, worker, NULL);
  log_line();
  CHRONOS_WRITE(config_val, 42); /* initialisation happens too late */
  CHRONOS_WRITE(config_ready, 1);
  pthread_join(w, NULL);
  return 0;
}

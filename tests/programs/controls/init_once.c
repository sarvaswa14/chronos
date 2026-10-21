/* CONTROL - lazy initialisation through pthread_once. The init routine runs
 * exactly once and its writes happen-before every use. */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
static pthread_once_t once = PTHREAD_ONCE_INIT;
static int config, init_calls;
static void init(void) {
  CHRONOS_WRITE(config, 42);
  init_calls++;
}
static void *worker(void *arg) {
  (void)arg;
  pthread_once(&once, init);
  int c = CHRONOS_READ(config);
  CHRONOS_ASSERT(c == 42, "config visible after pthread_once");
  return NULL;
}
int main(void) {
  pthread_t a, b, c;
  pthread_create(&a, NULL, worker, NULL);
  pthread_create(&b, NULL, worker, NULL);
  pthread_create(&c, NULL, worker, NULL);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
  pthread_join(c, NULL);
  CHRONOS_ASSERT(init_calls == 1, "init ran exactly once");
  printf("ok\n");
  return 0;
}

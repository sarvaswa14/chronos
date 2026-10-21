/* sleep/usleep/nanosleep are scheduling points on virtual time: this program
 * would take > 60 s if the delays were real */
#include <pthread.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include "chronos.h"
static int x;
static void *worker(void *a) {
  (void)a;
  sleep(30);
  usleep(2000000);
  struct timespec ts = {5, 0};
  nanosleep(&ts, NULL);
  x = 1;
  return NULL;
}
int main(void) {
  pthread_t t;
  pthread_create(&t, NULL, worker, NULL);
  sleep(10);
  pthread_join(t, NULL);
  CHRONOS_ASSERT(x == 1, "worker finished");
  printf("ok\n");
  return 0;
}

#include <pthread.h>
#include <time.h>
#include "chronos.h"
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t c = PTHREAD_COND_INITIALIZER;
int main(void) {
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  ts.tv_sec += 1;
  pthread_mutex_lock(&m);
  pthread_cond_timedwait(&c, &m, &ts);
  return 0;
}

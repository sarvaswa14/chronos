/* creates argv[1] trivial threads and joins them (limit is 64 incl. main) */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include "chronos.h"
static int n_done;
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static void *w(void *a) { (void)a; pthread_mutex_lock(&m); n_done++; pthread_mutex_unlock(&m); return NULL; }
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 8;
  pthread_t t[200];
  for (int i = 0; i < n; i++) pthread_create(&t[i], NULL, w, NULL);
  for (int i = 0; i < n; i++) pthread_join(t[i], NULL);
  CHRONOS_ASSERT(n_done == n, "all threads ran");
  printf("ok %d\n", n_done);
  return 0;
}

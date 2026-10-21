/* synchronization from a TLS destructor after thread exit is rejected loudly */
#include <pthread.h>
#include "chronos.h"
static pthread_key_t key;
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static void dtor(void *p) { (void)p; pthread_mutex_lock(&m); pthread_mutex_unlock(&m); }
static void *worker(void *a) { (void)a; pthread_setspecific(key, (void *)1); return NULL; }
int main(void) {
  pthread_key_create(&key, dtor);
  pthread_t t;
  pthread_create(&t, NULL, worker, NULL);
  pthread_join(t, NULL);
  return 0;
}

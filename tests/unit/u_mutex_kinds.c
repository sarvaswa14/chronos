/* normal / recursive / error-checking mutex semantics, trylock, EBUSY across threads */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static int try_result = -1;
static void *tryer(void *a) { (void)a; try_result = pthread_mutex_trylock(&m); return NULL; }
int main(void) {
  CHRONOS_ASSERT(pthread_mutex_trylock(&m) == 0, "trylock on free mutex succeeds");
  CHRONOS_ASSERT(pthread_mutex_trylock(&m) == EBUSY, "trylock on held normal mutex -> EBUSY");
  pthread_mutex_unlock(&m);
  pthread_mutex_lock(&m);
  pthread_t t;
  pthread_create(&t, NULL, tryer, NULL);
  pthread_join(t, NULL);
  CHRONOS_ASSERT(try_result == EBUSY, "other thread sees EBUSY while main holds the lock");
  pthread_mutex_unlock(&m);

  pthread_mutexattr_t at;
  pthread_mutex_t r, e;
  pthread_mutexattr_init(&at);
  pthread_mutexattr_settype(&at, PTHREAD_MUTEX_RECURSIVE);
  pthread_mutex_init(&r, &at);
  pthread_mutex_lock(&r);
  pthread_mutex_lock(&r);
  CHRONOS_ASSERT(pthread_mutex_trylock(&r) == 0, "recursive trylock by owner");
  pthread_mutex_unlock(&r);
  pthread_mutex_unlock(&r);
  pthread_mutex_unlock(&r);
  pthread_mutexattr_settype(&at, PTHREAD_MUTEX_ERRORCHECK);
  pthread_mutex_init(&e, &at);
  pthread_mutex_lock(&e);
  CHRONOS_ASSERT(pthread_mutex_lock(&e) == EDEADLK, "errorcheck relock -> EDEADLK");
  CHRONOS_ASSERT(pthread_mutex_unlock(&e) == 0, "errorcheck unlock by owner");
  CHRONOS_ASSERT(pthread_mutex_unlock(&e) == EPERM, "errorcheck unlock of free mutex -> EPERM");
  pthread_mutexattr_destroy(&at);
  pthread_mutex_destroy(&r);
  pthread_mutex_destroy(&e);
  printf("ok\n");
  return 0;
}

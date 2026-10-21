/* counting semaphore semantics: wait/trywait/post/getvalue, cross-thread post */
#include <errno.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include "chronos.h"
static sem_t s, handoff;
static int payload;
static void *poster(void *a) { (void)a; payload = 5; sem_post(&handoff); return NULL; }
int main(void) {
  int v = -1;
  sem_init(&s, 0, 2);
  sem_wait(&s);
  sem_wait(&s);
  CHRONOS_ASSERT(sem_trywait(&s) == -1 && errno == EAGAIN, "trywait on zero -> EAGAIN");
  sem_post(&s);
  sem_getvalue(&s, &v);
  CHRONOS_ASSERT(v == 1, "value is 1 after one post");
  CHRONOS_ASSERT(sem_trywait(&s) == 0, "trywait succeeds when value > 0");
  sem_init(&handoff, 0, 0);
  pthread_t t;
  pthread_create(&t, NULL, poster, NULL);
  sem_wait(&handoff);
  CHRONOS_ASSERT(payload == 5, "post->wait handoff");
  pthread_join(t, NULL);
  sem_destroy(&s);
  sem_destroy(&handoff);
  printf("ok\n");
  return 0;
}

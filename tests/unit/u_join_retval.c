/* return values via return and pthread_exit; detached thread via semaphore */
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include "chronos.h"
static sem_t done;
static int flag;
static void *ret_a(void *a) { (void)a; return (void *)123; }
static void *ret_b(void *a) { (void)a; pthread_exit((void *)456); return NULL; }
static void *detached(void *a) { (void)a; flag = 1; sem_post(&done); return NULL; }
int main(void) {
  pthread_t a, b, d;
  void *ra = NULL, *rb = NULL;
  pthread_create(&a, NULL, ret_a, NULL);
  pthread_create(&b, NULL, ret_b, NULL);
  pthread_join(a, &ra);
  pthread_join(b, &rb);
  CHRONOS_ASSERT((long)ra == 123, "return value via return");
  CHRONOS_ASSERT((long)rb == 456, "return value via pthread_exit");
  sem_init(&done, 0, 0);
  pthread_attr_t at;
  pthread_attr_init(&at);
  pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
  pthread_create(&d, &at, detached, NULL);
  sem_wait(&done);
  CHRONOS_ASSERT(flag == 1, "detached thread ran");
  printf("ok\n");
  return 0;
}

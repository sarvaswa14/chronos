/* lock-order detector truth table; mode in argv[1].
 * Cycle modes: abba three.  Silent modes: gate consistent samethread */
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include "chronos.h"
static pthread_mutex_t A = PTHREAD_MUTEX_INITIALIZER, B = PTHREAD_MUTEX_INITIALIZER, C = PTHREAD_MUTEX_INITIALIZER, G = PTHREAD_MUTEX_INITIALIZER;
static void two(pthread_mutex_t *p, pthread_mutex_t *q) { pthread_mutex_lock(p); pthread_mutex_lock(q); pthread_mutex_unlock(q); pthread_mutex_unlock(p); }
static void gated(pthread_mutex_t *p, pthread_mutex_t *q) { pthread_mutex_lock(&G); two(p, q); pthread_mutex_unlock(&G); }
static void *ab(void *a) { (void)a; two(&A, &B); return NULL; }
static void *ba(void *a) { (void)a; two(&B, &A); return NULL; }
static void *bc(void *a) { (void)a; two(&B, &C); return NULL; }
static void *ca(void *a) { (void)a; two(&C, &A); return NULL; }
static void *gab(void *a) { (void)a; gated(&A, &B); return NULL; }
static void *gba(void *a) { (void)a; gated(&B, &A); return NULL; }
static void *both(void *a) { (void)a; two(&A, &B); two(&B, &A); return NULL; }
static void spawn(int n, void *(*f[])(void *)) {
  pthread_t t[4];
  for (int i = 0; i < n; i++) pthread_create(&t[i], NULL, f[i], NULL);
  for (int i = 0; i < n; i++) pthread_join(t[i], NULL);
}
int main(int argc, char **argv) {
  CHRONOS_NAME(&A, "A"); CHRONOS_NAME(&B, "B"); CHRONOS_NAME(&C, "C"); CHRONOS_NAME(&G, "G");
  const char *m = argc > 1 ? argv[1] : "abba";
  if (!strcmp(m, "abba")) { void *(*f[])(void *) = {ab, ba}; spawn(2, f); }
  else if (!strcmp(m, "three")) { void *(*f[])(void *) = {ab, bc, ca}; spawn(3, f); }
  else if (!strcmp(m, "gate")) { void *(*f[])(void *) = {gab, gba}; spawn(2, f); }
  else if (!strcmp(m, "consistent")) { void *(*f[])(void *) = {ab, ab}; spawn(2, f); }
  else if (!strcmp(m, "samethread")) { void *(*f[])(void *) = {both}; spawn(1, f); }
  else return 2;
  printf("done %s\n", m);
  return 0;
}

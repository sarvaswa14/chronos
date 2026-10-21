#include <pthread.h>
#include "chronos.h"
static void *w(void *a) { (void)a; *(volatile int *)0 = 1; return NULL; }
int main(void) { pthread_t t; pthread_create(&t, NULL, w, NULL); pthread_join(t, NULL); return 0; }

/* relocking a normal mutex deadlocks: DEADLOCK[mutex_lock] */
#include <pthread.h>
#include "chronos.h"
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
int main(void) { pthread_mutex_lock(&m); pthread_mutex_lock(&m); return 0; }

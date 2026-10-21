#include <pthread.h>
#include "chronos.h"
static pthread_rwlock_t l = PTHREAD_RWLOCK_INITIALIZER;
int main(void) { pthread_rwlock_rdlock(&l); pthread_rwlock_unlock(&l); return 0; }

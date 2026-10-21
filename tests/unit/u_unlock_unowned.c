/* unlocking a mutex that is not held: MISUSE[unlock_not_owner] */
#include <pthread.h>
#include "chronos.h"
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
int main(void) { pthread_mutex_unlock(&m); return 0; }

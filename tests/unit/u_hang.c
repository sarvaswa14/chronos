/* a spin loop that never reaches a scheduling point: caught by the watchdog */
#include <pthread.h>
#include "chronos.h"
static volatile int never;
int main(void) { while (!never) { } return 0; }

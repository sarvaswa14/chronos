/* a livelock that keeps hitting scheduling points: caught by the step limit */
#include "chronos.h"
int main(void) { for (;;) CHRONOS_YIELD(); return 0; }

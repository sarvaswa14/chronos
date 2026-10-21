#include <pthread.h>

static int shared_value;

static void *increment(void *unused) {
  (void)unused;
  shared_value = shared_value + 1;
  return 0;
}

int main(void) {
  pthread_t first, second;
  if (pthread_create(&first, 0, increment, 0) != 0) return 2;
  if (pthread_create(&second, 0, increment, 0) != 0) return 3;
  if (pthread_join(first, 0) != 0) return 4;
  if (pthread_join(second, 0) != 0) return 5;
  return shared_value == 2 ? 0 : 1;
}

// CONTROL (C++) - std::thread + std::mutex + std::condition_variable with a
// predicate. Correct under every schedule.
#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <thread>
#include "chronos.h"

static int count = 0, consumed = 0;
static std::mutex mu;
static std::condition_variable cv;

static void consumer() {
  std::unique_lock<std::mutex> lk(mu);
  cv.wait(lk, [] { return CHRONOS_READ(count) > 0; });
  int c = CHRONOS_READ(count);
  CHRONOS_WRITE(count, c - 1);
  consumed++;
}
static void producer() {
  for (int i = 0; i < 2; i++) {
    {
      std::lock_guard<std::mutex> g(mu);
      int c = CHRONOS_READ(count);
      CHRONOS_WRITE(count, c + 1);
    }
    cv.notify_one();
  }
}
int main() {
  std::thread c1(consumer), c2(consumer), p(producer);
  c1.join();
  c2.join();
  p.join();
  CHRONOS_ASSERT(consumed == 2 && count == 0, "all consumed");
  std::printf("consumed=%d\n", consumed);
  return 0;
}

// BUG 9 (C++) - atomicity violation with std::thread / std::mutex.
// Demonstrates that interposition covers libstdc++ synchronization, which is
// built on the same pthread entry points.
// Oracle: ASSERT@cpp_atomicity.cpp.
#include <cstdio>
#include <mutex>
#include <thread>
#include "chronos.h"

static int slots = 1;
static int granted = 0;
static std::mutex mu;

static void reserve() {
  int s;
  {
    std::lock_guard<std::mutex> g(mu);
    s = CHRONOS_READ(slots); // check
  }
  if (s > 0) {
    std::lock_guard<std::mutex> g(mu); // act: slot may already be taken
    int cur = CHRONOS_READ(slots);
    CHRONOS_WRITE(slots, cur - 1);
    granted++;
  }
}

int main() {
  std::thread a(reserve), b(reserve);
  a.join();
  b.join();
  CHRONOS_ASSERT(granted <= 1, "slot granted twice");
  std::printf("granted=%d\n", granted);
  return 0;
}

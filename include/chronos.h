/* chronos.h - annotation API for Chronos (Phase 1).
 *
 * Include this header in programs under test. Every macro degrades to the
 * plain operation when the program runs WITHOUT libchronos.so preloaded
 * (the runtime hooks are declared weak), so the same binary can be run
 * natively as a stress-test baseline.
 *
 *   CHRONOS_READ(x)        -> value of x   (scheduling point + race check)
 *   CHRONOS_WRITE(x, v)    -> x = v        (scheduling point + race check)
 *   CHRONOS_ASSERT(c, msg) -> failure oracle (ASSERT@file:line)
 *   CHRONOS_ATOMIC_*       -> sequentially consistent atomics (sync ops)
 *   CHRONOS_NAME(p, "n")   -> give a sync object a readable name
 *   CHRONOS_YIELD()        -> explicit scheduling point
 */
#ifndef CHRONOS_H
#define CHRONOS_H

#include <stdio.h>
#include <stdlib.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { CHRONOS_ACC_READ = 0, CHRONOS_ACC_WRITE = 1 };
enum { CHRONOS_AT_LOAD = 0, CHRONOS_AT_STORE = 1, CHRONOS_AT_RMW = 2 };

void chronos_access(const void *addr, unsigned size, int is_write,
                    const char *name, const char *file, int line) __attribute__((weak));
void chronos_atomic_point(const void *addr, int kind, const char *name,
                          const char *file, int line) __attribute__((weak));
void chronos_assert_fail(const char *expr, const char *msg, const char *file,
                         int line) __attribute__((weak));
void chronos_name(const void *obj, const char *name) __attribute__((weak));
void chronos_yield(void) __attribute__((weak));

#ifdef __cplusplus
}
#endif

static inline void chronos__native_assert_fail(const char *expr, const char *msg,
                                               const char *file, int line) {
  fprintf(stderr, "ASSERTION FAILED %s:%d: %s (%s)\n", file, line, expr, msg);
  abort();
}

#define CHRONOS_READ(x)                                                        \
  __extension__({                                                              \
    if (chronos_access)                                                        \
      chronos_access((const void *)&(x), (unsigned)sizeof(x),                  \
                     CHRONOS_ACC_READ, #x, __FILE__, __LINE__);                \
    (x);                                                                       \
  })

#define CHRONOS_WRITE(x, v)                                                    \
  do {                                                                         \
    if (chronos_access)                                                        \
      chronos_access((const void *)&(x), (unsigned)sizeof(x),                  \
                     CHRONOS_ACC_WRITE, #x, __FILE__, __LINE__);               \
    (x) = (v);                                                                 \
  } while (0)

#define CHRONOS_ASSERT(cond, msg)                                              \
  do {                                                                         \
    if (!(cond)) {                                                             \
      if (chronos_assert_fail)                                                 \
        chronos_assert_fail(#cond, (msg), __FILE__, __LINE__);                 \
      else                                                                     \
        chronos__native_assert_fail(#cond, (msg), __FILE__, __LINE__);         \
    }                                                                          \
  } while (0)

#define CHRONOS_ATOMIC_PT(p, kind)                                             \
  do {                                                                         \
    if (chronos_atomic_point)                                                  \
      chronos_atomic_point((const void *)(p), (kind), #p, __FILE__, __LINE__); \
  } while (0)

#define CHRONOS_ATOMIC_LOAD(p)                                                 \
  __extension__({                                                              \
    CHRONOS_ATOMIC_PT(p, CHRONOS_AT_LOAD);                                     \
    __atomic_load_n((p), __ATOMIC_SEQ_CST);                                    \
  })
#define CHRONOS_ATOMIC_STORE(p, v)                                             \
  do {                                                                         \
    CHRONOS_ATOMIC_PT(p, CHRONOS_AT_STORE);                                    \
    __atomic_store_n((p), (v), __ATOMIC_SEQ_CST);                              \
  } while (0)
#define CHRONOS_ATOMIC_FETCH_ADD(p, v)                                         \
  __extension__({                                                              \
    CHRONOS_ATOMIC_PT(p, CHRONOS_AT_RMW);                                      \
    __atomic_fetch_add((p), (v), __ATOMIC_SEQ_CST);                            \
  })
#define CHRONOS_ATOMIC_EXCHANGE(p, v)                                          \
  __extension__({                                                              \
    CHRONOS_ATOMIC_PT(p, CHRONOS_AT_RMW);                                      \
    __atomic_exchange_n((p), (v), __ATOMIC_SEQ_CST);                           \
  })
#define CHRONOS_ATOMIC_CAS(p, expected_ptr, desired)                           \
  __extension__({                                                              \
    CHRONOS_ATOMIC_PT(p, CHRONOS_AT_RMW);                                      \
    __atomic_compare_exchange_n((p), (expected_ptr), (desired), 0,             \
                                __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);           \
  })

#define CHRONOS_NAME(obj, nm)                                                  \
  do {                                                                         \
    if (chronos_name) chronos_name((const void *)(obj), (nm));                 \
  } while (0)
#define CHRONOS_YIELD()                                                        \
  do {                                                                         \
    if (chronos_yield) chronos_yield();                                        \
  } while (0)

#endif /* CHRONOS_H */

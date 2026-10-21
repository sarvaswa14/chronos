/* BUG 2 - atomicity violation (check-then-act across two critical sections).
 * Every access to `balance` is protected by the mutex, so there is NO data
 * race: a race detector stays silent. Only schedule exploration finds it.
 * Oracle: ASSERT@atomicity_violation.c (balance went negative). */
#include <pthread.h>
#include <stdio.h>
#include "chronos.h"

static int balance = 100;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t audit_mu = PTHREAD_MUTEX_INITIALIZER;
static int audits;

static void audit(void) {
  pthread_mutex_lock(&audit_mu);
  audits++;
  pthread_mutex_unlock(&audit_mu);
}

static void *withdraw(void *arg) {
  (void)arg;
  audit();
  pthread_mutex_lock(&mu);
  int b = CHRONOS_READ(balance); /* check */
  pthread_mutex_unlock(&mu);
  audit();
  if (b >= 100) {
    pthread_mutex_lock(&mu); /* act - but balance may have changed */
    int cur = CHRONOS_READ(balance);
    CHRONOS_WRITE(balance, cur - 100);
    pthread_mutex_unlock(&mu);
  }
  audit();
  return NULL;
}

int main(void) {
  CHRONOS_NAME(&mu, "balance_mu");
  CHRONOS_NAME(&audit_mu, "audit_mu");
  pthread_t a, b;
  pthread_create(&a, NULL, withdraw, NULL);
  pthread_create(&b, NULL, withdraw, NULL);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
  CHRONOS_ASSERT(balance >= 0, "balance went negative (double withdrawal)");
  printf("balance=%d\n", balance);
  return 0;
}

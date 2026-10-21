/* schedule_check: loads a schedule (or override) file through the production
 * parsers. Exit 0 = parsed, 1 = rejected cleanly. Built with ASan+UBSan, so
 * any memory error / UB on malformed input aborts with a distinct status. */
#include <stdio.h>
#include <string.h>
#include "rt.h"

int main(int argc, char **argv) {
  if (argc != 3) {
    fprintf(stderr, "usage: schedule_check sched|over|prefix FILE\n");
    return 2;
  }
  char err[256] = "";
  if (!strcmp(argv[1], "sched")) {
    ch_sched s;
    int rc = ch_sched_load(argv[2], &s, err, sizeof err);
    if (rc != 0) { printf("REJECT %d %s\n", rc, err); return 1; }
    printf("OK steps=%u\n", s.n);
    ch_sched_free(&s);
    return 0;
  } else if (!strcmp(argv[1], "prefix")) {
    ch_prefix p;
    int rc = ch_prefix_load(argv[2], &p, err, sizeof err);
    if (rc != 0) { printf("REJECT %d %s\n", rc, err); return 1; }
    printf("OK prefix=%u\n", p.n);
    ch_prefix_free(&p);
    return 0;
  } else {
    ch_overrides o;
    int rc = ch_over_load(argv[2], &o, err, sizeof err);
    if (rc != 0) { printf("REJECT %d %s\n", rc, err); return 1; }
    printf("OK overrides=%u\n", o.n);
    ch_over_free(&o);
    return 0;
  }
}

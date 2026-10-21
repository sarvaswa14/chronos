/* rt_race.c - FastTrack-style happens-before race detection on *annotated*
 * accesses (Flanagan & Freund, PLDI'09).
 *
 * Variables are identified by their exact address; partially overlapping
 * accesses of different sizes are not related (documented limitation).
 * Only accesses made through CHRONOS_READ / CHRONOS_WRITE are observed.
 */
#include "rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint32_t clk; int16_t tid; } epoch_t; /* clk==0 : "never" */

typedef struct {
  int tid, step, is_write, line;
  const char *file, *name;
} site_t;

typedef struct shadow {
  const void *addr;
  struct shadow *next;
  epoch_t w;
  site_t wsite;
  int shared;          /* read state is a vector clock */
  epoch_t r;           /* when !shared */
  site_t rsite;
  uint32_t rvc[CH_MAXT];
  site_t rsites[CH_MAXT];
} shadow_t;

#define SH_HASH 4096
static shadow_t *g_sh[SH_HASH];

static shadow_t *shadow_get(const void *addr) {
  uintptr_t x = (uintptr_t)addr;
  x ^= x >> 15;
  x *= 0x9e3779b97f4a7c15ULL;
  unsigned h = (unsigned)((x >> 32) % SH_HASH);
  for (shadow_t *s = g_sh[h]; s; s = s->next)
    if (s->addr == addr) return s;
  shadow_t *s = (shadow_t *)calloc(1, sizeof *s);
  if (!s) ch_fail(F_UNSUPPORTED, "UNSUPPORTED[out_of_memory]", NULL, 0, "out of memory (race shadow)");
  s->addr = addr;
  s->next = g_sh[h];
  g_sh[h] = s;
  return s;
}

static int ordered(epoch_t e, const ch_thr *t) { /* e happens-before t's current point? */
  return e.clk == 0 || e.clk <= t->vc[e.tid];
}

static void site_str(char *dst, size_t n, const site_t *s) {
  snprintf(dst, n, "%s@%s:%d(%c)", s->name ? s->name : "?", ch_base(s->file), s->line, s->is_write ? 'W' : 'R');
}

static void report(const site_t *prev, const site_t *cur) {
  ch_finding f;
  memset(&f, 0, sizeof f);
  f.kind = F_RACE;
  char a[80], b[80];
  site_str(a, sizeof a, prev);
  site_str(b, sizeof b, cur);
  const char *lo = strcmp(a, b) <= 0 ? a : b;
  const char *hi = strcmp(a, b) <= 0 ? b : a;
  snprintf(f.sig, sizeof f.sig, "RACE[%s,%s]", lo, hi);
  snprintf(f.msg, sizeof f.msg, "data race on '%s': t%d %s at %s:%d vs t%d %s at %s:%d (no happens-before)",
           cur->name ? cur->name : "?", prev->tid, prev->is_write ? "write" : "read", ch_base(prev->file), prev->line,
           cur->tid, cur->is_write ? "write" : "read", ch_base(cur->file), cur->line);
  f.tid = cur->tid;
  f.step = cur->step;
  f.file = cur->file;
  f.line = cur->line;
  f.a.tid = prev->tid; f.a.step = prev->step; f.a.is_write = prev->is_write; f.a.line = prev->line; f.a.file = prev->file; f.a.name = prev->name;
  f.b.tid = cur->tid; f.b.step = cur->step; f.b.is_write = cur->is_write; f.b.line = cur->line; f.b.file = cur->file; f.b.name = cur->name;
  ch_report(&f);
}

void ch_race_access(ch_thr *t, const void *addr, unsigned size, int is_write, const char *name, const char *file,
                    int line) {
  if (size == 0 || size > 8) return; /* only scalar accesses are tracked */
  shadow_t *s = shadow_get(addr);
  site_t cur = {t->tid, ch_cur_step(), is_write, line, file, name};
  epoch_t me = {t->vc[t->tid], (int16_t)t->tid};
  if (!is_write) {
    if (!s->shared && s->r.clk == me.clk && s->r.tid == me.tid) return;       /* same epoch */
    if (s->shared && s->rvc[t->tid] == me.clk) return;                        /* same epoch (shared) */
    if (!ordered(s->w, t)) report(&s->wsite, &cur);                            /* write-read race */
    if (!s->shared) {
      if (ordered(s->r, t)) {
        s->r = me;
        s->rsite = cur;
      } else { /* concurrent readers: switch to vector-clock representation */
        s->shared = 1;
        memset(s->rvc, 0, sizeof s->rvc);
        s->rvc[s->r.tid] = s->r.clk;
        s->rsites[s->r.tid] = s->rsite;
        s->rvc[t->tid] = me.clk;
        s->rsites[t->tid] = cur;
      }
    } else {
      s->rvc[t->tid] = me.clk;
      s->rsites[t->tid] = cur;
    }
  } else {
    if (s->w.clk == me.clk && s->w.tid == me.tid) return;                     /* same epoch */
    if (!ordered(s->w, t)) report(&s->wsite, &cur);                            /* write-write race */
    if (!s->shared) {
      if (!ordered(s->r, t)) report(&s->rsite, &cur);                          /* read-write race */
    } else {
      for (int i = 0; i < g_nthreads; i++)
        if (s->rvc[i] > t->vc[i]) {
          report(&s->rsites[i], &cur);
          break;
        }
      s->shared = 0;
      s->r.clk = 0;
    }
    s->w = me;
    s->wsite = cur;
  }
}

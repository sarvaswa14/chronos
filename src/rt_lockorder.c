/* rt_lockorder.c - GoodLock-style potential-deadlock detection.
 *
 * Every acquisition of lock L while holding set H adds edges h->L (h in H),
 * labelled with the acquiring thread and the full held set (the "guard").
 * A cycle is reported only when its edges come from pairwise different
 * threads and have pairwise disjoint guards (a common gate lock rules the
 * cycle out). A reported cycle is a *potential* deadlock; an actual deadlock
 * is only ever reported by the scheduler when no thread is runnable.
 */
#include "rt.h"

#include <stdio.h>
#include <string.h>

#define MAX_EDGES 4096
#define MAX_CYCLE 4
#define GW (CH_LO_MAXLOCKS / 64)

typedef struct {
  int from, to, tid;
  uint64_t guard[GW];
  const char *file;
  int line;
} edge_t;

static int g_node_obj[CH_LO_MAXLOCKS]; /* node index -> obj id */
static int g_nnodes;
static edge_t g_edges[MAX_EDGES];
static int g_nedges;
static int g_overflow_reported;

static int node_of(int obj_id) {
  for (int i = 0; i < g_nnodes; i++)
    if (g_node_obj[i] == obj_id) return i;
  if (g_nnodes >= CH_LO_MAXLOCKS) return -1;
  g_node_obj[g_nnodes] = obj_id;
  return g_nnodes++;
}

static int guards_disjoint(const edge_t *a, const edge_t *b) {
  for (int i = 0; i < GW; i++)
    if (a->guard[i] & b->guard[i]) return 0;
  return 1;
}

static const char *node_name(int node) {
  const ch_obj *o = ch_obj_by_id(g_node_obj[node]);
  return o ? o->name : "?";
}

static void report_cycle(const edge_t *const *path, int len) {
  /* canonical rotation: start at the lexicographically smallest lock name */
  int start = 0;
  for (int i = 1; i < len; i++)
    if (strcmp(node_name(path[i]->from), node_name(path[start]->from)) < 0) start = i;
  ch_finding f;
  memset(&f, 0, sizeof f);
  f.kind = F_LOCKORDER;
  size_t n = (size_t)snprintf(f.sig, sizeof f.sig, "LOCKORDER[");
  for (int i = 0; i < len && n < sizeof f.sig - 50; i++) {
    const edge_t *e = path[(start + i) % len];
    int w = snprintf(f.sig + n, sizeof f.sig - n, "%s->", node_name(e->from));
    if (w > 0) n += (size_t)w;
  }
  snprintf(f.sig + n, sizeof f.sig - n, "%s]", node_name(path[start]->from));
  char tids[80];
  size_t tl = 0;
  tids[0] = 0;
  for (int i = 0; i < len && tl < sizeof tids - 12; i++) {
    int w = snprintf(tids + tl, sizeof tids - tl, "%st%d", i ? "," : "", path[i]->tid);
    if (w > 0) tl += (size_t)w;
  }
  snprintf(f.msg, sizeof f.msg, "potential deadlock: inconsistent lock order across threads %s (cycle of %d locks)", tids, len);
  f.tid = tls_self ? tls_self->tid : -1;
  f.step = ch_cur_step();
  f.file = path[0]->file;
  f.line = path[0]->line;
  ch_report(&f);
}

static void search(const edge_t *first, int cur, const edge_t **path, int depth) {
  if (depth >= MAX_CYCLE) return;
  for (int i = 0; i < g_nedges; i++) {
    const edge_t *e = &g_edges[i];
    if (e->from != cur) continue;
    int ok = 1;
    for (int k = 0; k < depth && ok; k++)
      if (path[k]->tid == e->tid || !guards_disjoint(path[k], e)) ok = 0;
    if (!ok) continue;
    path[depth] = e;
    if (e->to == first->from) {
      report_cycle(path, depth + 1);
    } else {
      /* avoid revisiting nodes already on the path */
      int seen = 0;
      for (int k = 0; k <= depth; k++)
        if (path[k]->from == e->to) seen = 1;
      if (!seen) search(first, e->to, path, depth + 1);
    }
  }
}

void ch_lo_push(ch_thr *t, ch_obj *m) {
  if (t->nheld < (int)(sizeof t->held / sizeof t->held[0])) t->held[t->nheld++] = m->id;
}

void ch_lo_acquire(ch_thr *t, ch_obj *m) {
  int to = node_of(m->id);
  if (to < 0) {
    if (!g_overflow_reported) {
      g_overflow_reported = 1;
      fprintf(stderr, "chronos: lock-order tracking limited to %d locks\n", CH_LO_MAXLOCKS);
    }
    ch_lo_push(t, m);
    return;
  }
  edge_t base;
  memset(&base, 0, sizeof base);
  for (int i = 0; i < t->nheld; i++) {
    int h = node_of(t->held[i]);
    if (h >= 0) base.guard[h / 64] |= 1ULL << (h % 64);
  }
  const ch_step *s = ch_cur_step_rec();
  for (int i = 0; i < t->nheld; i++) {
    int from = node_of(t->held[i]);
    if (from < 0 || from == to) continue;
    int dup = 0;
    for (int k = 0; k < g_nedges; k++) {
      const edge_t *e = &g_edges[k];
      if (e->from == from && e->to == to && e->tid == t->tid && !memcmp(e->guard, base.guard, sizeof base.guard)) dup = 1;
    }
    if (dup || g_nedges >= MAX_EDGES) continue;
    edge_t *e = &g_edges[g_nedges++];
    *e = base;
    e->from = from;
    e->to = to;
    e->tid = t->tid;
    e->file = s ? s->file : NULL;
    e->line = s ? s->line : 0;
    const edge_t *path[MAX_CYCLE + 1];
    path[0] = e;
    search(e, to, path, 1);
  }
  ch_lo_push(t, m);
}

void ch_lo_release(ch_thr *t, ch_obj *m) {
  for (int i = t->nheld - 1; i >= 0; i--)
    if (t->held[i] == m->id) {
      for (int k = i; k + 1 < t->nheld; k++) t->held[k] = t->held[k + 1];
      t->nheld--;
      return;
    }
}

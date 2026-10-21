/* rt_out.c - result / trace / schedule writers.
 *
 * All formatting is done with hand-written, allocation-free routines and
 * write(2) so the same code can run from a fatal-signal handler or from the
 * watchdog thread.
 */
#include "rt.h"

#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>


const char *const ch_fkind_names[F__COUNT] = {"ASSERT", "DEADLOCK", "RACE",        "LOCKORDER", "CRASH",
                                              "MISUSE", "UNSUPPORTED", "STEP_LIMIT", "HANG"};

const char *const ch_okind_names[6] = {"mutex", "cond", "sem", "barrier", "once", "atomic"};


char g_prog[256];
char g_div_reason[200];
uint32_t g_div_step;
int g_divergence;
int g_quiet;

typedef struct {
  int fd, err;
  size_t n;
  char b[1 << 15];
} wb_t;

static void wb_flush(wb_t *w) {
  size_t off = 0;
  while (off < w->n) {
    ssize_t r = write(w->fd, w->b + off, w->n - off);
    if (r < 0) {
      if (errno == EINTR) continue;
      w->err = 1;
      break;
    }
    off += (size_t)r;
  }
  w->n = 0;
}
static void wb_c(wb_t *w, char c) {
  if (w->n == sizeof w->b) wb_flush(w);
  w->b[w->n++] = c;
}
static void wb_s(wb_t *w, const char *s) {
  if (!s) return;
  while (*s) wb_c(w, *s++);
}
static void wb_u(wb_t *w, uint64_t v) {
  char t[24];
  int i = 0;
  if (v == 0) {
    wb_c(w, '0');
    return;
  }
  while (v) {
    t[i++] = (char)('0' + v % 10);
    v /= 10;
  }
  while (i) wb_c(w, t[--i]);
}
static void wb_i(wb_t *w, int64_t v) {
  if (v < 0) {
    wb_c(w, '-');
    wb_u(w, (uint64_t)(-(v + 1)) + 1u);
  } else
    wb_u(w, (uint64_t)v);
}
static void wb_hexraw(wb_t *w, uint64_t v) {
  static const char d[] = "0123456789abcdef";
  char t[17];
  int i = 0;
  if (v == 0) t[i++] = '0';
  while (v) {
    t[i++] = d[v & 15];
    v >>= 4;
  }
  while (i) wb_c(w, t[--i]);
}
static void wb_js(wb_t *w, const char *s) {
  static const char d[] = "0123456789abcdef";
  wb_c(w, '"');
  if (s) {
    for (; *s; s++) {
      unsigned char c = (unsigned char)*s;
      if (c == '"' || c == '\\') {
        wb_c(w, '\\');
        wb_c(w, (char)c);
      } else if (c < 0x20 || c >= 0x7f) {
        wb_s(w, "\\u00");
        wb_c(w, d[c >> 4]);
        wb_c(w, d[c & 15]);
      } else
        wb_c(w, (char)c);
    }
  }
  wb_c(w, '"');
}

static void count_switches(uint32_t *sw, uint32_t *pre) {
  uint32_t s = 0, p = 0;
  for (uint32_t i = 1; i < g_nsteps; i++) {
    int prev = g_steps[i - 1].tid, cur = g_steps[i].tid;
    if (prev != cur) {
      s++;
      if ((g_steps[i].run >> prev) & 1u) p++;
    }
  }
  *sw = s;
  *pre = p;
}

static const char *status_str(void) {
  if (g_divergence) return "divergence";
  if (g_have_failure) return "failure";
  return "ok";
}

static void wr_finding(wb_t *w, const ch_finding *f) {
  wb_s(w, "{\"type\":");
  wb_js(w, ch_fkind_names[f->kind]);
  wb_s(w, ",\"signature\":");
  wb_js(w, f->sig);
  wb_s(w, ",\"message\":");
  wb_js(w, f->msg);
  wb_s(w, ",\"tid\":");
  wb_i(w, f->tid);
  wb_s(w, ",\"step\":");
  wb_i(w, f->step);
  wb_s(w, ",\"file\":");
  wb_js(w, ch_base(f->file));
  wb_s(w, ",\"line\":");
  wb_i(w, f->line);
  if (f->kind == F_RACE) {
    for (int k = 0; k < 2; k++) {
      const typeof(f->a) *x = k ? &f->b : &f->a;
      wb_s(w, k ? ",\"b\":{" : ",\"a\":{");
      wb_s(w, "\"tid\":");
      wb_i(w, x->tid);
      wb_s(w, ",\"step\":");
      wb_i(w, x->step);
      wb_s(w, ",\"write\":");
      wb_s(w, x->is_write ? "true" : "false");
      wb_s(w, ",\"var\":");
      wb_js(w, x->name);
      wb_s(w, ",\"file\":");
      wb_js(w, ch_base(x->file));
      wb_s(w, ",\"line\":");
      wb_i(w, x->line);
      wb_c(w, '}');
    }
  }
  wb_c(w, '}');
}

static const char *strat_name(void) {
  switch (g_cfg.strategy) {
    case ST_RANDOM: return "random";
    case ST_PCT: return "pct";
    case ST_REPLAY: return "replay";
    case ST_PREEMPT: return "preempt";
    case ST_PREFIX: return "prefix";
    default: return "base";
  }
}

static void wr_result(wb_t *w) {
  uint32_t sw, pre;
  count_switches(&sw, &pre);
  wb_s(w, "{\"chronos\":1,\"status\":");
  wb_js(w, status_str());
  wb_s(w, ",\"program\":");
  wb_js(w, g_prog);
  wb_s(w, ",\"strategy\":");
  wb_js(w, strat_name());
  wb_s(w, ",\"seed\":");
  wb_u(w, g_cfg.seed);
  wb_s(w, ",\"steps\":");
  wb_u(w, g_nsteps);
  wb_s(w, ",\"threads\":");
  wb_u(w, (uint64_t)g_nthreads);
  wb_s(w, ",\"switches\":");
  wb_u(w, sw);
  wb_s(w, ",\"preemptions\":");
  wb_u(w, pre);
  if (g_have_failure) {
    wb_s(w, ",\"failure\":");
    wr_finding(w, &g_failure);
  }
  if (g_divergence) {
    wb_s(w, ",\"divergence\":{\"step\":");
    wb_u(w, g_div_step);
    wb_s(w, ",\"reason\":");
    wb_js(w, g_div_reason);
    wb_c(w, '}');
  }
  wb_s(w, ",\"findings\":[");
  for (int i = 0; i < g_nfindings; i++) {
    if (i) wb_c(w, ',');
    wr_finding(w, &g_findings[i]);
  }
  wb_s(w, "]}");
}

static void wr_trace(wb_t *w) {
  wb_s(w, "{\"chronos_trace\":1,\"program\":");
  wb_js(w, g_prog);
  wb_s(w, ",\"strategy\":");
  wb_js(w, strat_name());
  wb_s(w, ",\"seed\":");
  wb_u(w, g_cfg.seed);
  wb_s(w, ",\"threads\":");
  wb_u(w, (uint64_t)g_nthreads);
  wb_s(w, ",\"steps\":");
  wb_u(w, g_nsteps);
  wb_s(w, ",\"objects\":[");
  int n = ch_nobjs();
  for (int i = 0; i < n; i++) {
    ch_obj *o = ch_obj_by_id(i);
    if (i) wb_c(w, ',');
    wb_s(w, "{\"id\":");
    wb_u(w, (uint64_t)o->id);
    wb_s(w, ",\"kind\":");
    wb_js(w, ch_okind_names[o->kind]);
    wb_s(w, ",\"name\":");
    wb_js(w, o->name);
    wb_c(w, '}');
  }
  wb_s(w, "]}\n");
  for (uint32_t i = 0; i < g_nsteps; i++) {
    const ch_step *s = &g_steps[i];
    wb_s(w, "{\"i\":");
    wb_u(w, s->step);
    wb_s(w, ",\"t\":");
    wb_i(w, s->tid);
    wb_s(w, ",\"k\":");
    wb_u(w, s->opidx);
    wb_s(w, ",\"op\":");
    wb_js(w, ch_op_names[s->op]);
    wb_s(w, ",\"o\":");
    wb_i(w, s->obj);
    wb_s(w, ",\"a\":");
    wb_i(w, s->aux);
    wb_s(w, ",\"run\":\"");
    wb_hexraw(w, s->run);
    wb_s(w, "\",\"hb\":[");
    int first = 1;
    for (int h = 0; h < 3; h++)
      if (s->hb[h] >= 0) {
        if (!first) wb_c(w, ',');
        wb_i(w, s->hb[h]);
        first = 0;
      }
    wb_c(w, ']');
    if (s->name) {
      wb_s(w, ",\"nm\":");
      wb_js(w, s->name);
    }
    if (s->file) {
      wb_s(w, ",\"f\":");
      wb_js(w, ch_base(s->file));
      wb_s(w, ",\"l\":");
      wb_i(w, s->line);
    }
    wb_s(w, "}\n");
  }
  wb_s(w, "{\"end\":1,\"result\":");
  wr_result(w);
  wb_s(w, "}\n");
}

static void wr_schedule(wb_t *w) {
  wb_s(w, "chronos-schedule 1\nprogram ");
  /* program names are single tokens; replace blanks defensively */
  for (const char *p = g_prog; *p; p++) wb_c(w, (*p == ' ' || *p == '\n' || *p == '\t') ? '_' : *p);
  wb_c(w, '\n');
  if (g_have_failure) {
    wb_s(w, "fail ");
    for (const char *p = g_failure.sig; *p; p++) wb_c(w, (*p == ' ' || *p == '\n' || *p == '\t') ? '_' : *p);
    wb_c(w, '\n');
  }
  wb_s(w, "steps ");
  wb_u(w, g_nsteps);
  wb_c(w, '\n');
  for (uint32_t i = 0; i < g_nsteps; i++) {
    const ch_step *s = &g_steps[i];
    wb_u(w, s->step);
    wb_c(w, ' ');
    wb_i(w, s->tid);
    wb_c(w, ' ');
    wb_u(w, s->opidx);
    wb_c(w, ' ');
    wb_s(w, ch_op_names[s->op]);
    wb_c(w, ' ');
    wb_i(w, s->obj);
    wb_c(w, ' ');
    wb_hexraw(w, s->run);
    wb_c(w, '\n');
  }
  wb_s(w, "end\n");
}

static void emit_file(const char *path, void (*fn)(wb_t *)) {
  if (!path || !*path) return;
  static wb_t w; /* static: too large for a signal stack */
  memset(&w, 0, sizeof w);
  w.fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (w.fd < 0) return;
  fn(&w);
  wb_flush(&w);
  close(w.fd);
}

static void wr_result_nl(wb_t *w) {
  wr_result(w);
  wb_c(w, '\n');
}

static atomic_int g_done;

void ch_finalize(int mode) {
  if (atomic_exchange(&g_done, 1)) {
    if (mode == FIN_WATCHDOG) return;
    for (;;) pause(); /* another path is finishing the process */
  }
  if (mode == FIN_NORMAL && g_replay_on && !g_divergence && !g_have_failure && g_nsteps != g_replay_n && !g_cfg.prefix_ok) {
    g_divergence = 1;
    g_div_step = g_nsteps;
    snprintf(g_div_reason, sizeof g_div_reason, "program finished after %u steps but schedule has %u", g_nsteps, g_replay_n);
  }
  if (mode == FIN_FAIL) fflush(NULL);
  emit_file(g_cfg.result_out, wr_result_nl);
  emit_file(g_cfg.sched_out, wr_schedule);
  emit_file(g_cfg.trace_out, wr_trace);
  if (!g_quiet && (g_have_failure || g_divergence)) {
    static wb_t w;
    memset(&w, 0, sizeof w);
    w.fd = 2;
    wb_s(&w, "chronos: ");
    if (g_divergence) {
      wb_s(&w, "DIVERGENCE at step ");
      wb_u(&w, g_div_step);
      wb_s(&w, ": ");
      wb_s(&w, g_div_reason);
    } else {
      wb_s(&w, "FAILURE ");
      wb_s(&w, g_failure.sig);
      wb_s(&w, " (");
      wb_s(&w, g_failure.msg);
      wb_s(&w, ") after ");
      wb_u(&w, g_nsteps);
      wb_s(&w, " steps");
    }
    wb_c(&w, '\n');
    wb_flush(&w);
  }
  if (g_divergence) _exit(87);
  if (g_have_failure) _exit(86);
  if (mode != FIN_NORMAL) _exit(0);
}

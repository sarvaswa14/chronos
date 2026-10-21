/* rt_schedfile.c - strict, bounds-checked parsers for schedule and override
 * files. These are the only places where the runtime reads untrusted text, so
 * they are fuzzed separately (tests/fuzz). They never read past `len`, never
 * trust a declared count, and report errors instead of aborting.
 */
#include "rt.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const char *const ch_op_names[OP__COUNT] = {
    "start",        "exit",        "create",       "join",          "mutex_lock",
    "mutex_trylock", "mutex_unlock", "cond_wait",   "cond_wake",     "cond_signal",
    "cond_broadcast", "sem_wait",   "sem_trywait",  "sem_post",      "barrier_wait",
    "barrier_leave", "once",       "read",         "write",         "atomic_load",
    "atomic_store", "atomic_rmw",  "yield"};

int ch_op_from_name(const char *s) {
  for (int i = 0; i < OP__COUNT; i++)
    if (strcmp(ch_op_names[i], s) == 0) return i;
  return -1;
}

#define CH_MAX_SCHED_STEPS 2000000u
#define CH_MAX_LINE 400

static void set_err(char *err, size_t errsz, const char *msg, unsigned long a, const char *s) {
  if (err && errsz)
    snprintf(err, errsz, "%s%s%s (%lu)", msg, (s && *s) ? ": " : "", s ? s : "", a);
}

/* Parse unsigned integer token [base]; token must be fully consumed. */
static int parse_u64(const char *tok, int base, uint64_t *out) {
  if (!tok || !*tok) return -1;
  if (tok[0] == '-' || tok[0] == '+' || tok[0] == ' ') return -1;
  errno = 0;
  char *end = NULL;
  unsigned long long v = strtoull(tok, &end, base);
  if (errno != 0 || end == tok || *end != '\0') return -1;
  *out = (uint64_t)v;
  return 0;
}

static int parse_i64(const char *tok, int64_t *out) {
  if (!tok || !*tok) return -1;
  errno = 0;
  char *end = NULL;
  long long v = strtoll(tok, &end, 10);
  if (errno != 0 || end == tok || *end != '\0') return -1;
  *out = (int64_t)v;
  return 0;
}

/* Split `line` (modified) into up to `max` whitespace separated tokens. */
static int tokenize(char *line, char **tok, int max) {
  int n = 0;
  char *p = line;
  while (*p) {
    while (*p == ' ' || *p == '\t' || *p == '\r') p++;
    if (!*p) break;
    if (n == max) return -1;
    tok[n++] = p;
    while (*p && *p != ' ' && *p != '\t' && *p != '\r') p++;
    if (*p) *p++ = '\0';
  }
  return n;
}

/* Iterate lines of a bounded buffer. Returns 0 at end, 1 line ok, -1 too long
 * or contains NUL. */
static int next_line(const char *text, size_t len, size_t *pos, char *buf) {
  if (*pos >= len) return 0;
  size_t i = 0;
  while (*pos < len && text[*pos] != '\n') {
    char c = text[(*pos)++];
    if (c == '\0') return -1;
    if (i >= CH_MAX_LINE - 1) return -1;
    buf[i++] = c;
  }
  if (*pos < len) (*pos)++; /* skip '\n' */
  buf[i] = '\0';
  return 1;
}

int ch_sched_parse(const char *text, size_t len, ch_sched *out, char *err, size_t errsz) {
  memset(out, 0, sizeof *out);
  char line[CH_MAX_LINE];
  char *tok[8];
  size_t pos = 0;
  int rc = next_line(text, len, &pos, line);
  if (rc <= 0) { set_err(err, errsz, "empty or invalid schedule file", 0, ""); return -1; }
  int nt = tokenize(line, tok, 8);
  uint64_t ver = 0;
  if (nt != 2 || strcmp(tok[0], "chronos-schedule") != 0 || parse_u64(tok[1], 10, &ver) != 0 ||
      ver != CH_SCHEMA_VERSION) {
    set_err(err, errsz, "bad schedule header", 0, "");
    return -2;
  }
  uint64_t declared = 0;
  int have_steps = 0;
  /* header lines until "steps N" */
  while (!have_steps) {
    rc = next_line(text, len, &pos, line);
    if (rc <= 0) { set_err(err, errsz, "missing steps line", 0, ""); return -3; }
    nt = tokenize(line, tok, 8);
    if (nt < 1) continue;
    if (strcmp(tok[0], "program") == 0) {
      if (nt >= 2) { strncpy(out->program, tok[1], sizeof out->program - 1); }
    } else if (strcmp(tok[0], "fail") == 0) {
      if (nt >= 2) { strncpy(out->fail, tok[1], sizeof out->fail - 1); }
    } else if (strcmp(tok[0], "steps") == 0) {
      if (nt != 2 || parse_u64(tok[1], 10, &declared) != 0 || declared > CH_MAX_SCHED_STEPS) {
        set_err(err, errsz, "bad steps count", 0, "");
        return -4;
      }
      have_steps = 1;
    } else if (tok[0][0] == '#') {
      continue;
    } else {
      set_err(err, errsz, "unexpected header line", 0, tok[0]);
      return -5;
    }
  }
  if (declared > 0) {
    out->recs = (ch_sched_rec *)calloc((size_t)declared, sizeof(ch_sched_rec));
    if (!out->recs) { set_err(err, errsz, "out of memory", 0, ""); return -6; }
  }
  for (uint64_t i = 0; i < declared; i++) {
    rc = next_line(text, len, &pos, line);
    if (rc <= 0) {
      set_err(err, errsz, "truncated schedule at step", (unsigned long)i, "");
      ch_sched_free(out);
      return -7;
    }
    nt = tokenize(line, tok, 8);
    uint64_t step, tid, opidx, run;
    int64_t obj;
    if (nt != 6 || parse_u64(tok[0], 10, &step) != 0 || parse_u64(tok[1], 10, &tid) != 0 ||
        parse_u64(tok[2], 10, &opidx) != 0 || parse_i64(tok[4], &obj) != 0 ||
        parse_u64(tok[5], 16, &run) != 0) {
      set_err(err, errsz, "malformed step line", (unsigned long)i, "");
      ch_sched_free(out);
      return -8;
    }
    int op = ch_op_from_name(tok[3]);
    if (step != i || tid >= CH_MAXT || opidx > 0x7fffffffu || op < 0 || obj < -1 || obj > 10000000 ||
        ((run >> tid) & 1u) == 0) {
      set_err(err, errsz, "invalid step values at step", (unsigned long)i, "");
      ch_sched_free(out);
      return -9;
    }
    ch_sched_rec *r = &out->recs[i];
    r->step = (uint32_t)step;
    r->tid = (int)tid;
    r->opidx = (uint32_t)opidx;
    r->op = op;
    r->obj = (int)obj;
    r->run = run;
  }
  out->n = (uint32_t)declared;
  /* trailer: optional "end", nothing else of substance */
  while ((rc = next_line(text, len, &pos, line)) > 0) {
    nt = tokenize(line, tok, 8);
    if (nt == 0 || tok[0][0] == '#') continue;
    if (strcmp(tok[0], "end") == 0 && nt == 1) continue;
    set_err(err, errsz, "trailing garbage after steps", 0, tok[0]);
    ch_sched_free(out);
    return -10;
  }
  if (rc < 0) { set_err(err, errsz, "invalid trailer line", 0, ""); ch_sched_free(out); return -11; }
  return 0;
}

int ch_sched_load(const char *path, ch_sched *out, char *err, size_t errsz) {
  memset(out, 0, sizeof *out);
  FILE *f = fopen(path, "rb");
  if (!f) { set_err(err, errsz, "cannot open schedule file", 0, path); return -100; }
  size_t cap = 1 << 16, n = 0;
  char *buf = (char *)malloc(cap);
  if (!buf) { fclose(f); return -101; }
  for (;;) {
    size_t r = fread(buf + n, 1, cap - n, f);
    n += r;
    if (n < cap) break;
    if (cap > (size_t)(256u << 20)) { free(buf); fclose(f); set_err(err, errsz, "schedule file too large", 0, ""); return -102; }
    cap *= 2;
    char *nb = (char *)realloc(buf, cap);
    if (!nb) { free(buf); fclose(f); return -101; }
    buf = nb;
  }
  fclose(f);
  int rc = ch_sched_parse(buf, n, out, err, errsz);
  free(buf);
  return rc;
}

void ch_sched_free(ch_sched *s) {
  free(s->recs);
  s->recs = NULL;
  s->n = 0;
}

int ch_over_parse(const char *text, size_t len, ch_overrides *out, char *err, size_t errsz) {
  memset(out, 0, sizeof *out);
  char line[CH_MAX_LINE];
  char *tok[8];
  size_t pos = 0;
  int rc = next_line(text, len, &pos, line);
  if (rc <= 0) { set_err(err, errsz, "empty or invalid override file", 0, ""); return -1; }
  int nt = tokenize(line, tok, 8);
  uint64_t ver = 0;
  if (nt != 2 || strcmp(tok[0], "chronos-preempt") != 0 || parse_u64(tok[1], 10, &ver) != 0 ||
      ver != CH_SCHEMA_VERSION) {
    set_err(err, errsz, "bad override header", 0, "");
    return -2;
  }
  uint32_t cap = 0;
  while ((rc = next_line(text, len, &pos, line)) > 0) {
    nt = tokenize(line, tok, 8);
    if (nt == 0 || tok[0][0] == '#') continue;
    if (strcmp(tok[0], "end") == 0 && nt == 1) continue;
    uint64_t p, c, n;
    if (nt != 3 || parse_u64(tok[0], 10, &p) != 0 || parse_u64(tok[1], 10, &c) != 0 ||
        parse_u64(tok[2], 10, &n) != 0 || p >= CH_MAXT || n >= CH_MAXT || c > 0x7fffffffu) {
      set_err(err, errsz, "malformed override line", 0, "");
      ch_over_free(out);
      return -3;
    }
    if (out->n >= CH_MAX_SCHED_STEPS) { ch_over_free(out); set_err(err, errsz, "too many overrides", 0, ""); return -4; }
    if (out->n == cap) {
      cap = cap ? cap * 2 : 16;
      ch_override *nv = (ch_override *)realloc(out->v, cap * sizeof *nv);
      if (!nv) { ch_over_free(out); return -5; }
      out->v = nv;
    }
    out->v[out->n].prev = (int)p;
    out->v[out->n].cnt = (int)c;
    out->v[out->n].next = (int)n;
    out->n++;
  }
  if (rc < 0) { ch_over_free(out); set_err(err, errsz, "invalid override line", 0, ""); return -6; }
  return 0;
}

int ch_over_load(const char *path, ch_overrides *out, char *err, size_t errsz) {
  memset(out, 0, sizeof *out);
  FILE *f = fopen(path, "rb");
  if (!f) { set_err(err, errsz, "cannot open override file", 0, path); return -100; }
  size_t cap = 1 << 14, n = 0;
  char *buf = (char *)malloc(cap);
  if (!buf) { fclose(f); return -101; }
  for (;;) {
    size_t r = fread(buf + n, 1, cap - n, f);
    n += r;
    if (n < cap) break;
    if (cap > (size_t)(64u << 20)) { free(buf); fclose(f); return -102; }
    cap *= 2;
    char *nb = (char *)realloc(buf, cap);
    if (!nb) { free(buf); fclose(f); return -101; }
    buf = nb;
  }
  fclose(f);
  int rc = ch_over_parse(buf, n, out, err, errsz);
  free(buf);
  return rc;
}

void ch_over_free(ch_overrides *o) {
  free(o->v);
  o->v = NULL;
  o->n = 0;
}

/* prefix file: "chronos-prefix 1" followed by whitespace separated thread ids */
int ch_prefix_parse(const char *text, size_t len, ch_prefix *out, char *err, size_t errsz) {
  memset(out, 0, sizeof *out);
  char line[CH_MAX_LINE];
  char *tok[64];
  size_t pos = 0;
  int rc = next_line(text, len, &pos, line);
  if (rc <= 0) { set_err(err, errsz, "empty or invalid prefix file", 0, ""); return -1; }
  int nt = tokenize(line, tok, 8);
  uint64_t ver = 0;
  if (nt != 2 || strcmp(tok[0], "chronos-prefix") != 0 || parse_u64(tok[1], 10, &ver) != 0 || ver != CH_SCHEMA_VERSION) {
    set_err(err, errsz, "bad prefix header", 0, "");
    return -2;
  }
  uint32_t cap = 0;
  while ((rc = next_line(text, len, &pos, line)) > 0) {
    char *p = line;
    for (;;) {
      while (*p == ' ' || *p == '\t' || *p == '\r') p++;
      if (!*p) break;
      char *start = p;
      while (*p && *p != ' ' && *p != '\t' && *p != '\r') p++;
      if (*p) *p++ = '\0';
      if (strcmp(start, "end") == 0) continue;
      uint64_t v;
      if (parse_u64(start, 10, &v) != 0 || v >= CH_MAXT) {
        set_err(err, errsz, "invalid thread id in prefix", 0, start);
        ch_prefix_free(out);
        return -3;
      }
      if (out->n >= CH_MAX_SCHED_STEPS) { ch_prefix_free(out); set_err(err, errsz, "prefix too long", 0, ""); return -4; }
      if (out->n == cap) {
        cap = cap ? cap * 2 : 64;
        uint8_t *nv = (uint8_t *)realloc(out->v, cap);
        if (!nv) { ch_prefix_free(out); return -5; }
        out->v = nv;
      }
      out->v[out->n++] = (uint8_t)v;
    }
  }
  if (rc < 0) { ch_prefix_free(out); set_err(err, errsz, "invalid prefix line", 0, ""); return -6; }
  return 0;
}

int ch_prefix_load(const char *path, ch_prefix *out, char *err, size_t errsz) {
  memset(out, 0, sizeof *out);
  FILE *f = fopen(path, "rb");
  if (!f) { set_err(err, errsz, "cannot open prefix file", 0, path); return -100; }
  size_t cap = 1 << 14, n = 0;
  char *buf = (char *)malloc(cap);
  if (!buf) { fclose(f); return -101; }
  for (;;) {
    size_t r = fread(buf + n, 1, cap - n, f);
    n += r;
    if (n < cap) break;
    if (cap > (size_t)(64u << 20)) { free(buf); fclose(f); return -102; }
    cap *= 2;
    char *nb = (char *)realloc(buf, cap);
    if (!nb) { free(buf); fclose(f); return -101; }
    buf = nb;
  }
  fclose(f);
  int rc = ch_prefix_parse(buf, n, out, err, errsz);
  free(buf);
  return rc;
}

void ch_prefix_free(ch_prefix *p) {
  free(p->v);
  p->v = NULL;
  p->n = 0;
}

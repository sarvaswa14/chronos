// Headless DOM check of a generated visualiser page.
// usage: node viz_dom_check.js page.html '{"steps":[44,44],"tabs":2,"signature":"ASSERT@x.c:1"}'
const fs = require('fs'), path = require('path');
function findJsdom() {
  const c = [process.env.JSDOM_PATH, path.join(__dirname, '..', 'tools', 'node_modules', 'jsdom'), '/tmp/vz/node_modules/jsdom'].filter(Boolean);
  for (const p of c) { try { return require(p); } catch (e) {} }
  throw new Error('jsdom not found (npm install jsdom --prefix tools)');
}
const { JSDOM } = findJsdom();
const html = fs.readFileSync(process.argv[2], 'utf8');
const exp = JSON.parse(process.argv[3] || '{}');
const errors = [], checks = {};
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(w) { w.addEventListener('error', e => errors.push(String(e.error || e.message))); } });
const d = dom.window.document, ev = n => new dom.window.Event(n);
const data = JSON.parse(d.getElementById('data').textContent);
checks.no_script_errors = errors.length === 0;
checks.trace_count = data.traces.length === (exp.steps || []).length;
for (let t = 0; t < data.traces.length; t++) {
  if (t > 0) d.querySelectorAll('.tab')[t].dispatchEvent(ev('click'));
  const n = data.traces[t].events.length;
  checks['trace' + t + '_glyphs_equal_steps'] = d.querySelectorAll('#gEv g').length === n && n === exp.steps[t];
  checks['trace' + t + '_minimap_equal_steps'] = d.querySelectorAll('#minimap i').length === n;
  checks['trace' + t + '_thread_rows'] = d.querySelectorAll('#threads tr').length === data.traces[t].header.threads + 1;
  d.querySelector('#gEv g[data-i="' + (n - 1) + '"]').dispatchEvent(ev('click'));
  checks['trace' + t + '_inspector_follows_click'] = d.getElementById('detail').textContent.includes((n - 1) + ' of ' + (n - 1));
  const chips = Array.from(d.querySelectorAll('.chip')).map(c => c.textContent).join(' | ');
  checks['trace' + t + '_verdict_chip'] = exp.signature ? chips.includes(exp.signature) : chips.includes('OK');
  checks['trace' + t + '_findings_listed'] = exp.signature ? d.querySelectorAll('.find').length >= 1 : true;
}
if (exp.tabs) checks.tab_buttons = d.querySelectorAll('.tab').length === exp.tabs;
const ok = Object.values(checks).every(Boolean);
console.log(JSON.stringify({ ok, checks, errors }));
process.exit(ok ? 0 : 1);

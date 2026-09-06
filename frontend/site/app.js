// OPENMAGI landing: language, appear-on-scroll, scroll-driven terminal,
// copy buttons and the offline policy probe. Strings and rules come from
// data.js, links and flags from config.js. No framework, no build step.

import { DICT, RULES, LINES, METRICS, HARNESSES, KANJI, RUKEY } from './data.js';
import { CONFIG } from './config.js';

const GROUPS = [['destructive', 'gDestructive'], ['exfiltration', 'gExfil'], ['scope', 'gScope'], ['irreversible', 'gIrrev']];
const EXAMPLES = ['rm -rf ./build', 'cat .env', 'git push --force'];
const LANG_KEY = 'mg-lang';

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = { lang: readLang(), probe: '' };

function readLang() {
  try { const saved = localStorage.getItem(LANG_KEY); if (saved === 'en' || saved === 'ru') return saved; } catch (e) { /* storage unavailable */ }
  return 'ru';
}

function t() { return DICT[state.lang]; }

// --- language -------------------------------------------------------------

function applyLang(lang) {
  state.lang = lang;
  try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* storage unavailable */ }
  const html = document.documentElement;
  html.lang = lang; html.dataset.lang = lang;
  document.title = CONFIG.title[lang];
  $('meta[name="description"]').content = CONFIG.description[lang];
  const dict = t();
  $$('[data-i18n]').forEach(el => { el.textContent = dict[el.dataset.i18n]; });
  $$('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', dict[el.dataset.i18nAria]); });
  $$('[data-i18n-placeholder]').forEach(el => { el.placeholder = dict[el.dataset.i18nPlaceholder]; });
  renderTerminal();
  renderMetrics();
  renderRules();
  renderProbe();
  renderHarnesses();
  renderStrip();
}

// --- static links from config -------------------------------------------

function applyLinks() {
  const urls = { github: CONFIG.githubUrl, benchmark: CONFIG.benchmarkUrl };
  $$('[data-link]').forEach(a => { a.href = urls[a.dataset.link]; });
  $$('[data-cmd]').forEach(el => { el.textContent = CONFIG.installCommand; });
  $$('[data-copy]').forEach(button => { button.hidden = !CONFIG.installCommand; });
  $('#metrics').hidden = !CONFIG.showMetrics;
}

// --- copy buttons ---------------------------------------------------------

function initCopy() {
  const live = $('#live');
  $$('[data-copy]').forEach(button => {
    let timer = 0;
    const done = () => {
      button.classList.add('is-copied'); live.textContent = t().copied;
      clearTimeout(timer);
      timer = setTimeout(() => { button.classList.remove('is-copied'); live.textContent = ''; }, 2000);
    };
    const fallback = () => {
      const code = $('[data-cmd]', button);
      try {
        const range = document.createRange(); range.selectNodeContents(code);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        if (document.execCommand('copy')) { selection.removeAllRanges(); done(); }
      } catch (e) { /* selection stays for manual copy */ }
    };
    button.addEventListener('click', () => {
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(CONFIG.installCommand).then(done, fallback);
      else fallback();
    });
  });
}

// --- appear on scroll and header state -----------------------------------

function initAppear() {
  const targets = $$('[data-appear]');
  const show = el => el.classList.add('is-visible');
  if (reduced || !('IntersectionObserver' in window)) { targets.forEach(show); return; }
  const io = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) { show(entry.target); io.unobserve(entry.target); }
  }), { threshold: 0.15 });
  targets.forEach(el => io.observe(el));
}

function initHeader() {
  const header = $('#header');
  const io = new IntersectionObserver(entries => header.classList.toggle('is-scrolled', !entries[0].isIntersecting), { rootMargin: '-8px 0px 0px 0px' });
  io.observe($('#top-sentinel'));
}

// --- scroll-driven terminal ----------------------------------------------

let shownLines = reduced ? LINES.length : 0;

function lineHtml(line) {
  switch (line.k) {
    case 'prompt': return `<div class="line line--prompt"><span class="line__bullet">&gt;</span> ${esc(line.text)}</div>`;
    case 'step': return `<div class="line line--step"><span class="line__bullet">●</span> ${esc(line.text)}</div>`;
    case 'done': return `<div class="line line--done"><span class="line__bullet">●</span> ${esc(line.text)}</div>`;
    case 'allow': return `<div class="line line--allow"><span class="line__allow"><span class="kanji">${KANJI.allow}</span> allow</span><span class="line__ms">${esc(line.ms)}</span></div>`;
    case 'gate': return `<div class="line"><div class="gate"><span class="kanji gate__kanji">${KANJI.deny}</span><div class="gate__body"><span class="gate__name">${esc(t().denyRu)}</span><span class="gate__meta">deny · recursive delete outside the declared work scope</span><span class="gate__meta">suggest: move to .trash/ and let the user confirm</span></div></div></div>`;
    default: return '';
  }
}

function renderTerminal() {
  $('#log').innerHTML = LINES.map(lineHtml).join('');
  applyShownLines();
}

function applyShownLines() {
  const log = $('#log');
  $$('.line', log).forEach((el, i) => el.classList.toggle('is-shown', i < shownLines));
  requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
}

function initTerminal() {
  if (reduced) return;
  const wrap = $('#term');
  let raf = 0;
  const compute = () => {
    const rect = wrap.getBoundingClientRect();
    const total = Math.max(1, rect.height - innerHeight);
    const progress = Math.min(1, Math.max(0, -rect.top / total));
    const count = LINES.filter(line => progress >= line.th).length;
    if (count !== shownLines) { shownLines = count; applyShownLines(); }
  };
  const loop = () => { compute(); raf = requestAnimationFrame(loop); };
  const io = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) { if (!raf) loop(); }
    else { cancelAnimationFrame(raf); raf = 0; compute(); }
  });
  io.observe(wrap);
}

// --- metrics --------------------------------------------------------------

let metricProgress = 1;

function renderMetrics() {
  const dict = t();
  $('#metrics-grid').innerHTML = METRICS.map(m => {
    const value = CONFIG.metricValues[m.key];
    const display = value == null ? '——' : m.fmt(value * metricProgress);
    return `<div class="metric"><div class="metric__key">${m.key}</div><div class="metric__value">${display}</div><div class="metric__label">${esc(dict[m.l])}</div></div>`;
  }).join('');
}

function initMetricCountUp() {
  const hasValues = METRICS.some(m => CONFIG.metricValues[m.key] != null);
  if (!hasValues || reduced || !CONFIG.showMetrics) return;
  metricProgress = 0; renderMetrics();
  const io = new IntersectionObserver(entries => {
    if (!entries[0].isIntersecting) return;
    io.disconnect();
    const start = performance.now();
    const step = () => {
      const x = Math.min(1, (performance.now() - start) / 600);
      metricProgress = 1 - Math.pow(1 - x, 3); renderMetrics();
      if (x < 1) requestAnimationFrame(step);
    };
    step();
  }, { threshold: 0.3 });
  io.observe($('#metrics'));
}

// --- policy rules and the offline probe ----------------------------------

function renderRules() {
  const dict = t();
  $('#rules').innerHTML = GROUPS.map(([group, key]) => {
    const cards = RULES.filter(r => r.group === group).map(r =>
      `<button type="button" class="rule" data-example="${escAttr(r.example)}"><code class="rule__label">${esc(r.label)}</code><span class="rule__chip v-${r.decision}"><span class="kanji">${KANJI[r.decision]}</span> ${r.decision}</span></button>`
    ).join('');
    return `<div class="rule-group"><div class="rule-group__title">${esc(dict[key])}</div>${cards}</div>`;
  }).join('');
}

function normalize(input) {
  return input.replace(/\s+/g, ' ').trim().replace(/^\$\s*/, '').replace(/^sudo\s+(-\S+\s+)*/, '');
}

function evaluate(input) {
  const command = normalize(input);
  if (!command) return null;
  for (const rule of RULES) if (rule.re.test(command)) return rule;
  return 'unknown';
}

function examplesHtml(dict, muted) {
  const chips = EXAMPLES.map(cmd => `<button type="button" class="chip" data-example="${escAttr(cmd)}">${esc(cmd)}</button>`).join('');
  return `<div class="probe__examples"><span class="probe__try">${esc(dict.tryThese)}:</span>${chips}</div>`;
}

function renderProbe() {
  const dict = t();
  const verdict = evaluate(state.probe);
  let html;
  if (verdict === null) {
    html = `<div class="probe__hint"><span>${esc(dict.idle)}</span>${examplesHtml(dict)}</div>`;
  } else if (verdict === 'unknown') {
    html = `<div class="probe__hint probe__hint--unknown"><span>${esc(dict.unknown)}</span>${examplesHtml(dict)}</div>`;
  } else {
    const suggest = verdict.suggest ? `<span class="verdict__key">suggest:</span><span class="verdict__val">${esc(verdict.suggest)}</span>` : '';
    html = `<div class="verdict verdict--${verdict.decision}">
      <div class="plaque"><span class="kanji plaque__kanji">${KANJI[verdict.decision]}</span><span class="plaque__name">${esc(dict[RUKEY[verdict.decision]])}</span><span class="plaque__decision">${verdict.decision}</span></div>
      <div class="verdict__grid"><span class="verdict__key">reason:</span><span class="verdict__val">${esc(verdict.reason)}</span>${suggest}</div>
    </div>`;
  }
  $('#probe-state').innerHTML = html;
}

function initProbe() {
  const input = $('#probe');
  input.addEventListener('input', () => { state.probe = input.value; renderProbe(); });
  document.addEventListener('click', event => {
    const source = event.target.closest('[data-example]');
    if (!source) return;
    state.probe = source.dataset.example; input.value = state.probe; renderProbe(); input.focus();
  });
}

// --- harnesses ------------------------------------------------------------

function renderHarnesses() {
  $('#harnesses-grid').innerHTML = HARNESSES.map(h =>
    `<a class="harness" href="${escAttr(h.url)}" target="_blank" rel="noopener"><span class="harness__logo" style="--logo:url('${escAttr(h.logo)}')"></span><span class="harness__name">${esc(h.name)}</span></a>`
  ).join('');
}

// Logos are inlined as CSS masks so they take the text color; the row is
// duplicated so the marquee loops without a visible seam.
function renderStrip() {
  const items = HARNESSES.map(h =>
    `<a class="strip__item" href="${escAttr(h.url)}" target="_blank" rel="noopener"><span class="strip__logo" style="--logo:url('${escAttr(h.logo)}')"></span><span class="strip__name">${esc(h.name)}</span></a>`
  ).join('');
  $('#strip-track').innerHTML = `<div class="strip__row">${items}</div><div class="strip__row" aria-hidden="true">${items}</div>`;
}

// --- helpers --------------------------------------------------------------

function esc(value) {
  return String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escAttr(value) { return esc(value); }

// --- boot -----------------------------------------------------------------

applyLinks();
applyLang(state.lang);
$('#lang-toggle').addEventListener('click', () => applyLang(state.lang === 'ru' ? 'en' : 'ru'));
initCopy();
initAppear();
initHeader();
initTerminal();
initMetricCountUp();
initProbe();

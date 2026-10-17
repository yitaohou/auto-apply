// driver.js — execution. Takes fields (from walker) + resolved decisions and operates
// one handler per control.kind, each with a readback. Runs inside `ego-browser nodejs`;
// `js`, `cdp`, `click`, `typeText`, `pressKey`, `uploadFile`, `wait` are globals.
//
// Every handler returns { ok, actual, method, error? }. A failed field is retried once
// with its alternate method, then handed back (results.json) — never a third time.

import { walk } from './walker.js';

const B = String.raw`
  const R = window.__aa || {};
  const get = (id) => (R[id] && R[id].el && R[id].el.isConnected) ? R[id] : null;
  const txt = (el) => (el ? (el.innerText != null ? el.innerText : el.textContent) || '' : '').replace(/\s+/g, ' ').trim();
  const norm = (s) => String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const visible = (el) => { if (!el || !el.isConnected) return false; const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const setNative = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, 'value'); if (d && d.set) d.set.call(el, v); else el.value = v;
  };
  const fire = (el, types) => types.forEach(t => el.dispatchEvent(new Event(t, { bubbles: true, cancelable: true })));
  const center = (el) => { el.scrollIntoView({ block: 'center', inline: 'nearest' }); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; };
  const matchIdx = (opts, v) => {
    if (typeof v === 'string' && v[0] === '~') { // "~regex": decide.py defers the choice to the live option list (EEO decline)
      let re; try { re = new RegExp(v.slice(1), 'i'); } catch (e) { return -1; }
      return opts.findIndex(o => re.test(o));
    }
    const n = norm(v); if (!n) return -1;
    const ns = opts.map(norm);
    let i = ns.indexOf(n); if (i >= 0) return i;
    i = ns.findIndex(o => o.startsWith(n)); if (i >= 0) return i;
    i = ns.findIndex(o => o.includes(n)); if (i >= 0) return i;
    i = ns.findIndex(o => o && n.includes(o) && o.length >= 3); if (i >= 0) return i;
    // token-prefix overlap ("master of engineering" ↔ "master's degree"): best option with ≥ half its tokens matched
    const vt = n.split(' ').filter(t => t.length >= 3); let best = -1, bs = 0;
    ns.forEach((o, j) => { const ot = o.split(' ').filter(t => t.length >= 3); if (!ot.length || !vt.length) return;
      const hit = ot.filter(t => vt.some(v => t.startsWith(v) || v.startsWith(t))).length; const sc = hit / ot.length; if (sc >= 0.5 && sc > bs) { best = j; bs = sc; } });
    return best;
  };
  const pointerSeq = (el) => { const r = el.getBoundingClientRect(); const o = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0, buttons: 1 };
    el.dispatchEvent(new PointerEvent('pointerdown', o)); el.dispatchEvent(new MouseEvent('mousedown', o)); el.dispatchEvent(new PointerEvent('pointerup', o)); el.dispatchEvent(new MouseEvent('mouseup', o)); el.dispatchEvent(new MouseEvent('click', o)); };
  const visibleOptions = (sel) => [...document.querySelectorAll(sel)].filter(visible).map(el => ({ el, text: txt(el) })).filter(o => o.text);
`;

const run = (body, vars = {}) => {
  const decl = Object.entries(vars).map(([k, v]) => `const ${k} = ${JSON.stringify(v).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')};`).join('\n');
  return js(`(() => {\n${B}\n${decl}\n${body}\n})()`);
};

const sameText = (a, b) => {
  const n = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  if (n(a) === n(b)) return true;
  const d = (s) => n(s).replace(/\D/g, '');
  // phone / numeric masks: digits agree
  return d(a) && d(a) === d(b) && d(a).length >= 6;
};
const toBool = (v) => typeof v === 'boolean' ? v : /^(true|yes|y|1|on|checked)$/i.test(String(v).trim());
const stripBrackets = (s) => String(s).replace(/[<>\[\]{}]/g, '');
/** True per-character keystrokes (keydown/keyup with text) — typeText is an insertText and fires no keydown. */
async function typeKeys(str) {
  for (const ch of String(str)) {
    const code = /\d/.test(ch) ? 'Digit' + ch : /[a-z]/i.test(ch) ? 'Key' + ch.toUpperCase() : '';
    await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: ch, code, text: ch });
    await cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: ch, code });
  }
}

// ---------- text ----------
async function textSetter(id, value) {
  const r = await run(`
    const n = get(ID); if (!n) return { error: 'stale' };
    const el = n.el; el.scrollIntoView({ block: 'center' }); el.focus();
    setNative(el, V); fire(el, ['input', 'change']); el.blur();
    return { actual: el.value };`, { ID: id, V: value });
  if (r.error) return { ok: false, error: r.error, method: 'setter' };
  return { ok: sameText(r.actual, value), actual: r.actual, method: 'setter' };
}
async function textKeys(id, value, { contenteditable = false } = {}) {
  const c = await run(`
    const n = get(ID); if (!n) return { error: 'stale' };
    const el = n.el; const p = center(el); el.focus();
    if (el.select) { el.select(); } else { const s = getSelection(); const rg = document.createRange(); rg.selectNodeContents(el); s.removeAllRanges(); s.addRange(rg); }
    return p;`, { ID: id });
  if (c.error) return { ok: false, error: c.error, method: 'keys' };
  await click([c.x, c.y]);
  await run(`const n = get(ID); if (n) { const el = n.el; if (el.select) el.select(); else { const s = getSelection(); const rg = document.createRange(); rg.selectNodeContents(el); s.removeAllRanges(); s.addRange(rg); } } return 1;`, { ID: id });
  await pressKey('Backspace');
  if (value) await cdp('Input.insertText', { text: String(value) });
  await wait(0.2);
  const r = await run(`
    const n = get(ID); if (!n) return { error: 'stale' };
    const el = n.el; fire(el, ['change']); el.blur();
    return { actual: CE ? txt(el) : el.value };`, { ID: id, CE: contenteditable });
  if (r.error) return { ok: false, error: r.error, method: 'keys' };
  return { ok: sameText(r.actual, value), actual: r.actual, method: 'keys' };
}
const hText = {
  async input(f, v) { return textSetter(f.id, String(v)); },
  async inputAlt(f, v) { return textKeys(f.id, String(v)); },
  async textarea(f, v) { return textSetter(f.id, stripBrackets(v)); },
  async textareaAlt(f, v) { return textKeys(f.id, stripBrackets(v)); },
  async workdayTextarea(f, v) { return textKeys(f.id, stripBrackets(v)); },
  async workdayTextareaAlt(f, v) { return textSetter(f.id, stripBrackets(v)); },
  async contenteditable(f, v) { return textKeys(f.id, stripBrackets(v), { contenteditable: true }); },
};

// ---------- native select ----------
async function selectNative(f, v) {
  const r = await run(`
    const n = get(ID); if (!n) return { error: 'stale' };
    const el = n.el; const opts = [...el.options];
    const texts = opts.map(o => txt(o) || o.value); const vals = opts.map(o => o.value);
    let i = matchIdx(texts, V); if (i < 0) i = matchIdx(vals, V);
    if (i < 0) return { error: 'no_option', options: texts.slice(0, 60) };
    el.focus(); setNative(el, vals[i]); el.selectedIndex = i; fire(el, ['input', 'change']); el.blur();
    return { actual: el.selectedIndex >= 0 ? (txt(el.options[el.selectedIndex]) || el.value) : '' , picked: texts[i] };`, { ID: f.id, V: v });
  if (r.error) return { ok: false, error: r.error, options: r.options, method: 'select' };
  return { ok: sameText(r.actual, r.picked), actual: r.actual, method: 'select' };
}

// ---------- radio / checkbox ----------
async function radio(f, v, viaCoords = false) {
  const r = await run(`
    const n = get(ID); if (!n || !n.inputs) return { error: 'stale' };
    const inputs = n.inputs.filter(i => i.isConnected);
    const i = matchIdx(OPTS, V); if (i < 0 || !inputs[i]) return { error: 'no_option', options: OPTS };
    const el = inputs[i]; const p = center(el);
    if (!COORDS) { el.click(); if (!el.checked) { const l = el.id ? document.querySelector('label[for="' + el.id + '"]') : el.closest('label'); if (l) l.click(); } }
    return { x: p.x, y: p.y, actual: inputs.findIndex(x => x.checked), picked: i };`, { ID: f.id, OPTS: f.options || [], V: v, COORDS: viaCoords });
  if (r.error) return { ok: false, error: r.error, options: r.options, method: viaCoords ? 'radio-click' : 'radio' };
  if (viaCoords) {
    await click([r.x, r.y]);
    const r2 = await run(`const n = get(ID); return n ? n.inputs.findIndex(x => x.checked) : -1;`, { ID: f.id });
    return { ok: r2 === r.picked, actual: (f.options || [])[r2] || '', method: 'radio-click' };
  }
  return { ok: r.actual === r.picked, actual: (f.options || [])[r.actual] || '', method: 'radio' };
}
async function checkbox(f, v, viaCoords = false) {
  const want = toBool(v);
  const r = await run(`
    const n = get(ID); if (!n) return { error: 'stale' };
    const el = n.el; const p = center(el);
    if (el.checked !== WANT && !COORDS) { el.click(); if (el.checked !== WANT) { const l = el.id ? document.querySelector('label[for="' + el.id + '"]') : el.closest('label'); if (l) l.click(); } }
    return { x: p.x, y: p.y, actual: el.checked };`, { ID: f.id, WANT: want, COORDS: viaCoords });
  if (r.error) return { ok: false, error: r.error, method: 'checkbox' };
  if (viaCoords && r.actual !== want) {
    await click([r.x, r.y]);
    const a = await run(`const n = get(ID); return n ? n.el.checked : null;`, { ID: f.id });
    return { ok: a === want, actual: a, method: 'checkbox-click' };
  }
  return { ok: r.actual === want, actual: r.actual, method: 'checkbox' };
}
async function checkboxGroup(f, v) {
  const wants = Array.isArray(v) ? v : String(v).split(/\s*[,;|]\s*/).filter(Boolean);
  const r = await run(`
    const n = get(ID); if (!n || !n.inputs) return { error: 'stale' };
    const inputs = n.inputs; const idxs = WANTS.map(w => matchIdx(OPTS, w)).filter(i => i >= 0);
    const missing = WANTS.filter(w => matchIdx(OPTS, w) < 0);
    inputs.forEach((el, i) => { const want = idxs.includes(i); if (el.checked !== want) el.click(); });
    return { actual: inputs.map((el, i) => el.checked ? OPTS[i] : null).filter(Boolean), missing };`, { ID: f.id, OPTS: f.options || [], WANTS: wants });
  if (r.error) return { ok: false, error: r.error, method: 'checkbox-group' };
  const ok = r.missing.length === 0 && wants.every(w => r.actual.some(a => sameText(a, w) || a.toLowerCase().includes(String(w).toLowerCase())));
  return { ok, actual: r.actual, method: 'checkbox-group', error: r.missing.length ? 'no_option: ' + r.missing.join(', ') : undefined };
}

// ---------- file ----------
async function file(f, path) {
  const base = String(path).split('/').pop();
  let method = 'uploadFile';
  try {
    await uploadFile(`[data-aa-id="${f.id}"]`, path);
  } catch (e) {
    method = 'setFileInputFiles';
    const { result } = await cdp('Runtime.evaluate', { expression: `window.__aa[${JSON.stringify(f.id)}].el`, returnByValue: false });
    if (!result || !result.objectId) return { ok: false, error: 'stale', method };
    await cdp('DOM.setFileInputFiles', { objectId: result.objectId, files: [path] });
  }
  await wait(1.5);
  const r = await run(`
    const n = get(ID); if (!n) return { error: 'stale' };
    const el = n.el; const name = el.files && el.files[0] ? el.files[0].name : '';
    let root = el; for (let i = 0; i < 5 && root.parentElement; i++) root = root.parentElement;
    const t = txt(root);
    return { name, chip: t.includes(BASE), remove: /remove|replace|delete|×/i.test(t) };`, { ID: f.id, BASE: base });
  if (r.error) return { ok: false, error: r.error, method };
  const ok = r.name === base || r.chip;
  return { ok, actual: r.name || (r.chip ? base : ''), method, error: ok ? undefined : 'no filename readback' };
}

// ---------- dates ----------
function parseDate(v) {
  const s = String(v).trim();
  let m;
  if ((m = s.match(/^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$/))) return { y: m[1], mo: m[2].padStart(2, '0'), d: m[3] ? m[3].padStart(2, '0') : '' };
  if ((m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/))) return { y: m[3], mo: m[1].padStart(2, '0'), d: m[2].padStart(2, '0') };
  if ((m = s.match(/^(\d{1,2})\/(\d{4})$/))) return { y: m[2], mo: m[1].padStart(2, '0'), d: '' };
  const dt = new Date(s); if (!isNaN(dt)) return { y: String(dt.getFullYear()), mo: String(dt.getMonth() + 1).padStart(2, '0'), d: String(dt.getDate()).padStart(2, '0') };
  return null;
}
async function dateInput(f, v) {
  const p = parseDate(v); if (!p) return { ok: false, error: 'unparseable date ' + v, method: 'date-input' };
  const val = f.control.inputType === 'month' ? `${p.y}-${p.mo}` : `${p.y}-${p.mo}-${p.d || '01'}`;
  const r = await textSetter(f.id, val); r.method = 'date-input'; return r;
}
async function dateSpin(f, v) {
  const p = parseDate(v); if (!p) return { ok: false, error: 'unparseable date ' + v, method: 'date-spin' };
  const parts = f.control.parts || [];
  const want = { month: p.mo, day: p.d || '01', year: p.y };
  for (let i = 0; i < parts.length; i++) {
    const k = parts[i]; if (!want[k]) continue;
    const c = await run(`const n = get(ID); if (!n || !n.inputs || !n.inputs[I]) return { error: 'stale' }; const el = n.inputs[I]; const p = center(el); el.focus(); return p;`, { ID: f.id, I: i });
    if (c.error) return { ok: false, error: c.error, method: 'date-spin' };
    await click([c.x, c.y]);
    await typeKeys(want[k]);
    await wait(0.2);
  }
  await pressKey('Tab');
  const r = await run(`const n = get(ID); if (!n) return { error: 'stale' }; return { actual: n.inputs.map(p => p.getAttribute('aria-valuetext') || txt(p)) };`, { ID: f.id });
  if (r.error) return { ok: false, error: r.error, method: 'date-spin' };
  const ok = parts.every((k, i) => !want[k] || String(r.actual[i]).replace(/\D/g, '').padStart(k === 'year' ? 4 : 2, '0') === want[k]);
  return { ok, actual: r.actual.join('/'), method: 'date-spin' };
}

// ---------- custom dropdowns ----------
const OPTION_SEL = '[role="option"], [class*="select__option"], [class*="-option"]:not([class*="-options"]), [data-automation-id="promptOption"], li[role="menuitem"]';

async function openAndType(f, v, { enter = false, clear = true } = {}) {
  const c = await run(`const n = get(ID); if (!n) return { error: 'stale' }; const p = center(n.el); return p;`, { ID: f.id });
  if (c.error) return c;
  await click([c.x, c.y]);
  await wait(0.3);
  if (clear) await run(`const n = get(ID); if (n && n.el.select) { n.el.select(); } return 1;`, { ID: f.id });
  if (v) await typeText(String(v));
  if (enter) await pressKey('Enter');
  await wait(enter ? 1.5 : 1.0);
  return {};
}
async function pickOption(v, sel = OPTION_SEL) {
  // returns { picked, x, y, options } — matches among visible options; caller clicks
  return run(`
    const opts = visibleOptions(SEL);
    const texts = opts.map(o => o.text);
    const i = matchIdx(texts, V);
    if (i < 0) return { picked: null, options: texts.slice(0, 40) };
    const p = center(opts[i].el);
    return { picked: texts[i], x: p.x, y: p.y, first: i === 0 };`, { SEL: sel, V: v });
}
async function readSelected(f, { inputValue = false } = {}) {
  return run(`
    const n = get(ID); if (!n) return { error: 'stale' }; const el = n.el;
    if (el.tagName === 'BUTTON') return { actual: txt(el) };
    let root = el.closest('[data-automation-id^="formField"], .field, [class*="field" i], .form-group, li');
    if (!root) { root = el; for (let i = 0; i < 3 && root.parentElement; i++) root = root.parentElement; }
    const pills = [...root.querySelectorAll('[data-automation-id="selectedItem"], [class*="multi-value__label"], [class*="single-value"], [class*="selected-value"]')].filter(visible).map(txt).filter(Boolean);
    if (pills.length) return { actual: pills.join(' | ') };
    if (INPUTVAL && el.value) return { actual: el.value };
    const ad = el.getAttribute('aria-activedescendant'); if (ad) { const o = document.getElementById(ad); if (o) return { actual: txt(o) }; }
    return { actual: '', typed: el.value || '' };`, { ID: f.id, INPUTVAL: inputValue });
}
async function reactSelect(f, v, viaDispatch = false) {
  const o = await openAndType(f, v); if (o.error) return { ok: false, error: o.error, method: 'react-select' };
  const p = await pickOption(v);
  if (!p.picked) { await pressKey('Escape'); return { ok: false, error: 'no_option', options: p.options, method: 'react-select' }; }
  if (viaDispatch) {
    await run(`const opts = visibleOptions(SEL); const i = opts.findIndex(o => o.text === P); if (i >= 0) pointerSeq(opts[i].el); return i;`, { SEL: OPTION_SEL, P: p.picked });
  } else if (p.first) {
    await pressKey('Enter');
  } else {
    await click([p.x, p.y]);
  }
  await wait(0.5);
  const r = await readSelected(f);
  if (r.error) return { ok: false, error: r.error, method: 'react-select' };
  const ok = sameText(r.actual, p.picked) || String(r.actual).toLowerCase().includes(p.picked.toLowerCase());
  return { ok, actual: r.actual, method: viaDispatch ? 'react-select-dispatch' : 'react-select', error: ok ? undefined : 'readback mismatch' };
}
async function workdayPrompt(f, v, viaDispatch = false) {
  const o = await openAndType(f, v, { enter: true }); if (o.error) return { ok: false, error: o.error, method: 'workday-prompt' };
  const p = await pickOption(v, '[data-automation-id="promptOption"], [role="option"]');
  if (!p.picked) { await pressKey('Escape'); return { ok: false, error: 'no_option', options: p.options, method: 'workday-prompt' }; }
  if (viaDispatch) await run(`const opts = visibleOptions(SEL); const i = opts.findIndex(o => o.text === P); if (i >= 0) pointerSeq(opts[i].el); return i;`, { SEL: '[data-automation-id="promptOption"], [role="option"]', P: p.picked });
  else await click([p.x, p.y]);
  await wait(0.8);
  const r = await readSelected(f);
  if (r.error) return { ok: false, error: r.error, method: 'workday-prompt' };
  const ok = String(r.actual).toLowerCase().includes(p.picked.toLowerCase());
  return { ok, actual: r.actual, method: viaDispatch ? 'workday-prompt-dispatch' : 'workday-prompt', error: ok ? undefined : 'readback mismatch' };
}
async function buttonSelect(f, v, viaDispatch = false) {
  const c = await run(`const n = get(ID); if (!n) return { error: 'stale' }; return center(n.el);`, { ID: f.id });
  if (c.error) return { ok: false, error: c.error, method: 'button-select' };
  await click([c.x, c.y]);
  await wait(0.6);
  const p = await pickOption(v, '[role="option"], [role="menuitem"], [data-automation-id="promptOption"], li');
  if (!p.picked) { await pressKey('Escape'); return { ok: false, error: 'no_option', options: p.options, method: 'button-select' }; }
  if (viaDispatch) await run(`const opts = visibleOptions(SEL); const i = opts.findIndex(o => o.text === P); if (i >= 0) pointerSeq(opts[i].el); return i;`, { SEL: '[role="option"], [role="menuitem"], [data-automation-id="promptOption"], li', P: p.picked });
  else await click([p.x, p.y]);
  await wait(0.5);
  const r = await readSelected(f);
  if (r.error) return { ok: false, error: r.error, method: 'button-select' };
  const ok = sameText(r.actual, p.picked) || String(r.actual).toLowerCase().includes(p.picked.toLowerCase());
  return { ok, actual: r.actual, method: viaDispatch ? 'button-select-dispatch' : 'button-select', error: ok ? undefined : 'readback mismatch' };
}
async function combobox(f, v, viaDispatch = false) {
  const o = await openAndType(f, v); if (o.error) return { ok: false, error: o.error, method: 'combobox' };
  const p = await pickOption(v);
  if (p.picked) {
    if (viaDispatch) await run(`const opts = visibleOptions(SEL); const i = opts.findIndex(o => o.text === P); if (i >= 0) pointerSeq(opts[i].el); return i;`, { SEL: OPTION_SEL, P: p.picked });
    else await click([p.x, p.y]);
  } else {
    await pressKey('Enter');
  }
  await wait(0.5);
  const r = await readSelected(f, { inputValue: true });
  if (r.error) return { ok: false, error: r.error, method: 'combobox' };
  const target = p.picked || v;
  const ok = sameText(r.actual, target) || String(r.actual).toLowerCase().includes(String(target).toLowerCase());
  return { ok, actual: r.actual, method: 'combobox', error: ok ? undefined : (p.picked ? 'readback mismatch' : 'no_option'), options: p.options };
}

// ---------- dispatch ----------
const HANDLERS = {
  'input': [hText.input, hText.inputAlt],
  'textarea': [hText.textarea, hText.textareaAlt],
  'workday-textarea': [hText.workdayTextarea, hText.workdayTextareaAlt],
  'contenteditable': [hText.contenteditable],
  'select': [selectNative],
  'radio': [(f, v) => radio(f, v, false), (f, v) => radio(f, v, true)],
  'checkbox': [(f, v) => checkbox(f, v, false), (f, v) => checkbox(f, v, true)],
  'checkbox-group': [checkboxGroup],
  'file': [file],
  'date-input': [dateInput],
  'date-spin': [dateSpin],
  'react-select': [(f, v) => reactSelect(f, v, false), (f, v) => reactSelect(f, v, true)],
  'workday-prompt': [(f, v) => workdayPrompt(f, v, false), (f, v) => workdayPrompt(f, v, true)],
  'workday-button-select': [(f, v) => buttonSelect(f, v, false), (f, v) => buttonSelect(f, v, true)],
  'button-select': [(f, v) => buttonSelect(f, v, false), (f, v) => buttonSelect(f, v, true)],
  'combobox': [(f, v) => combobox(f, v, false), (f, v) => combobox(f, v, true)],
};

const sig = (f) => [f.type, f.control.kind, f.control.name || '', f.label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()].join('|');

/**
 * Re-walk the page and map the ids in `fields` (a previous walk) onto the fresh
 * registry. Returns { fields: freshFields, map: {oldId: newId} }.
 */
export async function refresh(fields) {
  const fresh = await walk();
  const bySig = new Map();
  for (const f of fresh.fields) { const s = sig(f); if (!bySig.has(s)) bySig.set(s, []); bySig.get(s).push(f.id); }
  const map = {};
  for (const f of fields) {
    const cands = bySig.get(sig(f)) || [];
    map[f.id] = cands.includes(f.id) ? f.id : (cands.shift() || null);
  }
  return { page: fresh, map };
}

/**
 * fill(fields, decisions) — decisions: [{id, value}] (resolved values only).
 * Returns results: [{id, label, ok, actual, method, error?, options?}]
 */
export async function fill(fields, decisions, { log = () => {} } = {}) {
  const { page, map } = await refresh(fields);
  const freshById = Object.fromEntries(page.fields.map(f => [f.id, f]));
  const oldById = Object.fromEntries(fields.map(f => [f.id, f]));
  const results = [];
  for (const d of decisions) {
    if (d.value === null || d.value === undefined || d.source === 'ASK_USER' || d.source === 'LLM') continue;
    const old = oldById[d.id];
    const nid = map[d.id];
    const f = nid ? freshById[nid] : null;
    if (!f) { results.push({ id: d.id, label: old ? old.label : '', ok: false, error: 'field not found on page (stale)' }); continue; }
    const chain = HANDLERS[f.control.kind];
    if (!chain) { results.push({ id: d.id, label: f.label, ok: false, error: 'no handler for kind ' + f.control.kind }); continue; }
    let res = null;
    for (let attempt = 0; attempt < chain.length && attempt < 2; attempt++) {
      try { res = await chain[attempt](f, d.value); }
      catch (e) { res = { ok: false, error: String(e && e.message || e), method: 'exception' }; }
      log(`${f.id} ${f.label.slice(0, 40)} → ${res.ok ? 'ok' : 'FAIL'} [${res.method}] ${res.ok ? '' : (res.error || '')}`);
      if (res.ok) break;
      if (res.error === 'no_option') break; // alternate method will not invent an option
      await wait(0.3);
    }
    results.push(Object.assign({ id: d.id, label: f.label, kind: f.control.kind, value: d.value }, res));
    await wait(0.15);
  }
  return results;
}

/** Click a nav control from the latest walk (id like "n1"). */
export async function clickNav(id) {
  const c = await run(`const n = get(ID); if (!n) return { error: 'stale' }; return center(n.el);`, { ID: id });
  if (c.error) throw new Error('nav control ' + id + ' not found');
  await click([c.x, c.y]);
}

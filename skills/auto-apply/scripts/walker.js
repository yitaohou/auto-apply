// walker.js — perception. Reads the current page once and returns fields.json
// (see references/page-loop.md). Runs inside `ego-browser nodejs`; `js` is a global.
//
// The browser-side code is one IIFE (a string — js() has no argument channel).
// It tags every reported element with data-aa-id and keeps a registry at
// window.__aa[id] = { el, inputs } so the driver can reach elements inside shadow
// roots without a snapshot ref. ATS fingerprints decide control.kind (how a control
// is operated) and nothing else.

export const WALK_SRC = String.raw`(() => {
  const HONEYPOT = /beecatcher|hpcsaf|honeypot|^hp_|_hp$|^hp$/i;
  const MAX_OPTIONS = 200;
  const host = location.hostname;
  const ats = (() => {
    if (/myworkdayjobs|workday/.test(host)) return 'workday';
    if (/greenhouse\.io/.test(host)) return 'greenhouse';
    if (/lever\.co/.test(host)) return 'lever';
    if (/ashbyhq\.com/.test(host)) return 'ashby';
    if (/linkedin\.com/.test(host)) return 'linkedin';
    if (/indeed\.com/.test(host)) return 'indeed';
    if (/smartrecruiters\.com/.test(host)) return 'smartrecruiters';
    if (/icims\.com/.test(host)) return 'icims';
    if (/taleo\.net/.test(host)) return 'taleo';
    if (/successfactors|sapsf/.test(host)) return 'successfactors';
    if (/dayforce|ceridian/.test(host)) return 'dayforce';
    if (/adp\.com/.test(host)) return 'adp';
    if (/oraclecloud\.com/.test(host)) return 'oracle';
    if (/amazon\.jobs/.test(host)) return 'amazon';
    if (/bamboohr\.com/.test(host)) return 'bamboohr';
    if (/jobvite\.com/.test(host)) return 'jobvite';
    if (/workable\.com/.test(host)) return 'workable';
    if (host === '' || location.protocol === 'file:') return 'fixture';
    return 'generic';
  })();

  // ---- traversal (pierces open shadow roots) ----
  const all = [];
  const visit = (root) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let n = w.currentNode;
    while (n) {
      if (n.nodeType === 1) {
        all.push(n);
        if (n.shadowRoot) visit(n.shadowRoot);
      }
      n = w.nextNode();
    }
  };
  visit(document);
  for (const e of all) if (e.hasAttribute('data-aa-id')) e.removeAttribute('data-aa-id'); // stale tags from a previous walk

  const txt = (el) => (el ? (el.innerText != null ? el.innerText : el.textContent) || '' : '').replace(/\s+/g, ' ').trim();
  const clip = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : s);
  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    if (r.right + scrollX < 0 || r.bottom + scrollY < 0) return false; // parked off-page (honeypot style)
    // an ancestor with display:none makes getClientRects empty already; aria-hidden ancestors count as hidden
    let p = el.parentElement || (el.getRootNode() instanceof ShadowRoot ? el.getRootNode().host : null);
    while (p) { if (p.getAttribute && p.getAttribute('aria-hidden') === 'true') return false; p = p.parentElement || (p.getRootNode() instanceof ShadowRoot ? p.getRootNode().host : null); }
    return true;
  };
  const byId = (id) => { for (const e of all) if (e.id === id) return e; return null; };
  const closest = (el, sel) => {
    let n = el;
    while (n) { if (n.matches && n.matches(sel)) return n; n = n.parentElement || (n.getRootNode && n.getRootNode() instanceof ShadowRoot ? n.getRootNode().host : null); }
    return null;
  };

  // ---- label resolution ----
  const labelFor = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(id => txt(byId(id))).filter(Boolean).join(' '); if (t) return t; }
    const al = el.getAttribute('aria-label'); if (al && al.trim()) return al.trim();
    if (el.id) { for (const l of all) if (l.tagName === 'LABEL' && l.htmlFor === el.id) { const t = txt(l); if (t) return t; } }
    const anc = closest(el, 'label'); if (anc) { const c = anc.cloneNode(true); c.querySelectorAll('input,select,textarea,button').forEach(x => x.remove()); const t = txt(c); if (t) return t; }
    const fs = closest(el, 'fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) { const t = txt(lg); if (t) return t; } }
    // workday / generic wrappers: nearest ancestor that holds a label-ish element
    let p = el.parentElement; let depth = 0;
    while (p && depth < 4) {
      const cand = p.querySelector('label, legend, [data-automation-id="formLabel"], [class*="label" i], [class*="Label"], [id$="-label"], h3, h4, h5');
      if (cand && cand !== el && !cand.contains(el)) { const t = txt(cand); if (t) return t; }
      p = p.parentElement; depth++;
    }
    // previous sibling text
    let s = el.previousElementSibling;
    while (s) { const t = txt(s); if (t && t.length < 200) return t; s = s.previousElementSibling; }
    const ph = el.getAttribute('placeholder'); if (ph) return ph.trim();
    return el.getAttribute('name') || el.getAttribute('title') || '';
  };
  const helpFor = (el) => {
    const db = el.getAttribute('aria-describedby');
    if (db) { const t = db.split(/\s+/).map(id => txt(byId(id))).filter(Boolean).join(' '); if (t) return clip(t, 300); }
    const wrap = closest(el, '.field, [class*="field" i], [data-automation-id^="formField"], .form-group, li, fieldset');
    if (wrap) { const h = wrap.querySelector('[class*="help" i], [class*="hint" i], [class*="description" i], [class*="instructions" i], small'); if (h && !h.contains(el)) return clip(txt(h), 300); }
    return '';
  };
  const requiredFor = (el, label) => {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    if (/\*\s*$|^\*|\(required\)|\brequired\b/i.test(label)) return true;
    const wrap = closest(el, '[class*="required" i], [data-required="true"], [aria-required="true"]'); if (wrap) return true;
    const lab = el.id ? all.find(l => l.tagName === 'LABEL' && l.htmlFor === el.id) : null;
    if (lab && (lab.querySelector('[class*="required" i], abbr') || /\*/.test(lab.textContent))) return true;
    return false;
  };
  const cleanLabel = (s) => s.replace(/\s*\*\s*$/, '').replace(/^\*\s*/, '').replace(/\s*\(required\)\s*$/i, '').replace(/\s+/g, ' ').trim();

  // ---- collection ----
  const fields = [];
  const registry = {};
  const honeypots = [];
  const seenGroup = {};
  const doneWraps = new Set();
  let idx = 0;
  const nextId = () => 'f' + (++idx);
  const register = (id, el, extra) => { registry[id] = Object.assign({ el }, extra || {}); try { el.setAttribute('data-aa-id', id); } catch (e) {} };

  const isHoneypot = (el) => HONEYPOT.test(el.name || '') || HONEYPOT.test(el.id || '') || (el.tabIndex === -1 && el.getAttribute('autocomplete') === 'off' && !visible(el));

  const push = (f) => { fields.push(f); };

  for (const el of all) {
    const tag = el.tagName;
    if (!/^(INPUT|TEXTAREA|SELECT|BUTTON|DIV|SPAN)$/.test(tag)) continue;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (/^(hidden|submit|button|reset|image)$/.test(type)) continue;
      if (isHoneypot(el)) { honeypots.push(el.name || el.id); continue; }
      if (el.disabled || el.readOnly) continue;
      if (type !== 'file' && tag !== 'SELECT' && !visible(el)) continue;
      // hidden file inputs / overlaid selects count only when their wrapper is on screen (not a collapsed step)
      if ((type === 'file' || tag === 'SELECT') && !visible(el) && !(visible(el.parentElement) || (el.parentElement && visible(el.parentElement.parentElement)))) continue;

      // radio / checkbox groups
      if (type === 'radio' || type === 'checkbox') {
        const key = type + ':' + (el.name || el.id || '');
        const group = el.name ? all.filter(x => x.tagName === 'INPUT' && x.name === el.name && (x.getAttribute('type') || '').toLowerCase() === type && !x.disabled) : [el];
        if (seenGroup[key]) continue;
        seenGroup[key] = true;
        if (!group.some(visible)) continue;
        const optLabel = (inp) => {
          const l = inp.id ? all.find(x => x.tagName === 'LABEL' && x.htmlFor === inp.id) : null;
          if (l) { const c = l.cloneNode(true); c.querySelectorAll('input').forEach(x => x.remove()); const t = txt(c); if (t) return t; }
          const anc = closest(inp, 'label'); if (anc) { const c = anc.cloneNode(true); c.querySelectorAll('input').forEach(x => x.remove()); const t = txt(c); if (t) return t; }
          const al = inp.getAttribute('aria-label'); if (al) return al.trim();
          const sib = inp.nextElementSibling; if (sib) { const t = txt(sib); if (t) return t; }
          return inp.value || '';
        };
        const id = nextId();
        if (type === 'radio' || group.length > 1) {
          const fs = closest(el, 'fieldset, [role="radiogroup"], [role="group"]');
          let label = '';
          if (fs) { const lg = fs.querySelector('legend'); label = lg ? txt(lg) : (fs.getAttribute('aria-label') || ''); if (!label) { const lb = fs.getAttribute('aria-labelledby'); if (lb) label = txt(byId(lb)); } }
          if (!label) label = labelFor(el);
          // if label equals the first option label, look further up
          if (label === optLabel(el)) { const wrap = fs || el.parentElement.parentElement; const cand = wrap && wrap.querySelector('label, legend, [class*="label" i]'); label = cand ? txt(cand) : label; }
          const options = group.map(optLabel).map(s => clip(s, 120));
          const checked = group.map((g, i) => g.checked ? options[i] : null).filter(Boolean);
          register(id, fs || el, { inputs: group });
          group.forEach((g, i) => { try { g.setAttribute('data-aa-id', id + ':' + i); } catch (e) {} });
          push({ id, label: cleanLabel(label), type: type === 'radio' ? 'radio' : 'multiselect', required: requiredFor(el, label) || group.some(g => g.required),
                 control: { kind: type === 'radio' ? 'radio' : 'checkbox-group', tag: 'input', name: el.name || '' },
                 current: type === 'radio' ? (checked[0] || '') : checked, help: helpFor(fs || el), options });
        } else {
          const label = optLabel(el) || labelFor(el);
          register(id, el);
          push({ id, label: cleanLabel(label), type: 'checkbox', required: requiredFor(el, label), control: { kind: 'checkbox', tag: 'input', name: el.name || '' },
                 current: !!el.checked, help: helpFor(el), options: null });
        }
        continue;
      }

      const label = labelFor(el);
      const id = nextId();
      const base = { id, label: cleanLabel(label), required: requiredFor(el, label), help: helpFor(el), options: null };
      const name = el.name || el.id || '';
      if (tag === 'SELECT') {
        const opts = [...el.options].map(o => txt(o) || o.value).slice(0, MAX_OPTIONS);
        register(id, el);
        push(Object.assign(base, { type: el.multiple ? 'multiselect' : 'select', control: { kind: 'select', tag: 'select', name, hidden: !visible(el) },
          current: el.multiple ? [...el.selectedOptions].map(o => txt(o)) : (el.selectedIndex > 0 ? txt(el.options[el.selectedIndex]) : ''), options: opts }));
        continue;
      }
      if (type === 'file') {
        register(id, el);
        // Greenhouse: label[for] is the generic "Attach"; the real label sits in an ancestor wrapper. Fall back to the id (resume / cover_letter).
        const GENERIC = /^(attach|upload|choose|browse|select|enter manually|dropbox|google drive|drag|drop)/i;
        if (GENERIC.test(base.label)) {
          let p = el.parentElement, found = '';
          for (let d = 0; p && d < 6 && !found; d++, p = p.parentElement) {
            for (const c of p.querySelectorAll('label, legend, h2, h3, h4, h5, [class*="label" i]')) {
              const t = txt(c); if (t && t.length < 80 && !c.contains(el) && !GENERIC.test(t) && visible(c)) { found = t; break; }
            }
          }
          base.label = cleanLabel(found || el.id.replace(/[_-]+/g, ' ') || base.label);
          base.required = base.required || requiredFor(el, found);
        }
        let cur = el.files && el.files[0] ? el.files[0].name : '';
        if (!cur) { const wrap = closest(el, '.field, [class*="field" i], [data-automation-id^="formField"], .form-group, li, div'); const m = wrap && txt(wrap).match(/[\w .()\-]+\.(pdf|docx?|rtf|txt)/i); if (m) cur = m[0].trim(); }
        push(Object.assign(base, { type: 'file', control: { kind: 'file', tag: 'input', name, hidden: !visible(el), accept: el.getAttribute('accept') || '' }, current: cur }));
        continue;
      }
      if (tag === 'TEXTAREA') {
        register(id, el);
        push(Object.assign(base, { type: 'textarea', control: { kind: ats === 'workday' ? 'workday-textarea' : 'textarea', tag: 'textarea', name }, current: clip(el.value, 200) }));
        continue;
      }
      if (type === 'date' || type === 'month') {
        register(id, el);
        push(Object.assign(base, { type: 'date', control: { kind: 'date-input', tag: 'input', name, inputType: type }, current: el.value }));
        continue;
      }
      // text-like inputs; detect custom comboboxes by fingerprint
      const role = el.getAttribute('role') || '';
      const auto = el.getAttribute('aria-autocomplete') || '';
      const daid = el.getAttribute('data-automation-id') || '';
      let kind = 'input';
      let current = el.value;
      if (daid === 'searchBox' || closest(el, '[data-automation-id="multiselectInputContainer"], [data-automation-id="selectWidget"], [data-automation-id*="prompt" i]')) {
        kind = 'workday-prompt';
        const wrap = closest(el, '[data-automation-id^="formField"]') || el.parentElement;
        current = wrap ? [...wrap.querySelectorAll('[data-automation-id="selectedItem"]')].map(txt).join(' | ') : '';
      } else if (closest(el, '[class*="select__control"], [class*="select__value-container"], [class*="-control"][class*="css-"]') || /select__input/.test(el.className)) {
        kind = 'react-select';
        const wrap = closest(el, '[class*="select__container"], [class*="-container"]') || el.parentElement.parentElement;
        current = wrap ? [...wrap.querySelectorAll('[class*="single-value"], [class*="multi-value__label"]')].map(txt).join(' | ') : '';
      } else if (role === 'combobox' || auto === 'list' || auto === 'both' || el.getAttribute('aria-haspopup') === 'listbox') {
        kind = 'combobox';
      }
      register(id, el);
      const ftype = (kind === 'input') ? 'text' : 'select';
      push(Object.assign(base, { type: ftype, control: { kind, tag: 'input', name, inputType: type }, current: clip(current || '', 200) }));
      continue;
    }

    // ---- non-input controls ----
    if (tag === 'BUTTON' && el.getAttribute('aria-haspopup') === 'listbox' && visible(el) && !el.disabled) {
      const label = labelFor(el);
      const id = nextId();
      register(id, el);
      const cur = txt(el);
      push({ id, label: cleanLabel(label), type: 'select', required: requiredFor(el, label), control: { kind: el.hasAttribute('data-automation-id') ? 'workday-button-select' : 'button-select', tag: 'button', name: el.getAttribute('data-automation-id') || el.id || '' },
             current: /^(select one|select|choose|--)/i.test(cur) ? '' : cur, help: helpFor(el), options: null });
      continue;
    }
    if ((tag === 'DIV' || tag === 'SPAN') && el.getAttribute('role') === 'spinbutton' && visible(el)) {
      const wrap = closest(el.parentElement, '[data-automation-id="dateInputWrapper"], [data-automation-id*="date" i], [class*="date" i]') || el.parentElement;
      if (doneWraps.has(wrap)) continue;
      doneWraps.add(wrap);
      const parts = [...wrap.querySelectorAll('[role="spinbutton"]')];
      const label = labelFor(wrap) || labelFor(el);
      const id = nextId();
      register(id, wrap, { inputs: parts });
      const partNames = parts.map(p => { const d = p.getAttribute('data-automation-id') || p.getAttribute('aria-label') || ''; return /month/i.test(d) ? 'month' : /day/i.test(d) ? 'day' : /year/i.test(d) ? 'year' : 'part'; });
      push({ id, label: cleanLabel(label), type: 'date', required: requiredFor(wrap, label), control: { kind: 'date-spin', tag: 'div', parts: partNames },
             current: parts.map(p => p.getAttribute('aria-valuetext') || txt(p)).join('/'), help: helpFor(wrap), options: null });
      continue;
    }
    if ((tag === 'DIV') && el.isContentEditable && el.getAttribute('role') !== 'spinbutton' && visible(el) && !closest(el.parentElement, '[contenteditable="true"]')) {
      const label = labelFor(el);
      const id = nextId();
      register(id, el);
      push({ id, label: cleanLabel(label), type: 'textarea', required: requiredFor(el, label), control: { kind: 'contenteditable', tag: 'div', name: el.id || '' }, current: clip(txt(el), 200), help: helpFor(el), options: null });
      continue;
    }
  }

  // ---- navigation ----
  const navRe = {
    submit: /^(submit( application| my application)?|send( application)?|apply( now)?|finish|complete( application)?|submit & finish)$/i,
    next: /^(next|continue|save (and|&) continue|next step|review( application)?|proceed|save & continue)$/i,
    save: /^(save|save for later|save (and|&) exit|save draft)$/i,
  };
  const nav = { next: null, submit: null, save: null };
  let nidx = 0;
  for (const el of all) {
    if (!/^(BUTTON|A|INPUT)$/.test(el.tagName)) continue;
    if (el.tagName === 'INPUT' && !/^(submit|button)$/i.test(el.type || '')) continue;
    if (el.tagName === 'A' && el.getAttribute('role') !== 'button') continue;
    if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const t = (el.tagName === 'INPUT' ? el.value : txt(el) || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 40) continue;
    for (const k of ['submit', 'next', 'save']) {
      if (!nav[k] && navRe[k].test(t)) { const id = 'n' + (++nidx); register(id, el); nav[k] = { id, text: t }; }
    }
  }

  // ---- flags ----
  const bodyText = txt(document.body).slice(0, 20000);
  // captcha = a challenge the user must solve is on screen. Greenhouse ships an invisible reCAPTCHA (256×60 badge
  // bottom-right) on every board — that is captcha_present, not captcha; a 304×78 anchor (the checkbox) or a
  // visible bframe/hcaptcha/turnstile frame is a real challenge.
  const capEls = all.filter(e => (e.tagName === 'IFRAME' && /recaptcha|hcaptcha|turnstile|challenges\.cloudflare/i.test(e.src || '')) || e.classList.contains('g-recaptcha') || e.classList.contains('h-captcha') || e.classList.contains('cf-turnstile') || e.hasAttribute('data-sitekey'));
  const captchaPresent = capEls.length > 0;
  const captcha = capEls.some(e => { if (!visible(e)) return false; const r = e.getBoundingClientRect(); if (e.tagName !== 'IFRAME') return r.height > 0; if (/recaptcha.*anchor/i.test(e.src)) return r.width >= 300; return r.width > 0 && r.height > 0; });
  const loginWall = fields.some(f => f.control.inputType === 'password') || (/sign in to continue|log in to apply|create an account to apply|you need to be logged in|session has expired|please sign in/i.test(bodyText) && fields.length <= 3);
  const closed = /no longer accepting applications|job is no longer available|position has been filled|this job has (closed|expired)|this posting is closed|job posting has expired|application window has closed|no longer available/i.test(bodyText)
    || (ats === 'greenhouse' && /[?&]error=true/.test(location.search)); // closed Greenhouse postings redirect to the board root with ?error=true
  const iframes = all.filter(e => { if (e.tagName !== 'IFRAME' || !e.src) return false; let h = ''; try { h = new URL(e.src, location.href).hostname; } catch (x) { return false; } return /greenhouse|lever|workable|ashby|bamboohr|jobvite|smartrecruiters|icims|myworkday|applytojob|^jobs\./i.test(h); }).map(e => e.src).slice(0, 5);

  // ---- step & errors ----
  let step = '', of = null;
  const pb = all.find(e => e.getAttribute && (e.getAttribute('data-automation-id') === 'progressBar' || e.getAttribute('role') === 'progressbar' || /progress/i.test(e.id || '')));
  const m1 = bodyText.match(/step\s+(\d+)\s+(?:of|\/)\s+(\d+)/i);
  if (m1) { step = m1[1]; of = parseInt(m1[2], 10); }
  if (pb) { const act = pb.querySelector('[data-automation-id="progressBarActiveStep"], [aria-current], .active, [class*="active" i]'); if (act) step = txt(act) || step; const steps = pb.querySelectorAll('li, [data-automation-id^="progressBar"] > div'); if (steps.length && !of) of = steps.length; }
  const errors = [];
  for (const e of all) {
    if (!visible(e)) continue;
    const isErr = e.getAttribute('role') === 'alert' || /(^|\s)(error|invalid|field-error|validation)(\s|$|-)/i.test(e.className || '') || /errorMessage|fieldError/i.test(e.getAttribute('data-automation-id') || '') || (e.getAttribute('aria-live') === 'assertive');
    if (!isErr) continue;
    const t = txt(e); if (!t || t.length > 300 || errors.some(x => x.text === t)) continue;
    if (e.querySelector('[role="alert"], [class*="error" i]') && e.children.length > 1) continue;
    errors.push({ text: t });
  }
  const invalid = fields.filter(f => { const r = registry[f.id]; return r && r.el && r.el.getAttribute && r.el.getAttribute('aria-invalid') === 'true'; }).map(f => f.id);

  window.__aa = registry;
  return {
    ats, page: { url: location.href, title: document.title, step, of, errors: errors.slice(0, 20), invalid },
    fields, nav,
    flags: { honeypot: honeypots, captcha, captcha_present: captchaPresent, login_wall: loginWall, closed, iframes },
  };
})()`;

/** Run the walker on the current tab. Returns the fields object (already a value, not JSON). */
export async function walk() {
  const out = await js(WALK_SRC);
  if (!out || !out.fields) throw new Error('walker returned nothing: ' + JSON.stringify(out).slice(0, 200));
  return out;
}

/** One-line-per-field summary for the CLI. */
export function summarize(f) {
  const lines = [];
  lines.push(`ats=${f.ats} step=${JSON.stringify(f.page.step)}${f.page.of ? '/' + f.page.of : ''} fields=${f.fields.length} nav=${Object.entries(f.nav).filter(([, v]) => v).map(([k, v]) => k + ':' + v.text).join(',') || 'none'} flags=${JSON.stringify(f.flags)}`);
  for (const x of f.fields) {
    const cur = typeof x.current === 'string' ? x.current.slice(0, 40) : JSON.stringify(x.current);
    lines.push(`  ${x.id} ${x.required ? '*' : ' '} [${x.type}/${x.control.kind}] ${x.label.slice(0, 60)}${x.options ? ' (' + x.options.length + ' opts)' : ''}${cur ? ' = ' + cur : ''}`);
  }
  if (f.page.errors.length) lines.push('  errors: ' + f.page.errors.map(e => e.text).join(' | '));
  return lines.join('\n');
}

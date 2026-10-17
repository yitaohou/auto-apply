// formfill.js — entry point for the page loop (references/page-loop.md).
//
// Invoked from an `ego-browser nodejs` heredoc (environment variables do not reach
// the script, so every parameter is passed in the call):
//
//   const m = await import('<plugin>/skills/auto-apply/scripts/formfill.js')
//   await m.run({ mode: 'walk', jobDir: '/Users/me/job-search/data/pages/<job_id>', space: 'aa-run-1' })
//
// modes:
//   walk    → writes <jobDir>/fields.json, prints the field table
//   fill    → reads fields.json + decisions.json, drives the page, writes results.json
//   next    → clicks nav.next from the last walk, waits for the page to change, re-walks
//   submit  → clicks nav.submit, waits ≤25 s for a URL change or confirmation text,
//             writes submit.json with the evidence. Never called by fill.
//
// Tab selection: `tab` (target id from listTabs) > `url` (openOrReuseTab) > current tab.

import fs from 'node:fs';
import path from 'node:path';
import { walk, summarize } from './walker.js';
import { fill, clickNav, refresh } from './driver.js';

const CONFIRM_RE = /thank you for (applying|your application|submitting)|application (has been )?(received|submitted|complete)|we('ve| have) received your application|successfully submitted|your application was sent|application sent|submission (was )?successful/i;

const readJson = (p, fallback = null) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { if (fallback !== null) return fallback; throw new Error(`cannot read ${p}: ${e.message}`); } };
const writeJson = (p, v) => { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, JSON.stringify(v, null, 1)); };

async function selectTab({ space, tab, url }) {
  if (space) await useOrCreateTaskSpace(space);
  if (tab) await switchTab(tab);
  else if (url) await openOrReuseTab(url, { wait: true, timeout: 30 });
  else await ensureRealTab();
  const info = await pageInfo();
  if (info && info.dialog) throw new Error('a native dialog is open: ' + JSON.stringify(info.dialog));
  return info;
}

const pageSig = (f) => JSON.stringify([f.page.url, f.page.step, f.fields.map(x => x.id + x.label).join('|')]);

async function waitForChange(before, { timeout = 15, confirm = false } = {}) {
  const t0 = Date.now();
  let last = null;
  while ((Date.now() - t0) / 1000 < timeout) {
    await wait(1);
    let info; try { info = await pageInfo(); } catch (e) { continue; }
    if (info.dialog) return { changed: true, reason: 'dialog', dialog: info.dialog };
    if (info.url !== before.url) return { changed: true, reason: 'url', url: info.url };
    const probe = await js(`(() => ({ text: (document.body.innerText || '').slice(0, 30000), errors: [...document.querySelectorAll('[role="alert"], [class*="error" i]')].filter(e => e.getClientRects().length).map(e => e.innerText.trim()).filter(Boolean).slice(0, 10) }))()`);
    if (confirm) { const m = probe.text.match(CONFIRM_RE); if (m) return { changed: true, reason: 'text', matched: m[0], url: info.url }; }
    if (probe.errors.length && probe.errors.join() !== (before.errors || []).join()) return { changed: false, reason: 'validation', errors: probe.errors };
    last = probe;
    if (!confirm) {
      const f = await walk();
      if (pageSig(f) !== before.sig) return { changed: true, reason: 'fields', fields: f };
    }
  }
  return { changed: false, reason: 'timeout', errors: last ? last.errors : [] };
}

export async function run(opts) {
  const { mode, jobDir } = opts;
  if (!mode || !jobDir) throw new Error('run({mode, jobDir, space?, tab?, url?}) — mode and jobDir are required');
  const P = (n) => path.join(jobDir, n);
  const info = await selectTab(opts);

  if (mode === 'walk') {
    const f = await walk();
    f.walked_at = new Date().toISOString();
    writeJson(P('fields.json'), f);
    cliLog(summarize(f));
    cliLog(`→ ${P('fields.json')}`);
    return f;
  }

  if (mode === 'fill') {
    const fields = readJson(P('fields.json'));
    const dec = readJson(P('decisions.json'));
    const decisions = (dec.decisions || dec).filter(d => d.value !== null && d.value !== undefined && d.source !== 'ASK_USER' && d.source !== 'LLM');
    const t0 = Date.now();
    const results = await fill(fields.fields, decisions, { log: cliLog });
    const out = { url: info.url, filled_at: new Date().toISOString(), elapsed_ms: Date.now() - t0, ok: results.filter(r => r.ok).length, total: results.length, results };
    writeJson(P('results.json'), out);
    const bad = results.filter(r => !r.ok);
    cliLog(`fill: ${out.ok}/${out.total} ok in ${(out.elapsed_ms / 1000).toFixed(1)} s${bad.length ? '; failed: ' + bad.map(r => `${r.id} ${r.label.slice(0, 30)} (${r.error || 'readback'})`).join(', ') : ''}`);
    cliLog(`→ ${P('results.json')}`);
    return out;
  }

  if (mode === 'next' || mode === 'submit') {
    const prev = readJson(P('fields.json'));
    const { page: fresh, map } = await refresh(prev.fields);
    const navKey = mode === 'next' ? 'next' : 'submit';
    const nav = fresh.nav[navKey];
    if (!nav) {
      if (mode === 'next' && fresh.nav.submit) cliLog(`no "next" control; the page offers "${fresh.nav.submit.text}" — stopping before it (use mode:'submit' deliberately)`);
      writeJson(P('fields.json'), fresh);
      cliLog(`no ${navKey} control found on this page; re-walked → fields.json`);
      return { clicked: false, reason: 'no_nav', fields: fresh };
    }
    const required = fresh.fields.filter(f => f.required && (f.current === '' || f.current === false || (Array.isArray(f.current) && !f.current.length)));
    if (required.length && mode === 'submit') {
      cliLog(`refusing to submit: ${required.length} required field(s) empty: ${required.map(f => f.id + ' ' + f.label.slice(0, 30)).join(', ')}`);
      writeJson(P('fields.json'), fresh);
      return { clicked: false, reason: 'required_empty', required: required.map(f => f.id) };
    }
    const before = { url: fresh.page.url, sig: pageSig(fresh), errors: fresh.page.errors.map(e => e.text) };
    await clickNav(nav.id);
    const res = await waitForChange(before, { timeout: mode === 'submit' ? 25 : 15, confirm: mode === 'submit' });
    if (mode === 'submit') {
      if (res.changed && !res.matched) { // URL/fields changed first — give the confirmation text a moment, then look once more
        await wait(2);
        const t = await js(`(document.body.innerText || '').slice(0, 30000)`);
        const m = t.match(CONFIRM_RE); if (m) res.matched = m[0];
      }
      const after = await pageInfo();
      const evidence = { clicked: nav.text, url_before: before.url, url_after: after.url || null, title_after: after.title || null, reason: res.reason, matched: res.matched || null, errors: res.errors || [], dialog: res.dialog || null, at: new Date().toISOString() };
      writeJson(P('submit.json'), evidence);
      cliLog(`submit clicked ("${nav.text}") → ${res.reason}${res.matched ? ': "' + res.matched + '"' : ''}${res.errors && res.errors.length ? '; errors: ' + res.errors.join(' | ') : ''}`);
      cliLog(`→ ${P('submit.json')}`);
      return evidence;
    }
    const f = res.fields || await walk();
    f.walked_at = new Date().toISOString();
    f.nav_result = { clicked: nav.text, reason: res.reason, errors: res.errors || [] };
    writeJson(P('fields.json'), f);
    cliLog(`next clicked ("${nav.text}") → ${res.reason}${res.errors && res.errors.length ? '; errors: ' + res.errors.join(' | ') : ''}`);
    cliLog(summarize(f));
    cliLog(`→ ${P('fields.json')}`);
    return f;
  }

  throw new Error('unknown mode ' + mode);
}

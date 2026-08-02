# Browser Recipes

Read this before writing any browser script. Recipes here are reusable patterns for driving ATS application forms through ego-browser.

> STATUS: Greenhouse patterns VALIDATED (2026-07-28, one confirmed submission). Workday patterns still speculative — revise after the first Workday run.

The authoritative API reference is the installed `~/.claude/skills/ego-browser/SKILL.md` and its `references/`. Do not invent helper names — when a pattern here disagrees with the installed skill, the installed skill wins. Note: ego-browser does NOT expose Playwright-style `page.getByLabel`/`getByRole` helpers; its surface is `snapshotText()` (semantic tree with `@ref` / `loc=` locators), `fillInput`, `click`, `pressKey`, `uploadFile`, `js()`, and friends.

## Call structure: one Bash call by default

The heredoc is just a container for JavaScript; the Bash call is the execution round. A whole browser task defaults to ONE Bash call — each `await` is an internal operation, not a step boundary. Before starting, encode every predictable observation, action, wait, extraction, verification, and BOUNDED fallback into the script. Use browser results immediately inside the JavaScript and keep adapting in-process until the task completes. Do not exit just to look at intermediate output or plan the next step.

Only three cases justify a new Bash call:
1. User or external control is needed (ladder level 4).
2. A visual check that can't happen in-process (ladder level 3).
3. A process-level failure the script can't recover from (ego lite not running, CLI crashed).

## Heredoc skeleton

```bash
ego-browser nodejs <<'EOF'
// One job per script (principle 6). Reuse the batch's task space by name/id.
const task = await useOrCreateTaskSpace('auto-apply batch')
const tab = await openOrReuseTab('https://job-boards.greenhouse.io/acme/jobs/123', { wait: true, timeout: 30 })

// Observe → act → verify, all in-process.
const snap = await snapshotText()
// ... locate fields in `snap` by their labels, act via @refs / loc= selectors ...

// Always end with a single structured JSON result via cliLog.
cliLog(JSON.stringify({
  job_url: 'https://job-boards.greenhouse.io/acme/jobs/123',
  outcome: 'parked',              // parked | submitted | pending-confirmation | blocked | needs-user
  space_name: 'auto-apply batch',
  space_id: task.id,
  tab_index: tab.targetId ?? null,
  evidence: null,
  blockers: [],
}))
EOF
```

## Six principles, in order of importance

### 1. Semantic locating — never CSS paths or brittle XPath

Locate fields through the semantic tree: run `await snapshotText()` and find controls by their LABEL TEXT and role in the output, then act on the `@ref` or the `loc=` locator it reports. ATS forms must label fields (accessibility law), so "the textbox labeled Phone" holds across dozens of tenants; a CSS path like `div.gwt-Label > input:nth-child(3)` breaks on the next tenant.

This is the entire approach's resistance to change. The most common failure mode is freezing "how to drive this one site" into single-site selectors — such a script shatters on the next employer.

```js
// ❌ dies on the next tenant
await fillInput('div.gwt-Label > input:nth-child(3)', phone)

// ✅ holds across tenants: find the ref by its label in the snapshot, then act
const snap = await snapshotText()
// e.g. snapshot line:  textbox "Phone" [ref=41, loc=css:#phone]
const m = snap.match(/textbox "Phone[^"]*" \[ref=(\d+)/i)
if (!m) throw new Error('Phone field not found in snapshot')
await fillInput('@' + m[1], profile.phone)
```

`@N` refs are only valid against the LATEST `snapshotText()` — re-snapshot after any meaningful DOM change. For long-lived references, prefer the `loc=...` value from the snapshot.

### 2. Every loop has a hard bound

Six address formats means six iterations — never `while (!success)`. Unbounded retries destroy the single-round-trip cost advantage and can trip site rate limits.

### 3. Throws must carry state

The highest-payoff rule. A bare error message teaches the next round nothing; a state-carrying one hands it the full failure map.

```js
// ❌ next round can only guess
throw new Error('address failed')

// ✅ next round knows why each of the six formats failed
throw new Error('Address rejected all formats: ' + JSON.stringify(tried))
```

### 4. Read before write — idempotency

Scripts are NOT transactional. If a script fills 20 fields and throws on the 21st, those 20 are already in the real browser and the page sits half-filled. A retry WILL land on that half-filled page.

So: treat already-satisfied postconditions as completed work. Before operating a control that may already hold the target value, do the minimal read needed to judge; if it matches, do not open its editor or replay the interaction — advance to the remaining goals.

```js
const current = await js(String.raw`document.querySelector('#phone')?.value ?? ''`)
if (current !== expected) {
  await fillInput('@' + phoneRef, expected)
}
```

### 5. Retries reuse the same task space

`useOrCreateTaskSpace` returns `task.id` — keep reusing that id until the goal is finished. Retrying with the same id RESUMES that space and its half-filled page; a new space starts from zero and orphans the old tabs.

### 6. One job per script — never a mega-script for a whole batch

Per-job scripts cap the blast radius of a failure at one job. (During debugging, splitting by Workday PAGE is fine for isolating problems, at the cost of extra rounds; once stable, one script per full application.)

## Recipe: address field (six-format bounded loop + autocomplete iron rule)

```js
const formats = [
  'Toronto, Ontario, Canada',
  'Toronto, ON, Canada',
  'Toronto',
  'Ontario',
  'Canada',
  'CA',
]

// Locate the field ref from the latest snapshot first (see principle 1) → addrRef
let accepted = null
const tried = []

for (const fmt of formats) {                      // bounded: six formats, six tries
  await fillInput('@' + addrRef, '')
  await fillInput('@' + addrRef, fmt)
  await wait(2)                                   // let autocomplete candidates render

  // Iron rule: type → wait for candidates → SELECT a candidate.
  // Never submit raw typed text unless the site accepts free text.
  const snap = await snapshotText()
  const opt = snap.match(/option "[^"]*" \[ref=(\d+)/i)
  if (!opt) { tried.push({ fmt, why: 'no autocomplete candidate' }); continue }
  await click('@' + opt[1], { label: 'select address candidate' })

  // Readback verification
  const value = await js(String.raw`document.activeElement?.value
    ?? document.querySelector('[aria-invalid]')?.value ?? ''`)
  const invalid = await js(String.raw`document.querySelectorAll('[aria-invalid="true"]').length`)
  if (value && invalid === 0) { accepted = fmt; break }
  tried.push({ fmt, why: `readback="${value}" invalid=${invalid}` })
}

if (!accepted) throw new Error('Address rejected all formats: ' + JSON.stringify(tried))
```

Caveat: the `[aria-invalid="true"]` check is WEAK evidence — see `validation-timing` in the playbook. Some sites validate only on submit; a clean readback does not guarantee the field is acceptable. After success, record the accepted format in `automation_rules.csv` (`rule_category = employer`).

## Recipe: resume upload + attachment verification (VALIDATED on Greenhouse)

```js
await uploadFile('input[type="file"]', resumePath)   // absolute path from resume_rules.csv
await wait(3)

// VALIDATED trap: Greenhouse REMOVES the file input from the DOM after a successful
// attach — reading input.files afterwards returns empty and looks like a silent failure.
// The reliable postcondition is the filename chip rendered in the section, with a
// "Remove file" button next to it.
const snap = await snapshotText()
const attached = snap.includes(resumeFileName) && /button "Remove file"/i.test(snap)
if (!attached) {
  throw new Error('Upload unverified: filename chip + Remove-file button not found near Resume/CV section')
}
// Record the ACTUAL attached filename in application_log.resume_used.
```

Beware the input order: on Greenhouse, once the resume attaches, the FIRST remaining `input[type="file"]` belongs to the Cover Letter — a blind second upload call would attach the resume as the cover letter. Verify which section a file input belongs to (`el.id` was `cover_letter` on the validated form) before uploading into it. Other traps: PDF-only sites, hidden inputs in custom widgets (upload to them anyway, then verify visually), wrong variant attached. If verification fails after a level-3 screenshot check → `Blocked` (`upload`). NEVER proceed to submit unverified.

## Recipe: Greenhouse react-select dropdown (VALIDATED)

Greenhouse custom questions render as react-select comboboxes. The combobox `<input>` carries no useful aria-label — find its id through the question label: `label[for]` elements pair question text to input ids like `#question_14576645008`.

```js
// Map question text -> input selector once per page
const qmap = await js(String.raw`(() => {
  const out = {}
  document.querySelectorAll('label[for]').forEach(l => { out[l.textContent.slice(0, 60)] = '#' + l.getAttribute('for') })
  return out
})()`)

async function pickOption(sel, optionText, label) {
  await click(sel, { label: 'open ' + label })
  await wait(1)
  await typeText(optionText)                       // filters the option list
  await wait(1)
  const snap = await snapshotText()
  const m = snap.match(new RegExp('option "' + optionText + '[^"]*" \\[ref=(\\d+)', 'i'))
  if (m) { await click('@' + m[1], { label: 'pick ' + optionText }) } else { await pressKey('Enter') }
  await wait(1)
  // Readback: the committed value renders inside the input's .select__control container
  const shown = await js(String.raw`document.querySelector(${JSON.stringify(sel)})?.closest('.select__control')?.textContent ?? ''`)
  if (!shown.toLowerCase().includes(optionText.toLowerCase())) {
    throw new Error(label + ' readback failed: control shows "' + shown.slice(0, 80) + '"')
  }
}
```

Validated end-to-end on a real Greenhouse form (three dropdowns, zero failures).

## Recipe: custom dropdown — generic / Workday (SPECULATIVE, Workday's number-one killer)

```js
// 1. Close any autofill/overlay first
await pressKey('Escape')

// 2. Open the dropdown and click the REAL option text — never assign to the input
await click('@' + ddRef, { label: 'open dropdown' })
await wait(1)
let snap = await snapshotText()
let opt = snap.match(new RegExp(`option "${optionText}[^"]*" \\[ref=(\\d+)`, 'i'))
if (opt) {
  await click('@' + opt[1], { label: 'pick dropdown option' })
} else {
  // 3. Keyboard navigation fallback (bounded)
  await click('@' + ddRef)
  for (let i = 0; i < 30; i++) {                 // hard bound
    await pressKey('ArrowDown')
    const active = await js(String.raw`document.activeElement
      ?.getAttribute('aria-activedescendant') ?? ''`)
    const label = await js(String.raw`document.getElementById(
      document.activeElement?.getAttribute('aria-activedescendant') || '')?.textContent ?? ''`)
    if (label.trim() === optionText) { await pressKey('Enter'); break }
  }
}

// 4. READBACK — the only thing separating "selected" from "looks selected"
const val = await js(String.raw`/* read the control's committed value / selected option text */`)
const invalid = await js(String.raw`document.querySelectorAll('[aria-invalid="true"]').length`)
if (val !== optionText || invalid > 0) {
  throw new Error('Dropdown readback failed: ' + JSON.stringify({ want: optionText, got: val, invalid }))
}
```

(The keyboard-nav block is illustrative — adapt the readback to the actual widget; Workday commits the value to an adjacent hidden input or button text. Fix this recipe with the real pattern after the first Workday run.) Still failing after DOM + keyboard → ladder level 3 (screenshot + coordinate click). Same field failing repeatedly → record the blocker, move on.

## Recipe: the two endings

### `auto_submit=on` — click and collect evidence

```js
// Register the wait BEFORE clicking: URL change OR confirmation text, first wins.
const before = (await pageInfo()).url
await click('@' + submitRef, { label: 'submit application' })

let evidence = null
for (let i = 0; i < 13; i++) {                    // bounded: ~26s in 2s steps (Workday needs 10-20s)
  await wait(2)
  const info = await pageInfo()
  if (info.url !== before && /thanks|thank-you|submitted|confirmation/i.test(info.url)) {
    evidence = { type: 'url-pattern', confirmation_url: info.url, confirmation_text: '' }
    break
  }
  // SPA case: URL never changes, content swaps in place — check text too.
  const snap = await snapshotText()
  const m = snap.match(/(application (submitted|sent)|thank you for applying)/i)
  if (m) {
    evidence = { type: 'visible-text', confirmation_url: info.url, confirmation_text: m[0] }
    break
  }
}
// evidence !== null → Submitted (fill all three evidence columns), close tab.
// evidence === null → Pending confirmation. Do NOT click submit again (duplicate risk).
cliLog(JSON.stringify({ outcome: evidence ? 'submitted' : 'pending-confirmation', evidence }))
```

### `auto_submit=off` — park in front of submit

```js
// Verify the submit button exists and the form shows no validation errors, then STOP. Never click.
const snap = await snapshotText()
const submitVisible = /button "(submit|submit application)[^"]*"/i.test(snap)
const invalid = await js(String.raw`document.querySelectorAll('[aria-invalid="true"]').length`)
if (!submitVisible || invalid > 0) {
  throw new Error('Not parkable: ' + JSON.stringify({ submitVisible, invalid }))
}
// Keep the tab open. Report space + tab for job_pool.csv (space_name, tab_index, parked_at).
cliLog(JSON.stringify({
  outcome: 'parked', space_name: task.name ?? 'auto-apply batch', space_id: task.id,
  tab_index: (await currentTab()).targetId,
}))
```

Handing a parked batch to the user: `await handOffTaskSpace(task.id)`, then tell the user exactly what to do (click each submit; do NOT close the pages). Resume verification only after the user confirms, starting the next heredoc with `await takeOverTaskSpace(task.id)`.

## Recipe: delegating a capability that uses the browser

The parent agent and a capability provider drive the SAME ego lite instance. A provider may open mailbox tabs while a half-filled application form sits in another tab.

**Delegating a capability that uses the browser.** Before delegating, record the URL
and task space of the in-progress application tab. After the provider returns,
re-verify the application tab is still on the expected URL and that previously
entered field values are intact before entering the code. If form state was lost,
record `Blocked` / `unknown` rather than refilling blindly — the file's idempotency
principle applies.

```js
// BEFORE delegating: snapshot the application tab's identity and key field values.
const before = {
  space_id: task.id,
  url: (await pageInfo()).url,
  fields: await js(String.raw`[...document.querySelectorAll('input,select,textarea')]
    .slice(0, 30).map(el => el.value ?? '').join('')`),
}
cliLog(JSON.stringify({ checkpoint: 'pre-delegation', url: before.url }))
// ...delegate, then in the next round re-open the same task space and compare:
// same URL AND same field readback → proceed to enter the code.
// anything lost → Blocked / unknown. Never refill blindly.
```

## Recipe: Sent-folder tripwire

The parent agent cannot be made unable to send mail — it holds full JS execution because form-filling requires it. Prevention being impossible, detection is mandatory: that is this recipe's entire reason to exist.

At RUN START (only when `email_access = read_only`): open the mailbox's Sent folder, read the identity of the TOPMOST message (sent time + subject), write it to `data/.tripwire.json`, close the tab. Use the top message's identity, not a message count — Gmail's Sent view does not display a reliable count, and the top row is cheap to read.

At RUN END: read the topmost Sent message again and compare. ANY change → a message was sent during the run: abort, record `Blocked` / `email-access`, and alert the user loudly. A tripwire hit means the agent's model of its own behavior is wrong; continuing means acting on a wrong model. A false positive only costs one aborted batch.

```js
const tab = await openOrReuseTab('https://mail.google.com/mail/u/0/#sent', { wait: true, timeout: 30 })
const snap = await snapshotText()
// The first message row in the Sent list carries subject + date text; capture the raw row.
const topRow = (snap.split('\n').find(l => /row|link/i.test(l) && /\d{1,2}:\d{2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec/i.test(l)) ?? '').trim()
cliLog(JSON.stringify({ tripwire_baseline: topRow }))   // parent writes this into data/.tripwire.json
await closeTab(tab.targetId)
```

The stored baseline contains a sent time and subject only — never a verification code, never credentials.

## Verification pass over parked tabs (after the user clicks)

One script, bounded per tab: `takeOverTaskSpace` → `listTabs()` → for each parked tab, `switchTab`, give it the 25-second window (bounded poll as in the `auto_submit=on` ending), classify by the playbook's five-state table, `closeTab` only the `Submitted` ones, and `cliLog` one JSON array with a verdict per tab.

## Prohibited

- No `.js` files — heredocs only.
- No importing Playwright or launching any other browser. ego-browser IS the automation layer.
- No invented helper names — check `~/.claude/skills/ego-browser/SKILL.md` and `help('<name>')` when unsure.
- No screenshots as the default observation method — `snapshotText()` first; screenshots are ladder level 3.
- Nothing but reads against any mailbox domain. Never compose, reply, forward, or send; never delete, archive, or label; never touch mail settings. The only permitted mailbox operations are the Sent-folder tripwire reads above — everything else belongs to a bound provider.

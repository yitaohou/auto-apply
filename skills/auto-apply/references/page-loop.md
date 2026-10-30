# Page loop — walker → decide → driver

**Status: implemented** (`scripts/walker.js`, `scripts/driver.js`, `scripts/formfill.js`, `scripts/decide.py`, `scripts/laya_client.py`). The scripts are the executor; the skill only answers the fields they mark `LLM` and handles `ASK_USER` / failures as blockers. Never fill a page by hand while the scripts can walk it.

## Why

The old loop made the model the executor: per field it re-read a 10–30K-token snapshot, wrote a script, ran it, read back, rewrote. 30–90 s per field, of which the decision took under a second. The new loop runs one perception script, one decision pass, one execution script per **page**. Typical page cost: 2–3 script executions + 0–1 LLM call. Measured on the fixture: 17 fields filled and read back in 13 s.

## Per page

Every job has a page directory `$JOB_DIR = ~/job-search/data/pages/<job_id>/` (job_id = the `job_pool.csv` id or the posting id from the URL). The files there are the audit trail; they stay after the run.

```
formfill.js walk      → $JOB_DIR/fields.json      perception
decide.py decide      → $JOB_DIR/decisions.json   gate → seed rules → cache → local model → LLM markers
                        $JOB_DIR/laya.json        the model's predictions (only when decide_model ≠ llm)
[llm non-empty]       skill writes $JOB_DIR/llm.json, then decide.py merge
formfill.js fill      → $JOB_DIR/results.json     execution + readback
formfill.js next      → fields.json of the next page (or validation errors)   — repeat
formfill.js submit    → $JOB_DIR/submit.json      only on the last page, only per auto_submit
```

Invocation — the scripts run inside `ego-browser nodejs`; environment variables do **not** reach the heredoc, so every parameter is in the call. `PLUGIN` = this skill's directory.

```
ego-browser nodejs <<'EOF'
const m = await import('PLUGIN/scripts/formfill.js');
await m.run({ mode: 'walk', jobDir: '/Users/me/job-search/data/pages/<job_id>', space: 'aa-run-1', url: '<application url>' });
EOF
python3 PLUGIN/scripts/decide.py decide --job-dir ~/job-search/data/pages/<job_id> --company "<company>"
python3 PLUGIN/scripts/decide.py merge  --job-dir ~/job-search/data/pages/<job_id> --llm ~/job-search/data/pages/<job_id>/llm.json
```

`run({mode, jobDir, space?, tab?, url?})`: `tab` (a `listTabs()` target id) wins over `url` (`openOrReuseTab`) wins over the current tab. Several `run` calls may share one heredoc (fill then next). The walk prints a one-line-per-field table — read it, do not re-snapshot the page.

Rules of the loop:

- `decisions.json.ask_user` non-empty → the page stops: `Needs user`, park the tab, dashboard row, next job. `flags: ["ai_prohibited"]` → the posting bars AI-assisted applications → `Skipped`, nothing filled.
- `fields.json.flags.closed` → `Skipped`. `login_wall` → blocker `login-required`. `captcha` (a visible challenge; `captcha_present` alone is Greenhouse's invisible badge and means nothing) → blocker `captcha`, hand off.
- `results.json` with `ok=false`: the driver already tried its two methods; the skill tries **one** manual method from `references/ats-notes.md`, then the field is a blocker (`references/blockers.md`). Never the same failing script three times.
- `next` refuses to click a submit button; `submit` refuses while a required field reads back empty (`reason: required_empty`). `fill` never submits.
- `submit` registers the wait before clicking: URL change, confirmation text (`CONFIRM_RE`), a native dialog, or new validation errors, first wins, 25 s cap. `submit.json` is the evidence for the confirmation rule (`references/blockers.md` §2) — `reason: timeout` with no `matched` text is **not** a submission (`Pending confirmation`).
- Re-walking is cheap. `next`/`submit` re-walk first and remap ids by signature (`type|kind|name|label`), so a stale `fields.json` from before a reflow still works; if the remap drops a field it is reported, not guessed.

## `fields.json`

```json
{"ats":"greenhouse",
 "page":{"url":"…","title":"…","step":"","of":null,"errors":[{"text":"First Name is required"}],"invalid":[]},
 "fields":[{"id":"f4","label":"Country","type":"select","required":true,"help":"","options":null,
            "control":{"kind":"react-select","tag":"input","name":"country"},"current":""},
           {"id":"f7","label":"Resume/CV","type":"file","required":true,
            "control":{"kind":"file","tag":"input","name":"resume","hidden":false,"accept":".pdf,.docx"},"current":""}],
 "nav":{"next":null,"submit":{"id":"n1","text":"Apply"},"save":null},
 "flags":{"honeypot":["beecatcher"],"captcha":false,"captcha_present":true,"login_wall":false,"closed":false,"iframes":[]},
 "walked_at":"2026-10-14T19:07:03Z"}
```

- `type` ∈ `text | textarea | select | multiselect | radio | checkbox | date | file`.
- `control.kind` ∈ `input | textarea | workday-textarea | contenteditable | select | radio | checkbox | checkbox-group | file | date-input | date-spin | react-select | workday-prompt | workday-button-select | button-select | combobox`. The kind is a DOM fingerprint (Workday `data-automation-id`, Greenhouse `select__control`, `aria-haspopup=listbox`…) and decides **how** a control is operated, never what the field means. `ats` comes from the hostname and is only a cache key.
- Native selects, radio groups and checkbox groups list their options; custom listboxes (`options: null`) are opened by the driver at fill time.
- Honeypots (`beecatcher`, `*_hpcsaf`, off-page inputs) are excluded and listed under `flags.honeypot`. Elements inside open shadow roots are walked; cross-origin iframes are not (listed under `flags.iframes` — switch into the frame's own URL instead).
- `page.step/of` only from a progress bar or "Step N of M" text; single-page forms have `step: ""`.

## `decide`

```
decide(ats, fields[]) -> [{id, source, confidence, by, value}]
source ∈ profile:<path> | answer:<slug> | const:<value> | option:<index> | option:~<regex> | resume:default | LLM:<note> | ASK_USER | LLM
by     ∈ rule | laya | llm | user
```

`decisions.json`: `{ats, url, company, decided_at, decide_model, decisions[], ask_user[{id,label,note}], llm[{id,label,type,required,help,options,current,proposed?,note?}], flags[], laya?}`. Each decision also carries `stage` (`gate | seed | years | cache | open`) — `open` means nothing deterministic matched.

Resolution order (`decide.py decide`, model-independent):

0. **Gate (code).** `type=file` → `resume_rules.csv` default (cover letter / other → `LLM`). Label bars AI assistance → `ASK_USER` + `flags: ai_prohibited`. EEO labels → the decline option where listed; `option:~<regex>` when the menu is lazy (the driver matches it live); required ethnicity with no decline → `Asian` (answer_bank); else `ASK_USER`. US-authorization questions → `ASK_USER`.
1. **Seed rules** — regex label → user-confirmed profile fact (`profile:` virtual keys `candidate.first_name/last_name/province_name`, `work_authorization.answer_yes/sponsorship_no`, `background.background_check_consent_yes`); skill-years labels → `skills_experience_years.<skill>` else `const:2`; salary → `LLM:` with the "use the JD figure" note; "available immediately" dates → today.
2. **`never_guess`** keyword with no seed hit → `ASK_USER`. Nothing on that list reaches a model.
3. **Rule cache** `data/field_rules.csv` (`ats,label_norm,type,source,by,hits,last_used`; `ats=*` matches every ATS) → use it, bump `hits`/`last_used`.
4. **Local model** (`laya_client.py`, only when `settings.csv decide_model` is `shadow` or `laya`; `--model` overrides). One field = one state (ATS, page title, label, type/kind, required, help, options — ≤ 250 tokens); one `POST /v1/systemone/batch` per page for Q1, one per predicted class for Q2:
   - Q1 class, 10 options: `a_contact b_legal c_education d_work e_skill_years f_preference g_eeo h_free_text i_compliance j_other`.
   - Q2 key inside the class (≤ 15 + `NONE`), each key bound to a source in `laya_client.KEYS`; `e` takes the skill from the label (`years_for`), `g` → `ASK_USER`, `h`/`j` → `LLM`.
   - Q3 (only when the code matcher fails) picks the option closest to the resolved value.
   - `laya`: for `open` fields, p₁ and p₂ ≥ 0.85 → adopt + cache `by=laya`; 0.60–0.85 → adopt, no cache; below → `LLM`. Never adopts for textareas matching the free-text regex, never for a never_guess label (those never reach this step). `shadow`: nothing adopted; the summary line reads `laya (shadow): … 0 adopted`.
   - Both modes append `laya-train` rows to `data/decide_shadow.jsonl`: `{state, questions, expected, meta}` where `expected` is the class/key implied by the decision finally taken — from the rule, the cache, or the skill's `merge` (never from the model's own pick). Only decisions with an unambiguous label are logged.
   - The server (`~/job-search/.venv-laya/bin/laya-serve`, `127.0.0.1:8765`) is started on demand and stays up; `data/laya/checkpoint` (from `laya_eval.py train`) is registered as `autoapply-ft` and preferred. If the server cannot start, the summary says `unavailable` and the fields fall through to the LLM marker — never a blocker.
5. **`LLM` marker.** Free text gets `note: free text`.

`laya_eval.py report` — agreement and confidence buckets from the shadow log; `rules` — the same against `field_rules.csv` rows; `export` / `train` — fine-tuning rows and the `laya-train` wrapper (`--synth` adds `field_synth.py` rows: hand-written label phrasings per profile key, each validated against the seed rules by `field_synth.py --check`; `--real-as-eval` keeps every real row out of training); `eval` — scores the loaded checkpoint on `rows/eval.jsonl`. Switch-over rule (plan): ≥ 50 real pages, agreement ≥ 90 %, high-confidence errors < 2 %.

**The skill's LLM step** (`merge`): answer only the `llm[]` entries, once per page, into `llm.json` — `{"f3": "value", "f7": {"value": "...", "cache": false}}`; a value may be a source string (`profile:candidate.city`, `const:Yes`, `option:2`). Free text comes from `answer_bank.md` facts, short, never climbing, never a guessed fact. `merge` resolves the values, rewrites `decisions.json`, and caches `by=llm` rows except free text, EEO, and values over 120 characters.

Option matching (both sides): exact → startsWith → contains → reverse-contains → token-prefix overlap ≥ 0.5 of the option's tokens ("Master of Engineering" ↔ "Masters").

## `driver.js`

One handler chain per `control.kind`, at most two methods (primary, alternate), every method ending in a readback: `input`/`textarea` (native setter + events, then real keystrokes; Workday textarea keystrokes first), `select` (native), `radio`/`checkbox`/`checkbox-group` (click → coordinate click), `file` (`uploadFile` → `DOM.setFileInputFiles`; readback = `files[0].name` or the filename chip), `date-input`, `date-spin` (`Input.dispatchKeyEvent` per digit — `insertText` fires no keydown), `react-select` (type → wait for `[role=option]` → coordinate click, Enter when the match is first; readback from `single-value`/pills, never from the typed input), `workday-prompt` (type → Enter → full pointerdown/mousedown/click sequence on `promptOption` → pill readback), `button-select` (open → listbox option), `combobox`. Values typed into free text are stripped of `< > [ ] { }`. `no_option` short-circuits the chain and returns the live option list in `results.json` so the skill can choose.

`results.json`: `{url, filled_at, elapsed_ms, ok, total, results:[{id,label,ok,actual,method,error?,options?}]}`.

Confirmation rule, evidence columns, tripwire, and dashboard behaviour are unchanged by this loop.

## Fixture

`scripts/fixtures/form.html` is a two-step form with every control kind, honeypots, validation and a confirmation ending. Run the loop against `file://…/form.html` (ATS `fixture`) after touching any script; expected: 18 fields, 17/17 ok, next → step 2, submit → `#confirmation` + "Thank you for applying".

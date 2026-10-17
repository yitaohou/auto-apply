# Page loop — walker → decide → driver

**Status: specification.** The scripts (`scripts/walker.js`, `scripts/driver.js`, `scripts/formfill.js`, `scripts/decide.py`) land in the next release; until then the agent fills pages by hand following the same contract (SKILL.md, Operating a page). Everything below is binding for the scripts and is the shape the hand method imitates.

## Why

The old loop made the model the executor: per field it re-read a 10–30K-token snapshot, wrote a script, ran it, read back, rewrote. 30–90 s per field, of which the decision took under a second. The new loop runs one perception script, one decision pass, one execution script per **page**. Typical page cost: 2 script executions + 0–1 LLM call.

## Per page

    formfill.js walk     → $JOB_DIR/fields.json        (perception, ATS-aware controls only)
    decide.py            → $JOB_DIR/decisions.json     (gate → rule cache → model → LLM markers)
    [LLM markers non-empty] one skill call fills them → decisions.json rewritten
    formfill.js fill     → $JOB_DIR/results.json       (execution + readback)
    formfill.js walk     → check: required still empty / validation errors / next page reached

`ASK_USER` non-empty → the page stops: park the tab, dashboard row, next job. Any `ok=false` in results is retried at most once, then handed back to the skill as a blocker (`references/blockers.md`). `fields.json` / `results.json` stay in `$JOB_DIR` for post-mortems.

## `fields.json`

```json
{"ats":"workday","page":{"url":"…","step":"My Experience","of":6},
 "fields":[{"id":"f3","label":"Phone Number","type":"text","required":true,
            "control":{"kind":"input","ref":"e42"},"current":"","help":"Include country code","options":null},
           {"id":"f7","label":"How did you hear about us?","type":"select",
            "control":{"kind":"workday-prompt","ref":"e51"},"options":["LinkedIn","Indeed"]}],
 "nav":{"next":"e90","submit":null,"save":null},
 "flags":{"honeypot":["beecatcher"],"captcha":false,"login_wall":false,"closed":false}}
```

- `type` ∈ `text | textarea | select | multiselect | radio | checkbox | date | file | unknown`.
- `control.kind` comes from the snapshot role plus the ATS fingerprint (`references/ats-notes.md`) — Workday `data-automation-id`, Greenhouse react-select, LinkedIn Easy Apply modal. The fingerprint decides **how a control is operated**, never what a field means.
- Native selects list their options; custom listboxes are expanded by the driver at fill time, the walker only marks the kind.
- Honeypots (`beecatcher`) and hidden elements are filtered out and listed under `flags`.

## `decide`

    decide(ats, fields[]) -> [{id, source, confidence, by}]
    source ∈ "profile:<key>" | "answer:<key>" | "const:<value>" | "option:<index>" | "ASK_USER" | "LLM"
    by     ∈ "rule" | "laya" | "llm" | "user"

Resolution order, model-independent:

0. **Gate (code).** Honeypot / hidden → skip. `type=file` → `resume_rules.csv`. Label matches a `never_guess` keyword → `ASK_USER`, no model sees it.
1. **Rule cache** `data/field_rules.csv` (`ats,label_norm,type,source,by,hits,last_used`) hit → use it; bump `hits`, `last_used`.
2. **Local model** (Laya, when enabled): Q1 field category — a personal/contact · b authorization/legal · c education · d work history · e skill years · f preferences · g EEO · h customised free text · i compliance · j other. `g` → `ASK_USER`; `h` + text → `LLM`, never cached; `i` → rule (consent = true, signature = legal name, date = today); `j` or confidence < 0.70 → `LLM`. For a–f, Q2 picks the key inside the class (≤15 candidates + NONE): ≥0.85 adopt and cache (`by=laya`); 0.60–0.85 adopt without caching; else `LLM`. Skill-years labels with no listed skill → `const:2`.
3. **Value → control (code).** Text/date filled directly; select/radio matched exact → contains → normalised; unmatched → Q3 "closest option to value V" (≥0.85) → else `LLM`.
4. **LLM (the skill), once per page**, only for fields marked `LLM`: input is the compact field table plus a profile/answer-bank digest; output is a JSON mapping. Adopted answers are cached (`by=llm`) except class h.

Until the local model is enabled, steps 2–3 mark everything `LLM`; the gate, cache, and interface are unchanged. Shadow mode (`settings.csv` `decide_model=llm|laya`) records both answers to `data/decide_shadow.jsonl` without acting on the model's.

## `driver.js`

One handler per `control.kind`, built from the verified methods in `references/ats-notes.md`: text (fill + readback), textarea (real keystrokes; strip `< > [ ] { }`), native select, `workday-prompt` (coordinate click → read `promptOption` list → match → full pointer sequence → readback), `react-select`, radio, checkbox, date (three spinbuttons), file (upload → attachment-name readback, `Remove file` postcondition). Output `results.json`: `[{id, ok, actual, error}]`.

Confirmation rule, evidence columns, tripwire, and dashboard behaviour are unchanged by this loop.

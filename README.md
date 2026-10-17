English | [简体中文](README.zh-CN.md)

# auto-apply

Semi-automated ATS job application workflow for Claude Code, driven by
ego-browser (ego lite). You supply a queue of job URLs; the agent opens each
application, fills the form from your profile, and parks it at the submit button
for your click (or submits and verifies, if you enable that). Two bundled
subagents read emailed verification codes from your mailbox (read-only) and
search LinkedIn/Indeed for new postings.

## Prerequisites

- Claude Code with the ego lite (ego-browser) skill installed
- ego lite logged into the sites you apply through (it reuses your browser sessions)
- Python 3 on `PATH` (used by the decision and ranking scripts)

## Install

1. Add this repository as a plugin marketplace and install the plugin:

       claude plugin marketplace add yitaohou/auto-apply
       claude plugin install auto-apply@auto-apply

   (or do the same through the interactive `/plugin` UI in a session; to try a
   local clone instead, point `marketplace add` at its path)
2. Restart the session — plugins load at startup

## First-time setup

1. **Profile — just say "set up".** The agent runs a setup interview: it opens the
   résumés folder in Finder for you to drop your résumé in, drafts your profile
   from it, and **confirms every value with you before writing anything** —
   sensitive facts (visa, salary, notice) are always asked directly, never
   inferred. The interview fills `candidate_profile.json`, `answer_bank.md`, and
   `data/resume_rules.csv` under `~/job-search/`; you can edit those files by hand
   at any time.
2. **Settings.** `data/settings.csv` has four keys, all yours to edit — the agent
   never writes this file:
   - `auto_submit` — `on` (default): the agent clicks final submit and verifies the
     confirmation page; `off`: every completed form is parked for your click.
   - `email_access` — `off` (default) or `read_only`. With `read_only`, jobs that
     ask for an emailed verification code are handled by the bundled
     `email-code-reader` agent, which reads the code from the Gmail account you
     are logged into in ego lite and never sends, replies, or deletes anything.
     Log into Gmail in ego lite once before enabling it.
   - `email_address` — the address the ATS will send codes to.
   - `decide_model` — `llm` (default), `shadow`, or `laya`: whether the local
     decision model takes part in form filling (next section). Missing key = `llm`.

   Skipping email access only means jobs that require a code are recorded as
   blocked.

## Local decision model (optional)

Form fields are resolved by rules and a cache first; what is left goes to the
model. With [Laya](https://github.com/convaiinnovations/laya) (Apache 2.0, ~420 MB,
runs on an Apple-silicon GPU) those leftover fields can be decided locally in about
a second per page instead of an LLM round trip. It is optional — without it the
skill works exactly as before.

```
/opt/homebrew/bin/python3.12 -m venv ~/job-search/.venv-laya      # 3.12: torch has no 3.14 wheel yet
~/job-search/.venv-laya/bin/pip install "laya[serve]"
```

The scripts start `laya-serve` on `127.0.0.1:8765` on first use and keep it running
(log: `data/laya/serve.log`). Then set `decide_model` in `settings.csv`:

- `shadow` — the model predicts every field but nothing is acted on; each prediction
  and the decision actually taken are appended to `data/decide_shadow.jsonl`. Run this
  for a while first; the zero-shot checkpoint is not accurate enough to trust.
- `laya` — predictions are adopted when confident (≥ 0.85 also cached in
  `field_rules.csv`; 0.60–0.85 adopted for this page only; below that the LLM decides).
  Never for free text, EEO, or anything on `never_guess`.

`python3 skills/auto-apply/scripts/laya_eval.py report` prints the agreement rate
and confidence distribution from the shadow log; switch to `laya` once agreement is
≥ 90 % over ≥ 50 pages with under 2 % high-confidence errors. `laya_eval.py train`
fine-tunes the checkpoint on the shadow log (`data/laya/checkpoint`, picked up on the
next server start as model `autoapply-ft`).

## Starting a run

1. Put job posting URLs into `~/job-search/queue.txt`, one per line
2. Tell the agent: **"run the queue"** (or "apply to these jobs", "process
   queue.txt")
3. With `auto_submit=off`, the agent parks each completed form at the submit button
   and hands you the browser Space — click each submit yourself, don't close the
   pages, then tell the agent you're done so it can verify every tab
4. Anything that needs you (a CAPTCHA, a login wall, a question the profile can't
   answer) appears on a dashboard tab the agent opens; tick items as you clear them
5. Check outcomes in `~/job-search/data/`: `job_pool.csv` (per-job status),
   `blocker_queue.csv` (what needs your action), `daily_dashboard.csv` (totals)

A run only counts a job as **Submitted** when it observes confirmation evidence —
a clicked button is never assumed to be a submission.

## Searching for jobs

Fill `~/job-search/data/search_profile.csv` (keywords, locations, remote policy,
freshness window, free-text requirements — add `sources=indeed` or `sources=both`
there to go beyond LinkedIn — and a default result cap, 0 for none), then say
**"find me jobs"**. The `job-searcher` agent runs two scripts: `search.js` crawls
inside your browser session (one tab per site, rate limits built in; a LinkedIn
security challenge stops every crawl until you delete `data/LINKEDIN_STOP`) and
`classify.py` filters and ranks what was captured (tiers A–D; the rules are in
`skills/auto-apply/references/search-loop.md`) and appends the A/B postings to
`queue.txt`. Run the queue afterwards. Searching never happens on its own.
Each run leaves `data/search/<date>/ranked.csv` and `dropped.csv` so you can see
why a posting was or was not queued; Indeed requires you to be logged in first.

## Layout

| Path | What |
|---|---|
| `skills/auto-apply/SKILL.md` | The skill: contract, boundaries, run loop |
| `skills/auto-apply/references/` | `blockers.md` (states, confirmation rule, blocker procedures), `ats-notes.md` (per-ATS facts), `page-loop.md` (per-page fill loop), `dashboard.md`, `setup.md` |
| `skills/auto-apply/scripts/` | The page loop — `walker.js` (page → fields), `decide.py` (field → source, rule cache), `laya_client.py` / `laya_eval.py` (local decision model, shadow log, fine-tuning), `driver.js` (fill + readback), `formfill.js` (entry point run inside `ego-browser nodejs`); `fixtures/form.html` test form; `seen.py` dedupe/canonicalisation |
| `skills/auto-apply/templates/` | Profile template and the CSV schemas copied into `~/job-search/data/` on first run |
| `agents/` | `email-code-reader`, `job-searcher` |

Parts of this project's structure are adapted from
[`yvonnehe772/applypilot`](https://github.com/yvonnehe772/applypilot) (MIT License —
see `LICENSE`).

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
- Python 3 on `PATH` (used by the search helper script)

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
2. **Settings.** `data/settings.csv` has three keys, all yours to edit — the agent
   never writes this file:
   - `auto_submit` — `on` (default): the agent clicks final submit and verifies the
     confirmation page; `off`: every completed form is parked for your click.
   - `email_access` — `off` (default) or `read_only`. With `read_only`, jobs that
     ask for an emailed verification code are handled by the bundled
     `email-code-reader` agent, which reads the code from the Gmail account you
     are logged into in ego lite and never sends, replies, or deletes anything.
     Log into Gmail in ego lite once before enabling it.
   - `email_address` — the address the ATS will send codes to.

   Skipping email access only means jobs that require a code are recorded as
   blocked.

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
freshness window, free-text requirements, default result count), then say
**"find me jobs"**. The `job-searcher` agent appends matching postings to
`queue.txt`; run the queue afterwards. Searching never happens on its own.

## Layout

| Path | What |
|---|---|
| `skills/auto-apply/SKILL.md` | The skill: contract, boundaries, run loop |
| `skills/auto-apply/references/` | `blockers.md` (states, confirmation rule, blocker procedures), `ats-notes.md` (per-ATS facts), `page-loop.md` (per-page fill loop), `dashboard.md`, `setup.md` |
| `skills/auto-apply/scripts/` | The page loop — `walker.js` (page → fields), `decide.py` (field → source, rule cache), `driver.js` (fill + readback), `formfill.js` (entry point run inside `ego-browser nodejs`); `fixtures/form.html` test form; `seen.py` dedupe/canonicalisation |
| `skills/auto-apply/templates/` | Profile template and the CSV schemas copied into `~/job-search/data/` on first run |
| `agents/` | `email-code-reader`, `job-searcher` |

Parts of this project's structure are adapted from
[`yvonnehe772/applypilot`](https://github.com/yvonnehe772/applypilot) (MIT License —
see `LICENSE`).

# auto-apply

Semi-automated ATS job application workflow for Claude Code, driven by
[ego-browser](https://github.com/ego-browser). You supply a queue of job URLs; the
agent opens each application, fills the form from your profile, and parks it at the
submit button for your click (or submits and verifies, if you enable that). External
capabilities — such as reading email verification codes — are fulfilled by
separately installed [providers](PROVIDERS.md); this core ships none.

## Prerequisites

- Claude Code with the ego lite (ego-browser) skill installed
- ego lite logged into the sites you apply through (it reuses your browser sessions)

## Install

1. Install this plugin (point Claude Code at this repository, or install from a
   marketplace that lists it)
2. Restart the session — plugins load at startup

## First-time setup

1. **Data directory.** The first run creates `~/job-search/` from the bundled
   templates. Fill in:
   - `candidate_profile.json` — your facts (never guessed, only quoted)
   - `answer_bank.md` — the wording typed into forms
   - `data/resume_rules.csv` — which resume variant for which role family
2. **Settings.** `data/settings.csv` defaults to `auto_submit=off` (the agent never
   clicks final submit) and `batch_size=10`. Only change values yourself — the agent
   won't.
3. **Providers (optional, needed for email verification codes).** Ask the agent to
   *register providers*: it lists the options from [PROVIDERS.md](PROVIDERS.md),
   installs your pick after your confirmation, asks for consent
   (`email_access = read_only`) and your address, and writes the binding. Then
   restart the session and perform the provider's declared access setup (for the
   first-party Gmail reader: log into Gmail in ego lite once). Skipping this only
   means jobs that require an emailed code will be recorded as blocked.

## Starting a run

1. Put job posting URLs into `~/job-search/queue.txt`, one per line
2. Tell the agent: **"run the queue"** (or "apply to these jobs", "process
   queue.txt")
3. With `auto_submit=off`, the agent parks each completed form at the submit button
   and hands you the browser Space — click each submit yourself, don't close the
   pages, then tell the agent you're done so it can verify every tab
4. Check outcomes in `~/job-search/data/`: `job_pool.csv` (per-job status),
   `blocker_queue.csv` (what needs your action), `daily_dashboard.csv` (totals)

A run only counts a job as **Submitted** when it observes confirmation evidence —
a clicked button is never assumed to be a submission.

## Layout

| Path | What |
|---|---|
| `skills/auto-apply/` | The skill: SKILL.md + playbook, browser recipes, capability index, registration flow |
| `contracts/` | Capability contracts providers implement |
| `PROVIDERS.md` | Curated list of verified providers |

Parts of this project's structure are adapted from
[`yvonnehe772/applypilot`](https://github.com/yvonnehe772/applypilot) (MIT License —
see `LICENSE`).

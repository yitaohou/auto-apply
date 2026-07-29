---
name: auto-apply
description: "Automated job application pipeline driven by ego-browser. Use when the user wants to apply to jobs from a queue of URLs: filling ATS application forms (Greenhouse, Lever, Ashby, Workday, Oracle, Dayforce, LinkedIn Easy Apply, and others), parking forms at the submit button or auto-submitting them, verifying submission confirmations, triaging blockers, updating the job-search dashboard CSVs, or reporting a run summary. Trigger phrases include: run the queue, apply to these jobs, process queue.txt, check parked tabs, application status report."
---

# AutoApply

AutoApply turns a list of job URLs into completed applications. The user supplies URLs in `~/job-search/queue.txt`; the agent opens each application in ego-browser, fills the form from the candidate profile, and either parks it at the final submit button for the user to click (`auto_submit=off`) or submits and verifies confirmation (`auto_submit=on`).

This system does NOT search for jobs, screen or rank jobs, or tailor resumes. It consumes URLs from `queue.txt` and executes applications. Resume selection is a lookup in `resume_rules.csv`, nothing more.

## Core Contract

- Truthful, traceable applications. Never fabricate experience, credentials, dates, or authorization facts.
- Count only confirmed submissions. A clicked submit button is not a submission (see the confirmation rule in `references/application-playbook.md`).
- One default behavior. There is no separate "test mode" — the same rules apply to every run.
- Facts vs wording: `candidate_profile.json` stores facts (the real salary range); `answer_bank.md` stores wording (the sentence typed into forms). Update a fact once and every phrasing follows.

## Hard Boundaries

1. Never bypass CAPTCHA, hCaptcha, reCAPTCHA, Cloudflare, or any anti-bot check. Hand off to the user.
2. Never guess work authorization, visa, salary, EEO, or anything on the profile's `never_guess` list. Ask instead.
3. When `auto_submit=off`, never click final submit. Only the user edits `settings.csv`.
4. Never submit without verifying the resume is actually attached.
5. Never treat "submit was clicked" as "application submitted".
6. Never invent new `blocker_category` values — use `unknown` and let the user extend the enum.

## Data Files (working directory `~/job-search/`)

Read these five BEFORE every run — skipping them causes duplicate applications and lost rules:

| File | Purpose |
|---|---|
| `data/settings.csv` | `auto_submit` (on/off) and `batch_size`. User-edited only |
| `data/job_pool.csv` | Single source of truth for each job's current status; one row per job — dedupe against it |
| `data/automation_rules.csv` | Accumulated site rules: per-ATS confirmation criteria, accepted address formats |
| `data/blocker_queue.csv` | Retryable failures from earlier runs |
| `queue.txt` | Input: job URLs, one per line |

Read when needed: `candidate_profile.json` and `answer_bank.md` before filling any form; `data/resume_rules.csv` when choosing a resume; `data/application_log.csv` and `data/follow_up.csv` for history.

Write as you go: every attempt (including failures) appends to `application_log.csv`; status changes update `job_pool.csv`; obstacles append to `blocker_queue.csv`; each run's totals accumulate into `data/daily_dashboard.csv` (one row per day — add to today's row, never create a second).

## Job States (`job_pool.status`)

`Submitted` (confirmation evidence observed) · `Parked at submit` (form complete, waiting for the user's click; requires `space_name`, `tab_index`, `parked_at`) · `Pending confirmation` (submit clicked, no evidence seen) · `Skipped` (with reason) · `Blocked` (automation cannot safely finish) · `Needs user` (a user-owned fact or action is missing) · `Pending` (queued, no known blocker).

Every processed job must land in a terminal state — none may dangle. If a job conflicts with an unknown high-impact fact, mark `Needs user`, not `Pending`: escalate to the human instead of queueing optimistically.

## Run Loop

1. Read the five pre-run files. Re-verify leftover `Parked at submit` tabs and re-check `Pending confirmation` jobs before starting new work.
2. Add new `queue.txt` URLs to `job_pool.csv` as `Pending`; skip URLs already present.
3. Process jobs one at a time. With `auto_submit=off`, park `batch_size` forms, hand the Space to the user to click, verify each tab afterward, then continue with the next batch. With `on`, process continuously.
4. After the run: update `daily_dashboard.csv`, then scan `blocker_queue.csv` — any (blocker_category, ATS) pair appearing twice or more becomes a rule in `automation_rules.csv`. This scan is the system's only learning loop; nothing triggers it automatically.

## References — read before acting

- Before operating any application page: `references/application-playbook.md` — state machine details, strict confirmation rule, the automation ladder, and the handling procedure for each of the 14 blocker categories.
- Before writing any browser script: `references/browser-recipes.md` — the heredoc skeleton, scripting principles, and reusable recipes (address loop, upload verification, custom dropdowns, submit endings).
- User-facing instructions live in `USAGE.md`.

---

Parts of this skill's structure (file layout, CSV schemas, template organization) are adapted from [`yvonnehe772/applypilot`](https://github.com/yvonnehe772/applypilot) (MIT License — see `LICENSE`).

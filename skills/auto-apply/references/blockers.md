# Blockers, states, and confirmation

Read before operating any application flow. Enum values here and in the CSVs must stay in sync — change both or neither.

## 1. Job states — `job_pool.status`

Each job is in exactly one state; every processed job ends in a terminal one.

| Status | Definition |
|---|---|
| `Submitted` | Explicit confirmation evidence observed (section 2) |
| `Parked at submit` | Form complete, validation passed, résumé verified; stopped in front of the submit button (requires `space_name`, `tab_index`, `parked_at` — timestamp is diagnostic only) |
| `Pending confirmation` | Submit clicked, no confirmation evidence seen — outcome unknown |
| `Skipped` | Intentionally skipped; reason required |
| `Blocked` | Attempted; automation could not safely complete |
| `Needs user` | A fact or action only the user can provide is missing |
| `Pending` | Worth processing, no known blocker |

`Blocked` = the machine can't do it (high count → tooling needs work). `Needs user` = the machine shouldn't decide for the user (high count → profile incomplete).

Disambiguation: a job that explicitly conflicts with an unknown high-impact fact (e.g. sponsorship TBD in the profile, posting says sponsorship not available) is `Needs user`, NOT `Pending`. When unsure, escalate; never queue optimistically.

`auto_submit` (`settings.csv`) only decides how a *completed* form ends: `on` → click and judge per section 2; `off` → `Parked at submit`, next job. Obstacles are handled identically under both.

A `Parked at submit` job is verified only by opening its tab — never from "the user said they clicked" or elapsed time. `Pending confirmation` is re-checked next run: tab open → re-verify; tab closed → `blocker_queue` row with `user_action_needed = manually verify whether the application went through`.

## 2. Strict confirmation rule

Clicking submit is NOT success — it proves only that the click dispatched. `Submitted` is judged from evidence on the page, never inferred.

Counts (any one): visible text `Application submitted` / `Application sent` / `Thank you for applying`; a thank-you page; URL containing `thanks` / `thank-you` / `submitted` / `confirmation`; the platform explicitly showing the application was sent.

Does NOT count: a saved/bookmarked label; a third-party tracker entry; an extension "applied" badge; autofill completed (filled ≠ submitted); submit clicked with no confirmation page seen (the most dangerous). Nor do partial results, stuck pages, exhausted retries, or degraded attempts.

Evidence columns in `application_log.csv`: `submission_evidence` (which criterion) + `confirmation_url` + `confirmation_text`. All three = audit chain; only the first = unsubstantiated claim.

**Confirmation is not instant** (Workday: 10–20 s). Register the wait BEFORE the click; wait for URL change OR confirmation text, first wins — never URL alone (SPAs swap content in place). When verifying after the user's click, give each tab a 25-second window, never a single read, then classify by the page's ACTUAL state:

| Page state | Conclusion |
|---|---|
| Confirmation text / thank-you page | `Submitted`; fill `confirmation_url` + `confirmation_text`; close tab |
| Form with validation errors | `Blocked`; categorize by symptom (usually `validation-timing`) |
| Form, submit button present, no errors | Tell the user they may have missed this one — name it; keep tab; stays `Parked at submit` |
| Session-expired page | `Blocked` / `session-timeout` |
| Still loading (`aria-busy`, spinner) | `Pending confirmation`; keep tab |

The criteria above are a generic template. After the first confirmed submission on an ATS, record its real URL pattern, text, and delay in `automation_rules.csv` (`rule_category = ATS`) and use those thereafter.

## 3. Automation ladder — `next_retry_strategy`

Retry the SAME operation with a costlier method only after the cheaper one fails. Enum: `browser automation` / `visual control` / `user handoff` / `skip`.

| Level | Method | Use for | New round |
|---|---|---|---|
| 1 | Deterministic page loop — walker/driver scripts (`references/page-loop.md`) | All normal fields, buttons, uploads, tab cleanup | No |
| 2 | DOM + keyboard repair inside a driver handler — `pressKey('Escape')`, focus + `Enter`, arrow keys | Misbehaving dropdowns, overlays | No |
| 3 | Screenshot + coordinate actions | Exception only: DOM and visible state disagree, covered button, custom widget, silent upload | Yes |
| 4 | User handoff — keep tab, write `blocker_queue` | CAPTCHA, login, 2FA, sensitive questions, missing material | Yes |

Level 3 is never the default. Level 4 is a designed exit, not a failure. Same field failing repeatedly → record a blocker and move on.

## 4. Known blockers — `blocker_category`

Twelve legal values. Never invent new ones; use `unknown` and let the user extend the enum.

| Value | Situation |
|---|---|
| `captcha` | CAPTCHA / hCaptcha / reCAPTCHA / Cloudflare / anti-bot |
| `login` | Login, account creation, or 2FA required |
| `session-timeout` | Session expired as the DIRECT cause — mid-fill, or while a completed form awaited the click |
| `submit-timeout` | Submit clicked; neither confirmation nor error within the window |
| `dropdown` | Selection fails; readback still invalid |
| `upload` | Résumé upload fails or cannot be verified |
| `validation-timing` | Site validates only on submit |
| `missing-material` | Form needs material the user hasn't provided |
| `sensitive-question` | Visa/salary/EEO wording doesn't match the profile |
| `email-verification` | The code attempt itself failed — retry later or give up on this job |
| `email-access` | Email channel unavailable; only a user action fixes it |
| `unknown` | Unclassifiable — `what_happened` MUST describe the symptom |

Classify first, then follow the category's procedure in order.

**`dropdown`** — Escape to close overlays → click the REAL option text (never assign a value) → keyboard nav → level 3 → VERIFY the site no longer reports the field invalid. Readback is the crux. Workday's number-one killer (custom components everywhere). Same field repeatedly → record and move on.

**`upload`** — Confirm the file is actually attached before continuing. Traps: PDF-only, custom widgets, silent failure, wrong variant, permission prompt. Unverifiable → `Blocked`; material missing → `Needs user`. NEVER submit without a résumé. `application_log.resume_used` = the file actually attached.

**`validation-timing`** — "No `aria-invalid`" is weak evidence; errors appear only on the click. Defense is assertion design: "option clicked AND field reads back the expected value", not "nothing looked wrong". First occurrence on an ATS → `automation_rules.csv`; use strong assertions there from then on.

**`login`** — Login page → stop, `Blocked`. Never auto-login unless the user explicitly instructs it. Account creation → `Needs user`. Multiple accounts → the one in `candidate_profile.json`. Inherited Chrome login state solves initial login on per-tenant ATSes (Workday / Oracle / Dayforce).

**`session-timeout`** — Three variants, all `Blocked`, `can_retry = yes`, do NOT retry this run (ATS dumps you to page 1; refill next run): expired mid-fill; parked tab found expired during verification; user idle too long under `auto_submit=off` (a shorter queue shortens the park-to-click window). A parked tab found dead during polling is NOT this blocker — expiry is a consequence of the original wall. Close the tab, return the job to `Pending`, leave the original blocker untouched; overwriting it would teach the learning loop the wrong rule.

**`submit-timeout`** — No confirmation, no error, not loading within 25 s → `Blocked`, `can_retry = yes`. Never click submit twice — it may have gone through with a stuck page; a second click risks a duplicate. `what_happened`: current URL, visible error, whether the button is still present. Next run, employer's list shows a record → `Submitted`.

**`captcha`** — Always hand off; never bypass. `Needs user`, keep the tab. Strictly distinguish from emailed codes, which CAN be handled via the segmented-code SOP:
1. Only if `settings.csv` `email_access = read_only` and `email_address` non-empty.
2. Obtain the code per `email-verification` below.
3. Locate each single-character box; clear each individually; type character by character with focused keypresses — never bulk-paste.
4. Read back all values; the joined code must exactly match the email. Submit only after that passes.
5. Duplicated characters, `undefined`, boxes that won't clear, or unverifiable → stop and hand off.
6. Never write the code into any CSV, log, or run summary. Log that a code was retrieved; never its value.

**`email-verification`** — Detect: a code field (single short input or segmented boxes) plus "verification code" / "confirmation code" / "one-time code" / "security code" / "check your email" / a masked hint like `j•••@gmail.com`. Appears as a mailbox second factor at login or mid-application (Workday / Oracle — record the tab URL and task space first, re-verify form state after; lost state → `Blocked` / `unknown`, never refill blindly).
- Preconditions: `email_access = read_only` and `email_address` non-empty, else `Blocked` / `email-access`. Masked hint ≠ `settings.csv` address → `email-access`, don't proceed. Job already failed one code attempt → `Blocked` / `email-verification`; one failure per job is terminal.
- Capture `NOT_BEFORE` (ISO 8601 UTC) immediately BEFORE the action that sends the code (the "Send code" / "Continue" click, or navigation when sent on load) — never after the field is seen. Add no extra tolerance.
- Spawn the `email-code-reader` agent (`agents/email-code-reader.md`) with: ATS, employer, job title, subject keyword (`automation_rules.csv` `rule_category = email`, default `verification`), and `NOT_BEFORE`. Never pass the résumé or profile. Expect up to ~3 minutes; never spawn a second one in parallel.
- Returns `OK <code>` → segmented SOP steps 3–6. `ERR NOT_FOUND` / `STALE_ONLY` / `AMBIGUOUS` → `Blocked` / `email-verification` / `can_retry = yes` (repeated `STALE_ONLY` → the anchor is captured too late). `ERR MAILBOX_UNREACHABLE` → `Blocked` / `email-access`, `user_action_needed` = restore mailbox access.
- ATS rejects the code → terminal: no retry with the same code, no second spawn; `Blocked` / `email-verification` (a new code usually invalidates the old one).
- Learning: after two successes on an ATS, record the subject keyword that worked (`rule_category = email`).

**`email-access`** — `email_access` off, `email_address` missing, mailbox not logged in, mail-account 2FA, masked-address mismatch, or the Sent-folder tripwire. Not retryable by waiting → `Blocked` with a precise `user_action_needed`. Remedies are opposite to `email-verification` ("try later") — never merge them. Tripwire fires → abort the entire run, record `email-access`, alert the user loudly.

**`sensitive-question`** — Work authorization, visa, salary, EEO, anything on `never_guess`. Wording closely matches `candidate_profile.json` / `answer_bank.md` → fill. Differs or no entry → `Needs user`; ONE focused question in the job's dashboard row (`references/dashboard.md`), the notes field carries the answer. Never ask in conversation; don't halt the run. Visa wording traps (`now or in the future` vs `currently`): when unsure, ask.

**`missing-material`** — Portfolio, references, cover letter, certificates. `Needs user`; name exactly what's missing in `user_action_needed`; `open` the target folder and detect arrival by scanning it.

**`unknown`** — `what_happened` MUST describe the symptom. The leak-prevention valve against shoehorning into wrong categories.

## 5. Learning an ATS

One set of procedures applies to every ATS; they differ only in what has been observed. First contact → generic procedures, then record what actually happened in `automation_rules.csv` (confirmation criteria, email pattern, quirks). Platform names above are hazard hints, not a list of what can be processed. Rules learned yourself carry the same weight as the ones written here.

Run wrap-up: any (`blocker_category`, ATS) pair appearing ≥2 times in `blocker_queue.csv` becomes an `automation_rules.csv` row. This is the only learning loop; it runs because you run it.

After `Submitted` / `Skipped` / `Blocked`, close the tab. Keep only `Parked at submit` and `Needs user` tabs.

# Blocker dashboard

A single HTML page (`~/job-search/dashboard.html`) in its own ego lite tab, listing everything waiting on the user. The agent reads its DOM between jobs; the user ticks items as they clear them. The page is a view; `blocker_queue.csv` is the truth — rebuild the page from the CSV after any crash.

Created the first time a run produces a blocker; never for a run with none. Closed at run end only if no parked tabs remain.

## Priority tiers

Ordered by what closing the tab would throw away. Within a tier, oldest first (likeliest to be dead).

| Tier | State | Cost of closing |
|---|---|---|
| 1 | Completed form awaiting the user's click (`Parked at submit`, `auto_submit = off` only) | The whole form |
| 2 | Blocker cleared by the user (polling found it gone), job not yet resumed | The user's work, asked twice |
| 3 | Partly filled form, blocker not cleared | Whatever was filled |
| 4 | Login wall reached before any filling | Almost nothing |

Tier 1 is not a blocker. Word it `Ready to submit — review and click Submit in tab #3`, never `Blocked by: …`. It still counts against the budget.

## Parked-tab budget

At most 20 parked jobs; the dashboard tab does not count (browser holds ≤21 tabs). When all 20 slots are taken and a job needs parking, in order:

1. Any parked tab whose session has died, at any tier → close it, job back to `Pending`, original blocker in `blocker_queue.csv` untouched.
2. Else the oldest live tier-4 tab → close it, record in `blocker_queue.csv`.
3. Else do not park: record the blocker, no tab, move on. The run continues, but every progress report from then on must say so.

Never evict a live tier-1/2/3 tab. A dead one is closed in step 1 without hesitation.

## Detecting a dead session

- Free: `listTabs()` (already called between jobs) shows the tab left its expected page — login screen, home page, session-ended notice → dead.
- Only when a slot is needed: read pages oldest first, stop at the first dead one.
- Ten-minute rule: a tab read within the last ten minutes is assumed unchanged — reuse the verdict. Cache only *alive* verdicts (dead tabs are closed immediately); reset a tab's cache when its URL changes. Working memory only, never a CSV.
- No age-based shortcut: a tab is never presumed dead for being parked a long time. `parked_at` only orders within a tier.

## Layout and JS API

One row per item, sorted by tier then park time:

    [✓] [HIGH] Acme Corp — Senior Fullstack Engineer          parked 12 min ago
        Blocked by: CAPTCHA (form is filled — closing this tab loses all of it)
        What to do: switch to tab #3 → complete the check → come back and tick
        Notes: ________________________________________
                                              [ Undo ]  [ Session expired ]

Each row: tier badge (`READY` for tier 1, else `TIER n`; the "Blocked by" line is hidden for tier 1), company — title, park time, blocker in plain words, instructions naming the tab, tickbox, notes textarea, two buttons.

| Helper | Semantics |
|---|---|
| `aaAddRow({id, tier, company, title, parkedAt, blockedBy, todo})` | Append a row; returns `'exists'` if `id` already present |
| `aaReadState()` | `[{id, ticked, expired, notes}]` for every row |
| `aaClearNotes(id)` | Empty the notes field after consuming it |
| `aaUntick(id, why)` | Clear and unlock the tick; show `why` (default `looks unresolved`) |
| `aaMarkDone(id)` | Dim the row |
| `aaShowBanner()` | Show the stopped banner |

Insert every posting-derived string (company, title) with `textContent`, never `innerHTML` — postings are untrusted and the page lives in a browser holding logged-in sessions.

Controls: **Tickbox** locks (`disabled`) once ticked, so a stray tap cannot flip state while the agent polls. **Undo** is the only way back — clears the tick and unlocks. **Session expired** — the user found the tab dead; agent closes it, job back to `Pending`, original blocker kept, slot freed. The user's eyes override the URL heuristic.

## Where the user acts

Never in the conversation — asking suspends the session.

| Blocker | Where the user acts | How the agent notices |
|---|---|---|
| `login`, `captcha`, a form awaiting a click | The parked tab | `listTabs()` between jobs |
| `sensitive-question` | The row's notes field | Dashboard DOM read |
| `missing-material` | The folder the agent opened | Scanning that folder |

## Between jobs

After every job: one `listTabs()` + one dashboard DOM read.

1. Read state: ticked rows, notes, `Session expired` pressed.
2. Expired → close tab, `Pending`, blocker retained, slot freed.
3. Ticked → verify against the tab, not the tick. A tick means "look at this", not "this is done". Cleared → tier 2, scheduled next. Still blocked → `aaUntick(id)` so the user sees the disagreement.
4. Notes non-empty → consume, then `aaClearNotes(id)` so it is not read twice. For `sensitive-question` the notes carry the answer: fill the form and fold the wording into `answer_bank.md` (the dashboard is scratch, not a store). Anything else ("not applying", "changed the password") → act on it or record in `blocker_queue.what_happened`.
5. Update rows in place; never rewrite the document — the user may be typing.

## Run end

No parked tabs → close the dashboard. Tabs still parked → leave it open and call `aaShowBanner()` (red, top of page), exact wording:

    The agent has stopped. Finish the items below, then return to Claude Code and say
    "user tasks are done, continue the applications".

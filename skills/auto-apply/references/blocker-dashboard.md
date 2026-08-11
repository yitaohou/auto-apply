# Blocker dashboard

A single HTML page, open in its own ego lite tab, listing everything waiting on the
user. The agent reads its DOM between jobs; the user ticks items off as they clear
them.

The page is a **view**. `blocker_queue.csv` is the truth. A closed tab or a crashed
browser loses nothing — rebuild the page from the CSV.

## When it exists

Created the first time a run produces a blocker. Never created for a run that has
none — an empty panel is noise.

Closed at the end of a run only if no parked tabs remain. If any remain, it stays open
with the stopped-banner described below.

## Priority tiers

Every waiting item sits in one of four tiers. The ordering is by **what closing that tab
would throw away**, not by how urgent it feels.

| Tier | State | Cost of closing |
|---|---|---|
| 1 | A completed form waiting for the user's click (`Parked at submit`) | The whole form |
| 2 | Blocker cleared by the user, job not yet resumed | The user's work is wasted, and they will wonder why they are being asked twice |
| 3 | Partly filled form, blocker not yet cleared | Whatever was filled |
| 4 | Login wall, reached before any filling | Almost nothing |

Within a tier, oldest first: the longer a tab has been parked, the more likely its
session is already dead, so closing it costs least.

Tier 1 arises only from `Parked at submit`, and only while `auto_submit = off` — with
`on`, a completed form is submitted rather than parked. Keep the tier regardless; the
user can switch back at any time.

**Tier 1 is not a blocker.** Nothing went wrong: the form is filled and correct, and it
is waiting on one click. Word its row accordingly — `Ready to submit — review and click
Submit in tab #3`, not `Blocked by: …`. Calling it blocked tells the user something
failed when nothing did.

It still counts against the parked-tab budget. The budget bounds how many tabs the
browser holds, not how many things went wrong, and tier 1 tabs are the most expensive
of all to close — an entire filled form. Excluding them would let twenty blockers fill
the budget while fifteen completed forms sat alongside, unbudgeted and invisible to the
eviction rules.

Tier 2's observable test is that polling found the blocker gone while the job has not
yet been resumed.

## The parked-tab budget

**At most 20 parked jobs at a time. The dashboard itself does not count** — it is a
tool, not a job, and letting it occupy a slot would make "how many jobs are backed up"
read wrong. The browser therefore holds at most 21 tabs.

Tier 1 counts like any other tier. The budget bounds how many tabs the browser holds,
not how many things went wrong.

When a job needs parking and all 20 slots are taken, try these in order:

1. **Any parked tab whose session has died, at any tier** (see *Detecting a dead
   session* below) → close it, return that job to `Pending`, and leave its original
   blocker in `blocker_queue.csv` untouched. A slot is freed. A dead tab holds nothing worth protecting: the form state it was guarding
   is already gone, whatever tier it sat in.
2. **Otherwise the oldest live tier-4 tab** → close it and record it in
   `blocker_queue.csv`. A login wall reached before any filling costs nothing to
   reopen, and the oldest is the likeliest to be dead anyway.
3. **Otherwise do not park at all** → record the blocker in `blocker_queue.csv`, leave
   no tab, and move on.

Case 3 does not stop the run. Later jobs may well go through untouched, and a user who
has stepped away is better served by an agent that keeps going. But **every progress
report from then on must say so** (`SKILL.md`, Progress reporting) — otherwise the user
reads the applied count and assumes the rest were handled normally.

**Never evict a live tier-1, tier-2, or tier-3 tab to make room.** Those hold work that
would have to be redone; a tier-4 tab holds a page that can be reopened for free. The
qualifier matters: expiry is what step 1 is for, and a dead tier-1 tab is closed there
without hesitation — it is no longer protecting anything. What this rule forbids is
closing a tab that still holds a usable filled form.

## Detecting a dead session

Step 1 of the eviction order needs a definition of "dead". Expiry shows up in more than
one shape, so use the cheap checks first and read a page only when a slot is actually
needed.

**Free — the URL moved.** The `listTabs()` call already made between jobs returns every
tab's current URL. A tab that has left its expected page — bounced to a login screen, a
home page, a session-ended notice — is dead. This catches the common cases and costs
nothing extra.

**Only when a slot is needed — read the page.** Some expiries leave the URL unchanged.
When the budget is full and neither free check produced a candidate, read tabs **oldest
first and stop at the first dead one**. The oldest is the likeliest to have expired, so
this usually reads one or two, not twenty.

### The ten-minute rule

**A tab read within the last ten minutes is assumed unchanged.** Do not read it again;
reuse the earlier result.

This bounds the DOM reads during a burst of blocked jobs, when the same twenty tabs
would otherwise be re-read every few seconds.

Cache only the *alive* verdict. A tab found dead is closed immediately, so there is no
later read to skip. And a stale *alive* verdict is harmless: the tab is not an eviction
candidate anyway, and being ten minutes late to notice its death costs nothing.

Reset the cache for a tab whenever its URL changes — that is new information, and it
arrives free with `listTabs()`.

Hold this cache in working memory for the duration of the run. It does not belong in
any CSV: it expires in minutes, and a verdict from a previous run says nothing about a
tab in this one. `job_pool.parked_at` is the timestamp that does persist, and it serves
a different purpose — ordering within a priority tier.

Note there is no age-based shortcut: a tab is never presumed dead for having been parked
a long time. A user who steps away for two hours should come back to their work intact,
and this design exists precisely so they can step away.

## Layout

One row per waiting item, sorted by priority tier then by park time, oldest first:

    [✓] [HIGH] Acme Corp — Senior Fullstack Engineer          parked 12 min ago
        Blocked by: CAPTCHA (form is filled — closing this tab loses all of it)
        What to do: switch to tab #3 → complete the check → come back and tick
        Notes: ________________________________________
                                              [ Undo ]  [ Session expired ]

Every row carries: priority tier, company, job title, how long it has been parked, the
blocker in plain words, step-by-step instructions naming the tab, a tickbox, a notes
field, and two buttons.

## Controls

**Tickbox** — the user's signal that they have dealt with this item. **Once ticked it
locks (`disabled`).** A double-click or a stray tap must not toggle state back and
forth; the agent polls between jobs and would otherwise see a value that keeps
changing.

**Undo** — the only way back. An explicit button rather than an untick, so reversing is
a decision and not a misclick. Clears the tick and unlocks the box.

**Session expired** — for when the user switches to the tab and finds the session dead:
the login page bounced them to a home page, or the form is gone. Clicking it tells the
agent to close that tab, return the job to `Pending`, keep its original blocker in
`blocker_queue.csv`, and free a slot.

This button is also the backstop for the agent's own staleness detection, which infers
session death from a URL and will sometimes be wrong. **A user looking at the tab knows
better than the heuristic.**

## Where the user acts

The agent never asks a question — asking suspends the whole session. Each kind of
blocker instead has a place the user acts and a way the agent notices, and none of them
is the conversation:

| Blocker | Where the user acts | How the agent notices |
|---|---|---|
| `login`, `captcha`, `overlay`, a form awaiting a click | The parked tab itself | `listTabs()` between jobs |
| `sensitive-question` | The row's notes field | Reading the dashboard DOM |
| `missing-material` | The folder the agent opened | Scanning that folder |

The dashboard tick is the user's way of saying "look at this now" for any of them. It
is not a claim that the work is done — see below.

## What the agent does between jobs

One `listTabs()` call returns every tab's current URL, and one DOM read covers the whole
dashboard. Both are cheap; run them after every job.

1. Read the dashboard: which rows are ticked, what is in their notes, which have
   `Session expired` pressed.
2. `Session expired` pressed → close that tab, job back to `Pending`, blocker retained,
   slot freed.
3. Ticked → **verify against the tab itself, not the tick.** The tick means "please
   look at this", not "this is done".
   - Blocker actually cleared → the job moves to priority tier 2 and is scheduled next.
   - Still blocked → untick the row, unlock it, and mark it `looks unresolved` so the
     user can see the agent disagreed.
4. Notes non-empty → consume the content (see below), then clear the field so the same
   text is not read twice.
5. Update the row in place. **Never rewrite the whole document** — the user may be
   typing in a notes field.

Trusting the tab over the tick is the same rule as elsewhere in this skill: the user
says when to look, the agent decides what is true.

## Notes field

Two uses:

- **`sensitive-question`** — the user types the answer here. The agent reads it, fills
  the form, and folds the wording into `answer_bank.md`, which is where answers live.
  The dashboard is a scratch surface, not a store.
- **Anything else** — "not applying to this one", "I changed the password", and so on.
  Act on it, or record it in `blocker_queue.what_happened` if it is not actionable.

## Rendering job text safely

Company names and job titles come from postings, which anyone can publish, and this page
renders inside a browser holding the user's logged-in sessions.

**Insert every posting-derived string with `textContent`, never `innerHTML`.** One
property name is the whole defence, and it cannot be got subtly wrong the way a hand-
written escaper can.

This is not only about a deliberate attack. Angle brackets show up in postings for
ordinary reasons — pasted rich text, a template that forgot to escape, someone writing
`Salary: <negotiable>`. Unescaped, the cheapest outcome is a broken layout on the page
the user depends on to work through their backlog.

## When the run ends

**No parked tabs left** → close the dashboard.

**Tabs still parked** → leave it open and put a red banner at the top:

    The agent has stopped. Finish the items below, then return to Claude Code and say
    "user tasks are done, continue the applications".

This is the one moment the user has to speak first, and it is a natural one — they are
back at the computer. Without the banner they would tick everything and wait for
something that is not running.

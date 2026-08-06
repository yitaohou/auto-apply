# Job Search — delegating `job.search@1`

Read this before running any job search. Search is fulfilled entirely by a provider
bound to `job.search@1` (`references/capabilities.md`); the core still does not search
— it now has a way to ask something else to. `queue.txt` remains the only entry point
into the application pipeline.

## Trigger

The user asks explicitly. There is no `settings.csv` switch and no automatic trigger:
no search runs unless the user requests one in the conversation.

## Search criteria — `data/search_profile.csv`

User-edited only. One row: `keywords`, `locations`, `remote`, `posted_within_days`,
`requirements`.

- `requirements` is free-form text (level, roles, skill set, other preferences). The
  core does NOT parse it — pass it through to the provider verbatim. Parsing it would
  grow search semantics inside the core, and that is the provider's territory.
- Never derive search criteria from `candidate_profile.json`. That file holds personal
  data and must never become provider input. What leaves the machine containing no
  personal data is a structural guarantee, not a judgement call.

## Call loop

Delegate with exactly the six fields in `references/capabilities.md`, taking
`KEYWORDS` / `LOCATIONS` / `REMOTE` / `POSTED_WITHIN` / `REQUIREMENTS` from
`search_profile.csv`.

**`MAX_RESULTS` is 5 per batch.** Never request one large batch: a small batch means
applying can start within seconds instead of waiting for a full sweep, and filling one
Workday form takes longer than several search rounds anyway.

**Hard ordering — ingest before requesting the next batch:**

    call provider with MAX_RESULTS=5
        ↓
    append result URLs to queue.txt → complete ingestion into job_pool.csv   ← MUST finish first
        ↓
    need more? → call provider again

Skipping the middle step causes an infinite loop: the provider deduplicates against
`job_pool.csv`, so if the previous batch is not yet ingested it returns the same 5
postings again, the core sees them as new, and the cycle never ends.

**Stop when ANY of these holds:**

- The user's requested count is satisfied
- The provider returns `exhausted: true` — the only stop signal the provider gives;
  without honouring it the loop spins on an empty pool
- Two consecutive batches ingest 0 new jobs (defensive; normally `exhausted` fires first)
- The per-session round limit is reached (20 calls)

**Relay `exhausted: true` faithfully.** It means no further unseen postings exist under
the current criteria — not that the search broke. Tell the user:

> No further unseen postings under the current criteria. Consider broadening
> search_profile.csv.

Do not let the user think a retry would help.

**Report shortfalls honestly.** Asked for 5, got 2 → say so. Never silently return 2 as
if it were the full batch.

## Landing results

The core writes returned URLs into `queue.txt` and runs the existing ingestion
(Run Loop step 2) unchanged — new URLs become `Pending` rows in `job_pool.csv`, and the
metadata fields (`title`, `company`, `location`, `level`) fill the corresponding
`job_pool.csv` columns. Before any of them touch disk, apply the sanitisation rule in
`references/application-playbook.md` ("Sanitising search results") — the provider
sanitises too, but the provider is a stranger's code, so the caller repeats the pass.

Deduplication against seen URLs is the provider's job (it has read access to the `url`
column of `job_pool.csv`, and only that column — contract §1.1). The core does not
re-deduplicate the search results, but ingestion's existing uniqueness check stays — it
is the last line of defence.

## Failures

A search failure produces NO blocker. Blockers record a specific job getting stuck;
search failures happen before any job exists. Tell the user and stop the search:

| Situation | Tell the user |
|---|---|
| No provider bound for `job.search@1` | `No provider bound for job.search@1. See PROVIDERS.md.` |
| Response fails the gate (`ERR PROTOCOL`) | `Search provider returned malformed output; discarded.` |
| Provider returns an error code | Relay it as-is |

Never invent a `blocker_category` value for search; the enum stays as the playbook
defines it.

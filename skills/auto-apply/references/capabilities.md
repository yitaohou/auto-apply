# Capability index

Providers are bound in `data/providers.csv`. Never reference a provider by name in
SKILL.md or the playbook — always resolve through the binding table.

Full contracts live in `contracts/`. This file carries only what the caller needs at
delegation time.

## email.verification_code@1

Contract: `contracts/email.verification_code.v1.md`

Delegation prompt — exactly these five fields, all required:

    ATS: <ats platform name>
    EMPLOYER: <employer name>
    JOB_TITLE: <job title>
    SUBJECT_CONTAINS: <keyword>
    NOT_BEFORE: <ISO 8601 UTC>

Return gate:

    ^OK [A-Za-z0-9]{4,8}$|^ERR (NOT_FOUND|STALE_ONLY|AMBIGUOUS|MAILBOX_UNREACHABLE)$

Anything not matching the gate is discarded as ERR PROTOCOL. Do not parse it, do not
read it. There is no fallback branch that reads prose.

Error codes: NOT_FOUND | STALE_ONLY | AMBIGUOUS | MAILBOX_UNREACHABLE

A call may legitimately run for up to ~3 minutes: the contract obliges providers to
keep observing the mailbox until NOT_BEFORE + 180 seconds before they may conclude
NOT_FOUND or STALE_ONLY. A long-running delegation is not a failure.

## job.search@1

Contract: `contracts/job.search.v1.md`
Flow: `references/search.md`

This provider appends results to `queue.txt` as it works and returns a summary. It does
not return the jobs themselves.

Delegation prompt — exactly these six fields, all required:

    KEYWORDS: <comma-separated titles or keywords>
    LOCATIONS: <comma-separated locations>
    REMOTE: onsite | hybrid | remote | any
    POSTED_WITHIN_HOURS: <integer hours, rolling window from now>
    REQUIREMENTS: <free-form requirement set, passed through verbatim>
    MAX_RESULTS: <integer, how many jobs to append at most>

Return gate — a single line:

    ^OK APPENDED [0-9]+ (EXHAUSTED|MORE_AVAILABLE)( BY_KEYWORD "[^"=]+"=[0-9]+(,"[^"=]+"=[0-9]+)*)?$|^ERR [A-Z_]+$

Anything not matching is discarded as ERR PROTOCOL. Do not parse it, do not read it.
There is no fallback branch that reads prose.

The optional `BY_KEYWORD` suffix breaks the appended count down by the caller's own
keywords (echoed verbatim; counts sum to the appended count — see the contract's §5).
When present, relay the breakdown to the user in the run summary.

Error codes: NO_RESULTS | SOURCE_UNAVAILABLE | RATE_LIMITED | INVALID_INPUT

EXHAUSTED is not an error. It means no further unseen postings exist under these
criteria.

Queue lines carry exactly five keys: url, title, company, location, level. Providers do
not supply a posting date or an ATS name — those are established later, when a job is
actually opened for application.

The appended count is the provider's own claim. Verify it against the lines actually
added; a count exceeding MAX_RESULTS is a contract violation to report to the user.

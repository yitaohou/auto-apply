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

    ^OK [A-Za-z0-9]{4,8}$|^ERR [A-Z_]+$

Anything not matching the gate is discarded as ERR PROTOCOL. Do not parse it, do not
read it. There is no fallback branch that reads prose.

Error codes: NOT_FOUND | STALE_ONLY | AMBIGUOUS | MAILBOX_UNREACHABLE

A call may legitimately run for up to ~3 minutes: the contract obliges providers to
keep observing the mailbox until NOT_BEFORE + 180 seconds before they may conclude
NOT_FOUND or STALE_ONLY. A long-running delegation is not a failure.

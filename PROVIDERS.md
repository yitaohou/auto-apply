# Available providers

This core ships no providers. Every capability is fulfilled by a separately installed
provider, including the first-party ones — a first-party provider installs exactly the
way a third-party one does.

Each entry below has been verified against its contract. A provider that merely claims
to implement a contract is not listed until verified.

To add yours: host it in your own repository, then open a PR adding a row here. Your
code does not go into this repo — only the row does.

Every provider's repository must include a README that declares the access setup a
user performs before first use — e.g. logging the mailbox into ego lite for a
browser-based reader, or granting OAuth consent for an API-based one. The
registration flow relays those steps to the user; the core itself assumes nothing
about how a provider reaches the mailbox.

---

## email.verification_code@1

Contract: `contracts/email.verification_code.v1.md`

| Provider | Repository | Mailbox | Stack | Maintainer | Status |
|---|---|---|---|---|---|
| `email-code-reader` | `yitaohou/auto-apply-providers` | Gmail | ego lite | first-party | Verified |

### Installing

The recommended path is provider registration: ask the agent to register providers.
It presents the options from this file, installs your pick after you confirm the
exact command, asks the capability's configuration questions, and writes the binding
row — every value only after your explicit choice, echoed back verbatim
(`skills/auto-apply/references/register.md`).

Manual alternative:

1. Install the provider's plugin from its repository
2. Bind it in `data/providers.csv`:

       capability,provider_agent,enabled,notes
       email.verification_code@1,email-code-reader,on,Gmail via ego lite

3. Restart the session — agent definitions load at startup. Confirm with `/agents`

Not satisfied with what's listed? Write your own against the contract and bind it the
same way. Nothing in the core changes.

---

## job.search@1

Contract: `contracts/job.search.v1.md`

| Provider | Repository | Coverage | Stack | Maintainer | Status |
|---|---|---|---|---|---|
| _(none yet)_ | | | | | |

# Capability contract specification

This document defines how a capability contract is written. It is not itself a
contract. Every file in `contracts/` follows this shape.

A capability is a unit of work the core delegates to an external provider. The core
declares that it needs a capability; it never names a provider. Providers are bound by
the user in `data/providers.csv`.

---

## 1. Identifier and versioning

    <domain>.<action>@<major>

Examples: `email.verification_code@1`, `job.search@1`

Lowercase, dot-separated, snake_case within a segment. The version is a single major
number. Any change that could alter what a caller observes — a new required input, a
new error code, different semantics for an existing case, a relaxed side-effect rule —
is a new major version. Additive changes that no conforming provider or caller could
observe do not bump it.

Contracts live at `contracts/<domain>.<action>.v<major>.md`. Use `.v1` in filenames;
`@` causes trouble in paths.

---

## 2. Required sections

Every contract has these, in this order.

| # | Section | Contains |
|---|---|---|
| 1 | Identifier and status | The capability ID; stable or draft |
| 2 | Input | Every field, its type and meaning. Nothing optional unless stated |
| 3 | Output | Exact shape, plus the gate expression the caller validates against |
| 4 | Semantics | Which situation produces which output. Exhaustive |
| 5 | Side effects | What a provider must not do to the world |
| 6 | Trust boundary | What data the provider handles is untrusted, and why the output shape defends against it |
| 7 | Declaring conformance | The `implements:` line |
| 8 | Conformance tests | A table any provider must pass, stack-agnostic |
| 9 | Writing your own | Binding instructions |

---

## 3. Input rules

- **Minimum necessary set.** A provider receives only what it needs to do the job.
  Never the résumé, the candidate profile, or unrelated application data. The input
  surface is the data-exposure surface — a third-party provider is a stranger's code
  running against the user's accounts.
- **All fields required unless the contract says otherwise.** Optional fields multiply
  the number of behaviours a caller has to reason about.
- A provider that needs something the contract does not list is a signal the contract
  is wrong. Extending the input locally is not portable — open an issue instead.

---

## 4. Output rules

- **As narrow as the task permits.** A single value, a bounded list, a fixed set of
  error codes. Never free-form prose.
- **Machine-checkable.** Every contract states a regular expression or equivalent gate.
- **The caller discards anything that does not match the gate**, without reading it.
  There is no fallback branch that parses prose. This rule is what makes untrusted
  providers and untrusted source data safe to touch — see §6.
- **Errors are enumerated codes, not messages.** A closed set, each with a distinct
  caller remedy. Two error codes that lead to the same remedy should be one code; one
  code covering two different remedies must be split.

---

## 5. What belongs in a contract

The test is a single question:

> **If the implementation changed, would the caller observe anything different?**

Answer yes → it belongs in the contract. Answer no → it belongs in the provider's own
implementation notes.

| Example rule | Caller observes a difference? | Belongs to |
|---|---|---|
| Read the value from a list preview instead of opening the item | No | Implementation |
| Clock tolerance of 90 seconds | Yes — same input, different result | **Contract** |
| Retry twice with a 10-second gap | No — only latency changes | Implementation |
| Never trigger a "resend" action | Yes — affects the *next* call | **Contract** |

Note the last row. A side effect is observable even when this call's output is
flawless, because it changes the world the next call runs against. That is why §5 of a
contract is part of the interface, not advice.

**Format alone is never sufficient.** All three of these pass a well-formed gate:

- a value taken from a source outside the requested time window
- a value picked arbitrarily when the contract required an ambiguity error
- a value obtained by taking a destructive action first

Only semantics and side effects rule these out.

---

## 6. Trust boundary

State explicitly, in every contract, whether the data a provider handles is
attacker-controlled — and if so, that such data is **data, never instruction**.

Then state the structural defence: a narrow output channel cannot carry an injected
instruction back to the caller. This is why §4 admits no exceptions. Content filtering
is not a defence; output narrowing is.

---

## 7. Conformance

`implements: <capability>@<major>` in the provider's description. It is a **version
marker**, not a discovery mechanism — nothing reads it programmatically. It exists so
that when a contract reaches `@2`, an implementation written against `@1` is
identifiable rather than silently returning a shape the caller no longer expects.

**Declaring conformance does not confer it.** Listing in `PROVIDERS.md` requires
passing the contract's test table.

Conformance tests must be **stack-agnostic**: expressed as setup and expected output,
never in terms of a particular language, protocol, or UI. If a test cannot be run
against a hypothetical second implementation on a different stack, it is testing the
implementation, not the contract.

---

## 8. Adding a new capability

1. Write `contracts/<domain>.<action>.v1.md` following §2
2. Apply §5 to every rule — move implementation details out
3. Sanity-check §2/§3 against a hypothetical second implementation on a different stack
4. Add a delegation entry to `skills/auto-apply/references/capabilities.md` — the
   prompt template, the gate, and the error codes only
5. Add a section to `PROVIDERS.md`
6. State in the playbook when the core needs this capability

The core never names a provider. Step 6 describes the situation; the binding table
resolves it.

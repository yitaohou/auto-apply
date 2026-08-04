# First-run setup interview

The interview turns an empty data directory into a filled one through confirmation
instead of hand-editing: the user uploads their resume first, the agent drafts the
profile from it, and every value is confirmed by the user before it is written.

## The discipline

1. **Resume-extracted values are drafts. A draft is never written until the user
   confirms it in this conversation.** What lands in `candidate_profile.json` is
   always a user-confirmed value — the same fidelity as hand-editing.
2. **Sensitive facts are never drafted from anything.** Work authorization, visa,
   salary expectations, notice period, EEO answers: resumes do not attest these.
   Ask directly; if the user declines to answer, leave the field empty and add it
   to the profile's `never_guess` list.
3. The apply-time rule stands unchanged: when filling forms, read only the
   confirmed profile — **never extract from the resume PDF at apply time** (see the
   playbook). The interview's extraction is setup-time drafting, gated by explicit
   confirmation.
4. **Whenever the user is asked to place files somewhere, open that folder first**
   (macOS: `open <dir>`), then wait for their go-ahead. Never make the user hunt
   for a path. This rule applies to every such moment in this skill, not just the
   interview.
5. Echo each section back after confirmation. The files remain user-editable
   afterwards — the interview is a scribe, not an owner.

## When to run

- The user asks to set up (or first use of the skill).
- Pre-run reads find `candidate_profile.json` missing or still **unfilled** →
  **offer** the interview; do not enter it uninvited, and do not enter it mid-run.

**Unfilled means the key identity fields are empty strings** — check
`candidate.legal_name` and `candidate.email`. The first run generates the file
from the bundled template, so a fresh data directory contains a complete-looking
JSON skeleton whose every value is `""`. Neither the file's existence nor its byte
count ever counts as "set up" — only content does. (A user who filled the file by
hand has, at minimum, a name or an email in it; they are correctly left alone.)

## The flow — in order

1. **Resumes first.** Ensure `~/job-search/resumes/` exists, open it in Finder,
   and ask the user to drop their resume file(s) in. Wait for their go-ahead, then
   list the files received.
2. **Read and draft.** Read the resume(s) and build a draft for the template's
   factual sections: identity and contacts, links, current status, education, work
   history. Do not draft the sensitive fields of rule 2.
3. **Confirm section by section.** Present each section's drafted values as quotes
   from the resume — "the resume says X, correct?" — in small batches. Confirmed →
   written; corrected → the user's version is written; uncertain → left empty.

   **Phone numbers: always store with an explicit country code, defaulting to
   `+1`.** If the resume's number has no code, draft it as `+1XXXXXXXXXX` and
   confirm like any other value; use a different code only when the number or the
   user says otherwise. A code-less phone causes trouble downstream — many ATS
   forms split the country code into its own selector, and the filler then has
   nothing to select by.
4. **Ask what the resume cannot know.** Work authorization / visa (wording traps
   matter — ask precisely), salary range, available start date and notice period,
   location and relocation stance. Declined answers go to `never_guess`.
5. **Answer bank.** Draft wording for common form questions from the confirmed
   facts; the user approves each entry before it lands in `answer_bank.md`.
6. **Resume rules.** Ask which resume variant serves which role family; write
   `data/resume_rules.csv` using the absolute paths of the files in `resumes/`.
7. **Hand off to provider registration** (`references/register.md`) — offer, and
   skip freely if the user declines.
8. **Wrap.** Echo the list of files written and remind the user they can edit any
   of them directly at any time.

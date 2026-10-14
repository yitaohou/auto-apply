# First-run setup interview

Turns an empty data directory into a filled one through confirmation instead of hand-editing: the user uploads a résumé first, the agent drafts the profile from it, and every value is confirmed by the user before it is written.

## Discipline

1. Résumé-extracted values are drafts; a draft is never written until the user confirms it in this conversation.
2. Sensitive facts are never drafted from anything — work authorization, visa, salary, notice period, EEO. Ask directly; if the user declines, leave the field empty and add it to `never_guess`.
3. At apply time read only the confirmed profile; never extract from the résumé PDF then. Extraction is setup-time drafting, gated by confirmation.
4. Whenever the user must place files somewhere, open that folder first (macOS `open <dir>`), then wait for their go-ahead.
5. Echo each section back after confirmation. The files stay user-editable — the interview is a scribe, not an owner.

## When to run

- The user asks ("set up"), or on first use.
- Pre-run reads find `candidate_profile.json` missing or **unfilled** — `candidate.legal_name` and `candidate.email` are empty strings. The first run generates the file from the template, so a complete-looking skeleton of `""` is the NOT-set-up state; existence never counts. Offer the interview; never enter it uninvited or mid-run.

## Flow

1. **Résumés first.** Ensure `~/job-search/resumes/` exists, open it, ask the user to drop their résumé(s) in, wait, list what arrived.
2. **Read and draft** the factual sections: identity and contacts, links, current status, education, work history. Not the sensitive fields.
3. **Confirm section by section**, quoting the résumé ("the résumé says X, correct?") in small batches. Confirmed → written; corrected → the user's version; uncertain → empty.
   Phone numbers always carry an explicit country code, default `+1`: draft `+1XXXXXXXXXX` and confirm like any value. Many ATS forms split the country code into its own selector; a code-less number leaves nothing to select.
4. **Ask what the résumé cannot know**: work authorization / visa (ask precisely — wording traps matter), salary range, start date and notice period, location and relocation stance. Declined answers go to `never_guess`.
5. **Answer bank.** Draft wording for common questions from the confirmed facts; the user approves each entry before it lands in `answer_bank.md`.
6. **Résumé rules.** Which variant serves which role family → `data/resume_rules.csv`, absolute paths.
7. **Email codes (optional).** Explain that emailed verification codes can be read from the mailbox the user is logged into in ego lite, read-only, never sending. If they consent, they set `email_access = read_only` and `email_address` in `data/settings.csv` themselves; the agent never writes that file. Skipping only means jobs that require a code are recorded as blocked.
8. **Wrap.** List the files written and remind the user they can edit any of them directly.

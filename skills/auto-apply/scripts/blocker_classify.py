#!/usr/bin/env python3
"""blocker_classify.py — propose a blocker_category for `unknown` rows (references/blockers.md §4).

    blocker_classify.py [--data ~/job-search] [--all] [--out data/blocker_review.csv]

Reads blocker_queue.csv, asks the local model (laya typed-decisions, one state per row built
from what_happened + why_blocked + next_retry_strategy) which of the twelve categories — or
"none of these" — fits, and writes a REVIEW file: one row per blocker with the proposed
category, P, the runner-up, and the symptom text. It never edits blocker_queue.csv: the user
spot-checks the review file and writes back what they agree with. The enum is the user's —
"none of these" rows are the evidence for extending it, not a licence to invent values.

The same model answers both typed-decision and multilingual questions zero-shot in the
form-filling and search shadows with poor accuracy — on the 143 already-labelled rows it agrees
66 % (2026-10-14), almost always under P 0.5 — so the review file also carries a deterministic
`symptom_family` column (regex over the symptom text) naming what the unknown rows actually are:
apply-control-missing, flow-dead-end, throttle, renderer-freeze, agent-error. Those families,
not the model's picks, are the proposal for extending the enum.
"""
import argparse, collections, csv, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import laya_client  # noqa: E402

CATEGORIES = {
    "captcha": "a CAPTCHA, hCaptcha, reCAPTCHA, Cloudflare or other anti-bot check blocked the way",
    "login": "a login, account creation, or two-factor step was required",
    "session-timeout": "the session expired mid-fill or while the completed form waited",
    "submit-timeout": "submit was clicked and neither a confirmation nor an error appeared in time",
    "dropdown": "a dropdown or custom select could not be set; readback stayed invalid",
    "upload": "the resume upload failed or could not be verified",
    "validation-timing": "the site showed validation errors only after the submit click",
    "missing-material": "the form needed material (document, portfolio, reference) the user has not provided",
    "sensitive-question": "a visa, salary, EEO or similar question did not match the profile wording",
    "email-verification": "an emailed verification code was needed and the code attempt failed",
    "email-access": "the mailbox channel itself was unavailable",
    "none": "none of these: the apply control never rendered, the site throttled or froze, or the agent itself erred",
}
# Symptom families the twelve categories do not cover (regex over what_happened + why_blocked).
# Candidates for extending the enum — the user's call; the script only names them.
FAMILIES = [
    ("agent-error", re.compile(r"agent (reused|error)|tab-(reuse|detection reuse)|closeTab loop|task space .*no longer|apply-open\d+ gone|provider returned prose|tab-finder", re.I)),
    ("throttle", re.compile(r"throttl|suppressed after|after \d+ successful|duplicate employer", re.I)),
    ("renderer-freeze", re.compile(r"renderer froze|froze the renderer|fully unresponsive|Runtime\.evaluate timed out", re.I)),
    ("apply-control-missing", re.compile(r"no apply control|apply( now)? button.*(no modal|produces nothing|no visible effect|never populates|click produces no|produces no visible|no visible effect)|empty-shell|renders (no|header shell)|application-not-found|did not open smartapply|modal does not open|no job/apply content", re.I)),
    ("flow-dead-end", re.compile(r"no continue control|next button .*silently|couldn't save|dialog closes silently|completed to 100% review", re.I)),
]


def family_of(r):
    text = f"{r.get('what_happened', '')} {r.get('why_blocked', '')}"
    for name, rx in FAMILIES:
        if rx.search(text):
            return name
    return ""


QUESTION = {"category": {"type": "choice", "instructions": "Which blocker category best describes what stopped this job application attempt?",
                         "criteria": CATEGORIES}}


def state_for(r):
    return (f"ATS / page: {r.get('job_url', '')}\nSymptom: {r.get('what_happened', '')}\nWhy blocked: {r.get('why_blocked', '')}\n"
            f"Retry strategy chosen: {r.get('next_retry_strategy', '')}\nUser action requested: {r.get('user_action_needed', '')}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="~/job-search")
    ap.add_argument("--all", action="store_true", help="score every row, not only the unknown ones (agreement check)")
    ap.add_argument("--out", default=None, help="review csv (default <data>/data/blocker_review.csv)")
    args = ap.parse_args(argv)
    data = Path(args.data).expanduser()
    src = data / "data" / "blocker_queue.csv"
    out = Path(args.out).expanduser() if args.out else data / "data" / "blocker_review.csv"
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
    todo = [r for r in rows if args.all or r.get("blocker_category") == "unknown"]
    if not todo:
        print("nothing to classify")
        return 0
    client = laya_client.LayaClient(str(data), log=lambda m: print(m, file=sys.stderr))
    if not client.ready:
        print(f"model unavailable: {client.error}", file=sys.stderr)
        return 1
    answers = []
    for i in range(0, len(todo), 8):
        chunk = todo[i:i + 8]
        res = client.batch([state_for(r) for r in chunk], QUESTION, timeout=300)
        if len(res) != len(chunk):
            print(f"batch failed at {i}: {client.error}", file=sys.stderr)
            return 1
        answers.extend(res)
    counts, families = collections.Counter(), collections.Counter()
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "company", "job_title", "job_url", "current_category", "symptom_family", "model_category", "p", "runner_up", "runner_up_p", "what_happened", "why_blocked", "status"])
        for r, a in zip(todo, answers):
            ans = a.get("category") or {}
            probs = sorted((ans.get("probabilities") or {}).items(), key=lambda kv: -kv[1])
            top = ans.get("choice") or (probs[0][0] if probs else "")
            second, second_p = (probs[1] if len(probs) > 1 else ("", 0))
            counts[top] += 1
            fam = family_of(r)
            families[fam or "(none)"] += 1
            w.writerow([r.get("date"), r.get("company"), r.get("job_title"), r.get("job_url"), r.get("blocker_category"), fam, top, ans.get("p"),
                        second, round(float(second_p), 3), r.get("what_happened"), r.get("why_blocked"), r.get("status")])
    print(f"{len(todo)} rows → {out}")
    print("model: " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    print("symptom families (regex): " + ", ".join(f"{k}={v}" for k, v in families.most_common()))
    if args.all:
        known = [r for r in todo if r.get("blocker_category") != "unknown"]
        hits = sum(1 for r, a in zip(todo, answers) if r.get("blocker_category") != "unknown" and (a.get("category") or {}).get("choice") == r.get("blocker_category"))
        print(f"agreement on already-labelled rows: {hits}/{len(known)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

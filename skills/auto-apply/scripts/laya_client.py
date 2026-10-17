#!/usr/bin/env python3
"""laya_client.py — the local typed-decision model behind decide.py (references/page-loop.md, step 4).

Talks to a `laya-serve` daemon (POST /v1/systemone/batch) so decide.py itself needs no torch and the
~18 s model load happens once, not once per page. Runs under the system python; the server runs in
`~/job-search/.venv-laya` (README, "Local decision model").

    client = LayaClient(data_dir)              # reads data/laya/*, starts the server if needed
    preds  = client.predict_fields(ats, page, fields)   # {field_id: {"state", "q1": {...}, "q2": {...}}}
    client.option_match(state, value, options)          # Q3 — only when the code matcher failed

Question set (the decision tree in the plan): Q1 field category (10 classes), Q2 key within the class
(≤ 15 + NONE), Q3 option match. Labels (`expected`) are never produced here — decide.py derives them
from the final adopted decision and writes shadow rows in `laya-train` format to data/decide_shadow.jsonl.
"""
import json, os, re, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

HOST, PORT = "127.0.0.1", 8765
BASE_MODEL = "typed-decisions"
FT_MODEL = "autoapply-ft"                 # name given to data/laya/checkpoint when it exists
ADOPT_CACHE, ADOPT = 0.85, 0.60           # p ≥ 0.85 adopt + cache · 0.60–0.85 adopt, no cache · < 0.60 LLM
MAX_OPTIONS_IN_STATE = 20

# ---------- question set ----------
CATEGORIES = {
    "a_contact": "personal or contact details: name, email, phone, address, city, postal code, links",
    "b_legal": "work authorization, citizenship, sponsorship, background check, criminal record, licence, age",
    "c_education": "education: school, degree, field of study, GPA, graduation dates",
    "d_work": "work history: employer, job title, employment dates, current role, job description",
    "e_skill_years": "years of experience with a named technology or skill",
    "f_preference": "job preferences: start date, notice, salary, relocation, remote/hybrid, how you heard, employment status",
    "g_eeo": "voluntary self-identification: gender, race, ethnicity, veteran, disability, pronouns",
    "h_free_text": "a customised open question answered in the candidate's own words: why us, cover letter, describe, tell us about",
    "i_compliance": "consent, certification, acknowledgement, signature, today's date, marketing opt-in",
    "j_other": "none of the above",
}
Q1 = {"type": "choice", "instructions": "Which category of information does this job-application form field ask for?", "criteria": CATEGORIES}

# Q2 per class: option label → (description, decide.py source).  NONE means "no listed key fits → LLM".
KEYS = {
    "a_contact": {
        "full_name": ("full legal name in one field", "profile:candidate.legal_name"),
        "first_name": ("first or given name", "profile:candidate.first_name"),
        "last_name": ("last, family name or surname", "profile:candidate.last_name"),
        "email": ("email address", "profile:candidate.email"),
        "phone": ("phone or mobile number", "profile:candidate.phone"),
        "linkedin": ("LinkedIn profile URL", "profile:candidate.linkedin_url"),
        "website": ("GitHub, portfolio or personal website URL", "profile:candidate.github_url"),
        "street": ("street address line 1", "profile:candidate.street_address"),
        "address2": ("address line 2, apartment, suite, unit", "const:"),
        "city": ("city or town", "profile:candidate.city"),
        "province": ("province, state or region", "profile:candidate.province_name"),
        "postal_code": ("postal or zip code", "profile:candidate.postal_code"),
        "location": ("current location as city + region in one field", "profile:candidate.current_location"),
        "country": ("country of residence", "profile:work_authorization.country"),
    },
    "b_legal": {
        "authorized": ("legally authorized / eligible to work in the country", "profile:work_authorization.answer_yes"),
        "sponsorship": ("needs visa sponsorship now or in future", "profile:work_authorization.sponsorship_no"),
        "status": ("citizen vs permanent resident vs permit status", "profile:work_authorization.current_authorization"),
        "citizenship": ("nationality or country of citizenship", "profile:work_authorization.citizenship_country"),
        "background_check": ("consent to a background check", "profile:background.background_check_consent_yes"),
        "criminal": ("criminal record or conviction", "const:No"),
        "licence": ("valid driver's licence", "const:Yes"),
        "age": ("18 or older / legal age", "const:Yes"),
        "conflict": ("conflict of interest, relatives at the company, non-compete, NDA, prior employment here", "const:No"),
        "clearance": ("security clearance", "ASK_USER"),
    },
    "c_education": {
        "school": ("school, university or institution name", "profile:education[0].school"),
        "degree": ("degree or highest education level", "profile:education[0].degree"),
        "field": ("field of study, major, discipline", "profile:education[0].field_of_study"),
        "gpa": ("GPA or grade", "const:3.88"),
        "edu_start": ("education start date", "profile:education[0].start_month_year"),
        "edu_end": ("graduation or education end date", "profile:education[0].end_month_year"),
    },
    "d_work": {
        "employer": ("current or most recent employer / company", "profile:work_experience[0].company"),
        "title": ("current or most recent job title", "profile:current_status.current_role_or_framing"),
        "work_start": ("employment start date", "profile:work_experience[0].start_month_year"),
        "work_end": ("employment end date", "profile:work_experience[0].end_month_year"),
        "work_location": ("employer location", "profile:work_experience[0].location"),
        "duties": ("job duties or description", "profile:work_experience[0].description"),
        "total_years": ("total years of professional experience", "const:4"),
    },
    "f_preference": {
        "start_date": ("available start date or availability", "profile:current_status.available_start_date"),
        "notice": ("notice period", "profile:current_status.notice_period"),
        "salary": ("salary or compensation expectation", "LLM:use the figure posted in the JD; profile default 110000 CAD (compensation.single_number_answer)"),
        "relocate": ("willing to relocate", "profile:location_preferences.willing_to_relocate"),
        "work_mode": ("remote, hybrid, on-site or return-to-office willingness", "answer:return_to_office_hybrid_onsite_willingness_user_approved_2026_08_11"),
        "source": ("how did you hear about us / referral source", "const:LinkedIn"),
        "employment_status": ("currently employed / employment status", "profile:current_status.employment_status_answer"),
        "prior_employee": ("previously worked for this company", "const:No"),
    },
    "i_compliance": {
        "consent": ("agree / certify / acknowledge / consent / I have read", "const:Yes"),
        "signature": ("signature — type your full name", "profile:candidate.legal_name"),
        "date": ("today's date or signature date", "const:<today>"),
        "marketing": ("marketing, SMS, newsletter, talent community or job-alert opt-in", "const:No"),
    },
}
# classes with no Q2: e (skill name comes from the label, by code), g → ASK_USER, h/j → LLM


def q2_for(cat):
    """→ (question id, question). One id per class — laya-train wants a stable schema per id."""
    keys = KEYS.get(cat)
    if not keys:
        return None, None
    crit = {k: v[0] for k, v in keys.items()}
    crit["NONE"] = "none of these keys fits this field"
    return "q2_" + cat, {"type": "choice", "instructions": f"This field asks for {CATEGORIES[cat]}. Which specific key does it ask for?", "criteria": crit}


def q3_for(value, options):
    crit = {f"o{i}": o[:80] for i, o in enumerate(options[:40])}
    crit["NONE"] = "no option matches"
    return {"type": "choice", "instructions": f"Which option means the same as the value \"{str(value)[:80]}\"?", "criteria": crit}


def state_text(ats, page, f):
    """One field → compact state (≤ ~250 tokens)."""
    c = f.get("control") or {}
    lines = ["Job application form field", f"ATS: {ats}"]
    title = (page or {}).get("title") or ""
    if title:
        lines.append(f"Page: {title[:100]}")
    if (page or {}).get("step"):
        lines.append(f"Step: {page['step'][:60]}")
    lines.append(f"Label: {(f.get('label') or '')[:200]}")
    lines.append(f"Type: {f.get('type')} ({c.get('kind') or c.get('tag') or ''})")
    lines.append(f"Required: {'yes' if f.get('required') else 'no'}")
    if f.get("help"):
        lines.append(f"Help: {f['help'][:200]}")
    opts = f.get("options") or []
    if opts:
        shown = " | ".join(o[:40] for o in opts[:MAX_OPTIONS_IN_STATE])
        if len(opts) > MAX_OPTIONS_IN_STATE:
            shown += f" | … ({len(opts)} options)"
        lines.append(f"Options: {shown[:600]}")
    return "\n".join(lines)


# ---------- server ----------
class LayaClient:
    def __init__(self, data_dir, host=HOST, port=PORT, autostart=True, timeout=60, log=print):
        self.data = Path(data_dir).expanduser()
        self.base = f"http://{host}:{port}"
        self.host, self.port = host, port
        self.laya_dir = self.data / "data" / "laya"
        self.log = log
        self.model = None
        self.ready = False
        self.error = None
        try:
            self.ready = self._ensure(autostart, timeout)
        except Exception as e:  # never let the model layer break decide
            self.error = f"{type(e).__name__}: {e}"

    # --- lifecycle ---
    def _health(self):
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=3) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def _serve_bin(self):
        for p in (os.environ.get("LAYA_SERVE"), self.data / ".venv-laya" / "bin" / "laya-serve"):
            if p and Path(p).exists():
                return str(p)
        return None

    def _ensure(self, autostart, timeout):
        h = self._health()
        if h is None and autostart:
            exe = self._serve_bin()
            if not exe:
                self.error = "laya-serve not installed (README: Local decision model)"
                return False
            env = dict(os.environ, LAYA_HOST=self.host, LAYA_PORT=str(self.port), LAYA_MODELS=BASE_MODEL,
                       LAYA_DEFAULT_MODEL=BASE_MODEL, LAYA_LOG_LEVEL=os.environ.get("LAYA_LOG_LEVEL", "warning"))
            ckpt = self.laya_dir / "checkpoint"
            if ckpt.is_dir():  # fine-tuned checkpoint (laya_eval.py train): registered and preloaded beside the base
                env["LAYA_EXTRA_MODELS"] = json.dumps({FT_MODEL: str(ckpt)})
                env["LAYA_MODELS"] = BASE_MODEL + "," + FT_MODEL
                env["LAYA_DEFAULT_MODEL"] = FT_MODEL
            self.laya_dir.mkdir(parents=True, exist_ok=True)
            logf = open(self.laya_dir / "serve.log", "ab")
            subprocess.Popen([exe], env=env, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            self.log(f"laya: starting {exe} on {self.base} (log {self.laya_dir / 'serve.log'})")
            t0 = time.time()
            while time.time() - t0 < timeout:
                time.sleep(1.0)
                h = self._health()
                if h:
                    break
        if h is None:
            self.error = f"laya-serve not reachable at {self.base}"
            return False
        loaded = h.get("loaded") or []
        self.model = FT_MODEL if FT_MODEL in loaded else (BASE_MODEL if BASE_MODEL in loaded else (loaded[0] if loaded else None))
        if not self.model:
            self.error = "laya-serve has no model loaded"
            return False
        return True

    # --- inference ---
    def batch(self, states, questions):
        """→ list (per state) of {qid: {choice, probabilities, confidence, answer_confidence}}; [] on failure."""
        if not self.ready or not states:
            return []
        body = json.dumps({"states": states, "questions": questions, "model": self.model}).encode()
        req = urllib.request.Request(self.base + "/v1/systemone/batch", data=body, headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            self.error = f"laya batch failed: {e}"
            return []
        out = []
        for item in res.get("results", []):
            ans = {}
            for qid, a in (item.get("answers") or {}).items():
                ans[qid] = {"choice": a.get("choice"), "p": round(float(a.get("answer_confidence") or 0.0), 4),
                            "entropy_conf": round(float(a.get("confidence") or 0.0), 4), "probabilities": a.get("probabilities")}
            u = item.get("usage") or {}
            if u.get("truncated"):
                ans["_truncated"] = True
            out.append(ans)
        return out

    def predict_fields(self, ats, page, fields):
        """Q1 for every field, then Q2 for the predicted class (2 forward passes per page)."""
        if not fields:
            return {}
        states = [state_text(ats, page, f) for f in fields]
        r1 = self.batch(states, {"q1": Q1})
        preds = {}
        if not r1:
            return preds
        for f, s, a in zip(fields, states, r1):
            preds[f["id"]] = {"state": s, "q1": a.get("q1"), "q2": None, "truncated": bool(a.get("_truncated"))}
        # second pass, grouped by class so each group shares one question
        groups = {}
        for f in fields:
            cat = (preds[f["id"]]["q1"] or {}).get("choice")
            if cat in KEYS:
                groups.setdefault(cat, []).append(f["id"])
        by_id = {f["id"]: f for f in fields}
        for cat, ids in groups.items():
            qid, q = q2_for(cat)
            r2 = self.batch([preds[i]["state"] for i in ids], {qid: q})
            for i, a in zip(ids, r2):
                preds[i]["q2"] = a.get(qid)
                preds[i]["q2_cat"] = cat
        return preds

    def option_match(self, state, value, options):
        """Q3 → (option index or -1, p)."""
        if not options:
            return -1, 0.0
        r = self.batch([state], {"q3": q3_for(value, options)})
        a = (r[0].get("q3") if r else None) or {}
        ch = a.get("choice") or "NONE"
        if ch == "NONE" or not ch.startswith("o"):
            return -1, a.get("p", 0.0)
        return int(ch[1:]), a.get("p", 0.0)


def source_for(cat, key):
    """(class, key) → decide.py source, or None when the pair has no mapping."""
    if cat == "g_eeo":
        return "ASK_USER"
    if cat in ("h_free_text", "j_other"):
        return "LLM"
    ent = KEYS.get(cat, {}).get(key)
    return ent[1] if ent else None


# reverse map: source → (class, key) — used to label shadow rows from the final decision.
# Sources shared by several keys (const:Yes, const:No, legal_name…) are disambiguated by the label text.
SOURCE_TO_LABEL = {}
AMBIGUOUS = {}
for _cat, _keys in KEYS.items():
    for _k, (_d, _src) in _keys.items():
        if _src == "ASK_USER":  # too many reasons lead there; labelled only through the note
            continue
        if _src in SOURCE_TO_LABEL:
            AMBIGUOUS.setdefault(_src, [SOURCE_TO_LABEL[_src]]).append((_cat, _k))
        else:
            SOURCE_TO_LABEL[_src] = (_cat, _k)
SOURCE_TO_LABEL["profile:candidate.preferred_name"] = ("a_contact", "first_name")
SOURCE_TO_LABEL["profile:work_authorization.criminal_record"] = ("b_legal", "criminal")
SOURCE_TO_LABEL["profile:candidate.portfolio_url"] = ("a_contact", "website")
# label regex per ambiguous key (first hit wins, in this order)
KEY_LABEL_RE = [
    ("i_compliance", "signature", r"signature|^sign\b|e-?sign|type your (full )?name"),
    ("i_compliance", "marketing", r"marketing|newsletter|promotional|text message|sms|talent (community|network)|job alerts"),
    ("i_compliance", "consent", r"certify|confirm|acknowledge|agree|attest|consent|understand|accept|have read|declare|terms|privacy"),
    ("b_legal", "licence", r"licen[cs]e"),
    ("b_legal", "age", r"\b18\b|legal age|years or older"),
    ("b_legal", "background_check", r"background check"),
    ("b_legal", "criminal", r"criminal|convict"),
    ("f_preference", "prior_employee", r"previously (worked|employed)|former employee|worked (for|at) (us|this company)|ever been employed"),
    ("b_legal", "conflict", r"relat|family member|household|relationship|conflict of interest|outside business|directorship|secondary employment|non-?compete|non-?disclosure|confidentiality|restriction"),
    ("a_contact", "address2", r"line 2|apartment|suite|unit"),
    ("a_contact", "full_name", r"name"),
]


def _label_by_text(src, label):
    cands = {c for c in AMBIGUOUS.get(src, [])} | ({SOURCE_TO_LABEL[src]} if src in SOURCE_TO_LABEL else set())
    for cat, key, pat in KEY_LABEL_RE:
        if (cat, key) in cands and re.search(pat, label or "", re.I):
            return (cat, key)
    return None


def label_from_decision(d):
    """Final decision → (class, key|None) or None when it cannot be labelled safely."""
    src = d.get("source") or ""
    note = d.get("note") or ""
    label = d.get("label") or ""
    if src.startswith("profile:skills_experience_years.") or (src == "const:2" and "2 years" in note):
        return ("e_skill_years", None)
    if src in AMBIGUOUS or src in ("profile:candidate.legal_name", "const:"):
        return _label_by_text(src, label)
    if src in SOURCE_TO_LABEL:
        return SOURCE_TO_LABEL[src]
    if src.startswith("LLM") and note.startswith("use the figure posted"):
        return ("f_preference", "salary")
    if src == "ASK_USER" and "EEO" in note:
        return ("g_eeo", None)
    if src.startswith("option:") and "EEO" in note:
        return ("g_eeo", None)
    if "free text" in note and (src.startswith("LLM") or (d.get("by") == "llm" and src.startswith("const:"))):
        return ("h_free_text", None)
    if d.get("by") == "llm" and src.startswith("const:") and len(str(d.get("value") or "")) > 120:
        return ("h_free_text", None)
    return None


def shadow_rows(d, pred, meta):
    """laya-train rows for one field: Q1 row (+ Q2 row when the class has keys and the key is known)."""
    lab = label_from_decision(d)
    if not lab or not pred:
        return []
    cat, key = lab
    rows = [{"state": pred["state"], "questions": {"q1": Q1}, "expected": {"q1": cat},
             "meta": meta | {"q": "q1", "laya_choice": (pred.get("q1") or {}).get("choice"), "laya_p": (pred.get("q1") or {}).get("p")}}]
    if cat in KEYS and key:
        q2 = pred.get("q2") if pred.get("q2_cat") == cat else None
        qid, q = q2_for(cat)
        rows.append({"state": pred["state"], "questions": {qid: q}, "expected": {qid: key},
                     "meta": meta | {"q": "q2", "laya_choice": (q2 or {}).get("choice"), "laya_p": (q2 or {}).get("p")}})
    return rows


def append_shadow(data_dir, rows):
    if not rows:
        return 0
    p = Path(data_dir).expanduser() / "data" / "decide_shadow.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


if __name__ == "__main__":  # quick probe: python3 laya_client.py "Label text" [type]
    c = LayaClient(Path.home() / "job-search")
    print("ready:", c.ready, c.model, c.error or "")
    if c.ready and len(sys.argv) > 1:
        f = {"id": "f1", "label": sys.argv[1], "type": sys.argv[2] if len(sys.argv) > 2 else "text", "required": True, "control": {"kind": "input"}}
        print(json.dumps(c.predict_fields("probe", {}, [f]), indent=1))

#!/usr/bin/env python3
"""decide.py — field → source decisions for one page (references/page-loop.md).

    decide.py decide --job-dir DIR [--data ~/job-search] [--company NAME] [--today YYYY-MM-DD]
        reads  DIR/fields.json
        writes DIR/decisions.json  {ats, decisions:[{id,label,type,source,by,confidence,value}],
                                    ask_user:[...], llm:[{id,label,type,required,help,options,current}]}
    decide.py merge  --job-dir DIR --llm FILE
        FILE is the skill's answer for the fields marked LLM: {"f3": "value", "f7": {"value": "...", "cache": false}}
        A value may also be a source string ("profile:candidate.city", "const:Yes", "option:2").
        Rewrites decisions.json with the values resolved and appends cacheable rows to data/field_rules.csv.

Resolution order (model-independent): gate → seed rules → field_rules.csv cache → Laya → LLM marker.
The gate never guesses: never_guess labels are answered only from user-confirmed profile facts
(by=rule) or sent to ASK_USER.

Local model (laya_client.py, `decide_model` in data/settings.csv or --model):
    llm     (default) no local model; everything past the cache is marked LLM
    shadow  Laya predicts every gated-through field; predictions are logged to data/decide_shadow.jsonl
            with the label taken from the final decision (decide, then merge) — never acted on
    laya    predictions are adopted between cache and LLM per the thresholds in laya_client.py
            (p ≥ 0.85 adopt + cache by=laya · 0.60–0.85 adopt only · < 0.60 LLM); still logged
"""
import argparse, csv, datetime as dt, json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_client as laya  # noqa: E402

DATA_DEFAULT = Path.home() / "job-search"
FREE_TEXT_RE = re.compile(r"why|describe|tell us|cover letter|explain|what (interests|makes|excites)|motivat|anything else|additional information|comments|summary|about yourself|message", re.I)


# ---------- helpers ----------
def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def get_path(obj, path):
    """profile path: a.b[0].c ; dict keys may contain dots/slashes, so try the longest literal key first."""
    cur = obj
    rest = path
    while rest:
        if isinstance(cur, dict):
            # longest key that is a prefix of rest followed by end, '.', or '['
            best = None
            for k in cur:
                if rest == k or rest.startswith(k + ".") or rest.startswith(k + "["):
                    if best is None or len(k) > len(best):
                        best = k
            if best is None:
                return None
            cur = cur[best]
            rest = rest[len(best):]
            rest = rest[1:] if rest.startswith(".") else rest
        elif isinstance(cur, list):
            m = re.match(r"\[(\d+)\]\.?(.*)", rest)
            if not m:
                return None
            i = int(m.group(1))
            if i >= len(cur):
                return None
            cur = cur[i]
            rest = m.group(2)
        else:
            return None
    return cur


def answer_sections(md_text):
    """answer_bank.md → {slug: body}. Slug = heading lowercased, non-alnum → '_'."""
    out, cur, buf = {}, None, []
    for line in md_text.splitlines():
        if line.startswith("## "):
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur = re.sub(r"[^a-z0-9]+", "_", line[3:].lower()).strip("_")
            buf = []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


class Ctx:
    def __init__(self, data_dir, company, today):
        self.data = Path(data_dir).expanduser()
        self.profile = load_json(self.data / "candidate_profile.json")
        ab = self.data / "answer_bank.md"
        self.answers = answer_sections(ab.read_text()) if ab.exists() else {}
        self.company = company or ""
        self.today = today or dt.date.today().isoformat()
        self.rules_path = self.data / "data" / "field_rules.csv"
        self.rules = self._load_rules()
        self.resume_rules = self._load_csv(self.data / "data" / "resume_rules.csv")
        self.never_guess = [norm(x) for x in self.profile.get("never_guess", [])]
        st = self._load_csv(self.data / "data" / "settings.csv")
        self.settings = st[0] if st else {}

    def decide_model(self, override=None):
        m = (override or self.settings.get("decide_model") or "llm").strip().lower()
        return m if m in ("llm", "shadow", "laya") else "llm"

    def _load_csv(self, p):
        try:
            with open(p, newline="") as f:
                return list(csv.DictReader(f))
        except FileNotFoundError:
            return []

    def _load_rules(self):
        rows = self._load_csv(self.rules_path)
        return {(r["ats"], r["label_norm"], r["type"]): r for r in rows if r.get("label_norm")}

    def save_rules(self):
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.rules_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ats", "label_norm", "type", "source", "by", "hits", "last_used"])
            w.writeheader()
            for r in self.rules.values():
                w.writerow(r)

    # ----- source → value -----
    def resolve(self, source, field):
        """Return (value, error). value None when unresolvable."""
        if source in ("ASK_USER", "LLM"):
            return None, None
        kind, _, key = source.partition(":")
        if kind == "profile":
            v = get_path(self.profile, key)
            if v is None or v == "":
                return None, f"profile key {key} missing"
            if isinstance(v, bool):
                return v if field["type"] == "checkbox" else ("Yes" if v else "No"), None
            return v, None
        if kind == "answer":
            v = self.answers.get(key)
            return (v, None) if v else (None, f"answer section {key} missing")
        if kind == "const":
            v = key
            if v == "<today>":
                v = self.today
            if field["type"] == "checkbox":
                return v.lower() in ("true", "yes", "1", "on"), None
            return v, None
        if kind == "option":
            opts = field.get("options") or []
            if key.startswith("~"):  # regex, resolved against the live option list by the driver (or here when options are known)
                i = next((i for i, o in enumerate(opts) if re.search(key[1:], o, re.I)), -1)
                return (opts[i] if i >= 0 else key), None
            try:
                return opts[int(key)], None
            except (ValueError, IndexError):
                return None, f"option index {key} out of range"
        if kind == "resume":
            return self.resume_path(), None
        return None, f"unknown source {source}"

    def resume_path(self):
        for r in self.resume_rules:
            if r.get("role_family") == "default" and r.get("resume_file_path"):
                return r["resume_file_path"]
        return self.resume_rules[0]["resume_file_path"] if self.resume_rules else None

    def years_for(self, label):
        sk = self.profile.get("skills_experience_years", {})
        ln = norm(label)
        best = None
        for k, v in sk.items():
            if k == "notes" or not isinstance(v, (int, float)):
                continue
            for alias in [k] + k.split("/") + re.findall(r"\(([^)]+)\)", k):
                a = norm(alias)
                if a and re.search(r"\b" + re.escape(a) + r"\b", ln):
                    if best is None or len(a) > len(best[0]):
                        best = (a, k, v)
        return best


# ---------- seed rules: (regex on label, type filter or None, source) ----------
# Order matters: first hit wins. These are user-confirmed facts and user-approved wording only.
SEED = [
    # identity / contact
    (r"^(legal )?(full|complete) name$|^name$|^your name$", None, "profile:candidate.legal_name"),
    (r"^(first|given) ?name|^first$", None, "profile:candidate.first_name"),
    (r"^(last|family) ?name|^surname|^last$", None, "profile:candidate.last_name"),
    (r"preferred (first )?name|nickname", None, "profile:candidate.first_name"),
    (r"(?<!reference )(?<!referee )e-?mail", "text", "profile:candidate.email"),
    (r"extension|^ext\.?$", "text", "const:"),
    (r"(mobile|cell|phone|telephone)( number)?$|^phone|phone number|contact number", "text", "profile:candidate.phone"),
    (r"phone (device )?type", "select", "const:Mobile"),
    (r"country (phone |dialing )?code|phone code", None, "const:+1"),
    (r"linkedin", "text", "profile:candidate.linkedin_url"),
    (r"github", "text", "profile:candidate.github_url"),
    (r"portfolio|personal (web)?site|website url|^website$", "text", "profile:candidate.github_url"),
    (r"^(current )?location( city)?$|current location|where (are you|do you) (located|live|based)|city and country", None, "profile:candidate.current_location"),
    (r"(street )?address( line)? ?1?$|^address$|street", "text", "profile:candidate.street_address"),
    (r"address line 2|apartment|suite|unit", "text", "const:"),
    (r"^city$|city/town|^town$|city of residence", None, "profile:candidate.city"),
    (r"^country( of residence)?$|country ?/? ?region|which country|country you (live|reside)", None, "profile:work_authorization.country"),
    (r"^province|^state$|state ?/? ?province|state or province|province ?/? ?state|^region$|province (of residence|territory)", None, "profile:candidate.province_name"),
    (r"postal ?code|zip ?code|^zip$|^postcode", None, "profile:candidate.postal_code"),
    (r"^nationality|citizenship$|country of citizenship", None, "profile:work_authorization.citizenship_country"),
    # authorization (user-confirmed facts in the profile; anything about the US is not ours to answer)
    (r"sponsor", None, "profile:work_authorization.sponsorship_no"),
    (r"(legally )?(authori[sz]ed|eligible|entitled|permitted) to work|able to work (lawfully|legally)|work authori[sz]ation|right to work|legal(ly)? (status|right)|work permit", None, "profile:work_authorization.answer_yes"),
    (r"canadian citizen|citizen or permanent resident|permanent resident", None, "profile:work_authorization.current_authorization"),
    # compensation / availability / preferences
    (r"salary|compensation|pay (expectation|rate)|expected (pay|rate)|desired (pay|salary)|hourly rate", None, "LLM:use the figure posted in the JD; profile default 110000 CAD (compensation.single_number_answer)"),
    (r"(total|overall) years|years of (professional|work|relevant|industry|software( development)?|programming|engineering) experience|how many years of (professional|work|relevant|total) experience|years of experience total", None, "const:4"),
    (r"available (to )?start|availability|earliest (start|date|available)|notice period|when (can|could) you start|(available|preferred|desired|target|earliest) start date|date available", None, "profile:current_status.available_start_date"),
    (r"relocat", None, "profile:location_preferences.willing_to_relocate"),
    (r"remote|hybrid|on-?site|in-?office|work arrangement|return to office", "radio|select", "answer:return_to_office_hybrid_onsite_willingness_user_approved_2026_08_11"),
    (r"how did you (hear|learn|find)|^source$|referral source|^referr(al|ed by)|where did you (hear|find)|were you referred", None, "const:LinkedIn"),
    (r"current (job )?title|most recent title|^title$|^job title$|current role|current position", "text", "profile:current_status.current_role_or_framing"),
    (r"current (company|employer)|most recent (company|employer)|^company$|^employer$", "text", "profile:work_experience[0].company"),
    (r"previously (worked|employed)|former employee|worked (for|at) (us|this company)|ever been employed", None, "const:No"),
    (r"relative|family member|household|personal or business relationship|conflict of interest|outside business|directorship|secondary employment|non-?compete|non-?disclosure|confidentiality agreement|post-?employment restriction", None, "const:No"),
    (r"background (check|verification)", None, "profile:background.background_check_consent_yes"),
    (r"criminal|convict|felony|offence", None, "profile:work_authorization.criminal_record_no"),
    (r"driver'?s licen[cs]e|valid licen[cs]e", None, "const:Yes"),
    (r"18 (years|or older)|at least 18|legal age|over 18", None, "const:Yes"),
    (r"security clearance|clearance", None, "ASK_USER"),
    # education (first entry)
    (r"(?<!high )(?<!secondary )(school|university|college|institution)( name)?$|^school|^university|^institution", None, "profile:education[0].school"),
    (r"^degree$|^degree (type|level|name)|degree (type|level)|highest (level of )?(education|degree)|education level|level of education|qualification", None, "profile:education[0].degree"),
    (r"(field|area) of study|^major|discipline|program(me)? of study", None, "profile:education[0].field_of_study"),
    (r"gpa|grade point", None, "const:3.88"),
    # compliance (class i)
    (r"marketing|newsletter|promotional|text message|sms|talent (community|network)|job alerts", "checkbox|radio|select", "const:No"),
    (r"i (certify|confirm|acknowledge|agree|attest|consent|understand|accept|have read|declare)|certify that|agree to|terms (of|and)|privacy (policy|notice|statement)|consent to|acknowledge", "checkbox|radio|select", "const:Yes"),
    (r"signature|^sign|e-?sign|type your (full )?name", "text", "profile:candidate.legal_name"),
    (r"today'?s date|date signed|^date$|signature date", None, "const:<today>"),
]

NEVER_GUESS_PATTERNS = {
    "work authorization": r"authori[sz]ed to work|work authori[sz]ation|right to work|work permit|legal(ly)? (status|right)|eligible to work|entitled to work",
    "visa / sponsorship": r"visa|sponsor|immigration status|nationalit|citizenship|citizen of",
    "employment status": r"employment status|currently employed|current employment|are you (currently )?(working|employed)",
    "relocation": r"relocat",
    "degree dates": r"(graduation|degree|education) (date|year|month)|(start|end) (date|year).*(school|degree|education)|year (of )?graduat",
    "employment dates": r"(employment|job|work) (start|end) date|dates? of employment|(start|end) date.*(employ|job|position|role)|^(from|to)$",
    "background check or non-compete questions": r"background check|non-?compete|criminal",
}
EEO_RE = re.compile(r"gender|race|ethnic|veteran|disabilit|sexual orientation|pronoun|indigenous|aboriginal|visible minority|self-?identif|transgender|hispanic|latin[ox]|lgbt", re.I)
AI_BAN_RE = re.compile(r"(ai|artificial intelligence|generated content|chatgpt|llm)\b.*(disqualif|prohibit|not (be )?(permitted|allowed)|forbid|will result in)|(do not|don t|must not|not) (use|rely on) (ai|artificial intelligence|chatgpt)|without (the )?(use of )?ai|only my own words", re.I)
LAZY_OPTION_KINDS = {"react-select", "workday-prompt", "workday-button-select", "button-select", "combobox"}
DECLINE_RE = re.compile(r"prefer not|decline|do not wish|choose not|rather not|don't wish|not to (answer|disclose|say)", re.I)


def profile_virtual(ctx, key):
    """Derived profile keys used by seed rules."""
    c = ctx.profile.get("candidate", {})
    name = (c.get("legal_name") or "").split()
    wa = ctx.profile.get("work_authorization", {})
    virt = {
        "candidate.first_name": name[0] if name else None,
        "candidate.last_name": " ".join(name[1:]) if len(name) > 1 else None,
        "candidate.province_name": {"ON": "Ontario", "BC": "British Columbia", "QC": "Quebec", "AB": "Alberta", "MB": "Manitoba", "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador", "PE": "Prince Edward Island"}.get(c.get("province"), c.get("province")),
        "work_authorization.answer_yes": "Yes" if wa.get("current_authorization") else None,
        "work_authorization.sponsorship_no": "No" if wa.get("requires_sponsorship_now") is False else None,
        "background.background_check_consent_yes": "Yes" if str(ctx.profile.get("background", {}).get("background_check_consent", "")).lower().startswith("yes") else None,
        "work_authorization.criminal_record_no": "No" if str(wa.get("criminal_record", "")).lower().startswith("no") else None,
    }
    return virt.get(key, "__none__")


def match_option(options, value):
    """Return index of the option matching value (exact → startswith → contains), or -1."""
    if not options:
        return -1
    n = norm(value)
    ns = [norm(o) for o in options]
    if n in ns:
        return ns.index(n)
    for i, o in enumerate(ns):
        if o.startswith(n) and n:
            return i
    for i, o in enumerate(ns):
        if n and n in o:
            return i
    for i, o in enumerate(ns):
        if o and len(o) >= 3 and o in n:
            return i
    # token-prefix overlap ("master of engineering" ↔ "master's degree"): best option with ≥ half its tokens matched
    vt = [t for t in n.split() if len(t) >= 3]
    best, best_score = -1, 0.0
    for i, o in enumerate(ns):
        ot = [t for t in o.split() if len(t) >= 3]
        if not ot or not vt:
            continue
        hit = sum(1 for t in ot if any(t.startswith(v) or v.startswith(t) for v in vt))
        score = hit / len(ot)
        if score >= 0.5 and score > best_score:
            best, best_score = i, score
    return best


def yes_no_option(options, value):
    """Map a Yes/No value onto options such as 'Yes', 'No', 'I am authorized…'."""
    v = norm(value)
    if v in ("yes", "true", "no", "false"):
        want = v in ("yes", "true")
        for i, o in enumerate(options):
            on = norm(o)
            if want and re.match(r"^(yes|y|true|i (am|do|have|agree|consent))\b", on) and not re.match(r"^(no|not)\b", on):
                return i
            if not want and re.match(r"^(no|n|false|i (am not|do not|don t|have not))\b", on):
                return i
    return -1


# ---------- decide ----------
def decide_field(ctx, ats, f):
    """Returns a decision dict for one field."""
    label = f.get("label", "")
    ln = norm(label)
    ftype = f["type"]
    d = {"id": f["id"], "label": label, "type": ftype, "source": None, "by": None, "confidence": 0.0, "value": None}

    def done(source, by, conf, note=None, stage="gate"):
        d.update(source=source, by=by, confidence=conf, stage=stage)
        if note:
            d["note"] = note
        return d

    # 0. gate
    if ftype == "file":
        if re.search(r"cover letter|other|additional|portfolio|transcript", ln):
            return done("LLM", None, 0.0, "file other than resume — the skill decides (cover letter → generate to ~/job-search/materials/)")
        d["value"] = ctx.resume_path()
        return done("resume:default", "rule", 1.0)
    if AI_BAN_RE.search(ln):
        return done("ASK_USER", None, 0.0, "posting bars AI-assisted applications — skip it (policy), do not answer")
    if EEO_RE.search(ln) and not re.search(r"pronoun", ln):
        opts = f.get("options") or []
        i = next((i for i, o in enumerate(opts) if DECLINE_RE.search(o)), -1)
        if i >= 0:
            d["value"] = opts[i]
            return done(f"option:{i}", "rule", 1.0, "EEO → decline where offered")
        if not opts and f.get("control", {}).get("kind") in LAZY_OPTION_KINDS:
            # options only appear once the menu opens — the driver matches this regex against the live list
            d["value"] = "~" + DECLINE_RE.pattern
            return done("option:" + d["value"], "rule", 0.9, "EEO → decline option matched by the driver at fill time")
        if f.get("required") and re.search(r"race|ethnic", ln):
            i = match_option(opts, "Asian")
            if i >= 0:
                d["value"] = opts[i]
                return done(f"option:{i}", "rule", 0.9, "required ethnicity without a decline option (answer_bank)")
        return done("ASK_USER", None, 0.0, "EEO without a decline option")

    # anything about US authorization is not ours to answer (Canada only); raw label, case-sensitive for "U.S."
    if re.search(r"[Uu]nited [Ss]tates|\bU\.?S\.?A?\b", label) and re.search(r"authori|eligib|sponsor|visa|citizen|work", ln):
        return done("ASK_USER", None, 0.0, "US work question")

    # 1. seed rules (user-confirmed facts) — these also cover never_guess labels, so nothing is guessed
    seed_hit = None
    for pat, tfilter, source in SEED:
        if tfilter and not re.search(r"^(" + tfilter + ")$", ftype):
            continue
        if re.search(pat, ln, re.I):
            seed_hit = source
            break
    if seed_hit == "ASK_USER":
        return done("ASK_USER", None, 0.0, "seed rule", stage="seed")
    if seed_hit and seed_hit.startswith("LLM:"):
        return done("LLM", None, 0.0, seed_hit[4:], stage="seed")
    if seed_hit:
        if seed_hit.startswith("profile:"):
            v = profile_virtual(ctx, seed_hit[8:])
            if v == "__none__":
                v, err = ctx.resolve(seed_hit, f)
            if v is None or v == "":
                return done("ASK_USER", None, 0.0, f"{seed_hit} empty in profile", stage="seed")
        else:
            v, err = ctx.resolve(seed_hit, f)
        d["value"] = v
        if ftype == "date" and not re.match(r"^\d{4}-\d{2}", str(v)) and re.search(r"immediate|asap|now|any ?time", str(v), re.I):
            d["value"] = v = ctx.today
        if ftype in ("select", "radio", "multiselect") and f.get("options"):
            i = yes_no_option(f["options"], v)
            if i < 0:
                i = match_option(f["options"], v)
            if i < 0:
                d["value"] = None
                d["proposed"] = v
                return done("LLM", None, 0.0, f"seed value {v!r} matches no option", stage="seed")
            d["value"] = f["options"][i]
        elif ftype == "checkbox":
            d["value"] = bool(v) if isinstance(v, bool) else str(v).lower() in ("yes", "true", "1", "on")
        return done(seed_hit, "rule", 1.0, stage="seed")

    # skill-years
    if re.search(r"years?( of)? (experience|exp)|how many years|experience \(years\)|yrs", ln):
        hit = ctx.years_for(label)
        if hit:
            d["value"] = str(hit[2])
            src = f"profile:skills_experience_years.{hit[1]}"
        else:
            d["value"] = "2"
            src = "const:2"
        if f.get("options"):
            i = match_option(f["options"], d["value"])
            if i < 0:
                i = next((i for i, o in enumerate(f["options"]) if re.search(r"\b" + d["value"] + r"\b", o)), -1)
            if i < 0:
                d["proposed"] = d["value"]
                d["value"] = None
                return done("LLM", None, 0.0, "years value matches no option", stage="years")
            d["value"] = f["options"][i]
        return done(src, "rule", 1.0 if hit else 0.8, None if hit else "skill not listed → 2 years (user rule)", stage="years")

    # never_guess with no confirmed fact → ASK_USER
    for key, pat in NEVER_GUESS_PATTERNS.items():
        if norm(key) in ctx.never_guess and re.search(pat, ln, re.I):
            return done("ASK_USER", None, 0.0, f"never_guess: {key}")

    # 2. cache
    r = ctx.rules.get((ats, ln, ftype)) or ctx.rules.get(("*", ln, ftype))
    if r:
        v, err = ctx.resolve(r["source"], f)
        if v is not None:
            if f.get("options") and ftype != "checkbox":
                i = match_option(f["options"], v)
                if i >= 0:
                    v = f["options"][i]
                else:
                    v = None
            if v is not None:
                d["value"] = v
                r["hits"] = str(int(r.get("hits") or 0) + 1)
                r["last_used"] = ctx.today
                return done(r["source"], r.get("by") or "rule", 0.95, "field_rules cache", stage="cache")

    # 3. local model — cmd_decide runs laya_step() over the page after this pass (needs one batch per page)
    # 4. LLM marker
    return done("LLM", None, 0.0, "free text" if FREE_TEXT_RE.search(ln) else None, stage="open")


# ---------- local model (step 3) ----------
def resolve_source(ctx, source, f):
    """source → value through the seed-rule virtual keys, then Ctx.resolve. (None, err) when empty."""
    if source.startswith("profile:"):
        v = profile_virtual(ctx, source[8:])
        if v != "__none__":
            return (v, None) if v not in (None, "") else (None, f"{source} empty in profile")
    v, err = ctx.resolve(source, f)
    return (v, err) if v not in (None, "") else (None, err or f"{source} empty")


def fit_value(ctx, f, v):
    """Shape a resolved value for the control: today for 'immediately' dates, bool for checkboxes,
    option text for listed options (Yes/No aware). Returns (value, matched) — matched False = no option fits."""
    ftype = f["type"]
    if ftype == "date" and not re.match(r"^\d{4}-\d{2}", str(v)) and re.search(r"immediate|asap|now|any ?time", str(v), re.I):
        return ctx.today, True
    if ftype == "checkbox":
        return (v if isinstance(v, bool) else str(v).lower() in ("yes", "true", "1", "on")), True
    if ftype in ("select", "radio", "multiselect") and f.get("options"):
        i = yes_no_option(f["options"], v)
        if i < 0:
            i = match_option(f["options"], v)
        return (f["options"][i], True) if i >= 0 else (v, False)
    return v, True


def _note(d, text):
    d["note"] = (d["note"] + "; " + text) if d.get("note") else text


def laya_adopt(ctx, ats, f, d, pred, client, summary):
    """mode=laya: turn one prediction into a decision for an `open` field (else leave it LLM)."""
    q1 = pred.get("q1") or {}
    cat, p1 = q1.get("choice"), float(q1.get("p") or 0)
    tag = f"laya {cat} p1={p1:.2f}"
    if not cat or p1 < laya.ADOPT:
        return _note(d, tag + " → LLM")
    if f["type"] == "textarea" and FREE_TEXT_RE.search(norm(f.get("label"))):
        return _note(d, tag + " → free text stays LLM")
    if cat == "g_eeo":
        d.update(source="ASK_USER", by="laya", value=None, confidence=p1)
        return _note(d, tag + " → EEO without a decline option")
    if cat in ("h_free_text", "j_other"):
        return _note(d, tag + " → LLM")
    if cat == "e_skill_years":
        hit = ctx.years_for(f["label"])
        source, p2 = (f"profile:skills_experience_years.{hit[1]}" if hit else "const:2"), p1
        if not hit:
            tag += " (skill not listed → 2 years, user rule)"
    else:
        q2 = pred.get("q2") or {}
        key, p2 = q2.get("choice"), float(q2.get("p") or 0)
        tag += f" {key} p2={p2:.2f}"
        if not key or key == "NONE" or p2 < laya.ADOPT:
            return _note(d, tag + " → LLM")
        source = laya.source_for(cat, key)
        if not source:
            return _note(d, tag + " → LLM")
    if source == "ASK_USER":
        d.update(source="ASK_USER", by="laya", value=None, confidence=min(p1, p2))
        return _note(d, tag)
    if source.startswith("LLM:"):
        return _note(d, tag + " → " + source[4:])
    v, err = resolve_source(ctx, source, f)
    if v is None:
        d.update(source="ASK_USER", by="laya", value=None, confidence=min(p1, p2))
        return _note(d, tag + f" → {err}")
    v, ok = fit_value(ctx, f, v)
    if not ok:  # Q3: let the model pick the option; below threshold the skill chooses (proposed value kept)
        i, p3 = client.option_match(pred["state"], v, f["options"])
        if i < 0 or p3 < laya.ADOPT_CACHE:
            d["proposed"] = v
            return _note(d, tag + f" → value {v!r} matches no option (q3 p={p3:.2f})")
        v = f["options"][i]
        tag += f" q3={p3:.2f}"
    conf = min(p1, p2)
    d.update(source=source, by="laya", value=v, confidence=conf)
    _note(d, tag)
    summary["adopted"] += 1
    ln = norm(f.get("label"))
    if conf >= laya.ADOPT_CACHE and not FREE_TEXT_RE.search(ln) and not EEO_RE.search(ln) and len(str(v)) <= 120:
        ctx.rules[(ats, ln, f["type"])] = {"ats": ats, "label_norm": ln, "type": f["type"], "source": source, "by": "laya", "hits": "1", "last_used": ctx.today}
        summary["cached"] += 1


def shadow_meta(ctx, ats, jd, d, mode, model=None):
    return {"ats": ats, "label": d.get("label"), "type": d.get("type"), "job_dir": str(jd), "id": d["id"], "mode": mode, "model": model,
            "final_source": d.get("source"), "by": d.get("by"), "stage": d.get("stage"), "at": ctx.today}


def laya_step(ctx, ats, page, fields, decisions, mode, jd, log=print):
    """mode ∈ shadow|laya. One Q1 batch (+ one Q2 batch per class) over every field that passed the gate.
    laya: adopt for `open` fields. Both modes: write $JOB_DIR/laya.json and append labelled rows to
    data/decide_shadow.jsonl for the fields whose decision is final now (LLM-marked ones are labelled by merge)."""
    by_id = {f["id"]: f for f in fields}
    cand = [by_id[d["id"]] for d in decisions if d.get("stage") in ("seed", "years", "cache", "open")]
    client = laya.LayaClient(ctx.data, log=log)
    summary = {"mode": mode, "ready": client.ready, "model": client.model, "error": client.error, "fields": len(cand), "adopted": 0, "cached": 0, "logged": 0}
    preds = client.predict_fields(ats, page, cand) if client.ready else {}
    if preds:
        if mode == "laya":
            for d in decisions:
                if d.get("stage") == "open" and d["id"] in preds:
                    laya_adopt(ctx, ats, by_id[d["id"]], d, preds[d["id"]], client, summary)
        rows = []  # labels come only from rules, the cache and the skill — never from Laya's own picks
        for d in decisions:
            if d["id"] in preds and d["source"] != "LLM" and d.get("by") != "laya":
                rows += laya.shadow_rows(d, preds[d["id"]], shadow_meta(ctx, ats, jd, d, mode, client.model))
        summary["logged"] = laya.append_shadow(ctx.data, rows)
    summary["error"] = client.error
    (jd / "laya.json").write_text(json.dumps({"summary": summary, "preds": preds}, indent=1, ensure_ascii=False))
    return summary


def cmd_decide(a):
    ctx = Ctx(a.data, a.company, a.today)
    jd = Path(a.job_dir)
    fields = load_json(jd / "fields.json")
    ats = fields.get("ats", "generic")
    decisions = [decide_field(ctx, ats, f) for f in fields["fields"]]
    mode = ctx.decide_model(a.model)
    laya_summary = laya_step(ctx, ats, fields.get("page"), fields["fields"], decisions, mode, jd) if mode != "llm" else None
    by_id = {f["id"]: f for f in fields["fields"]}
    llm = [{k: by_id[d["id"]].get(k) for k in ("id", "label", "type", "required", "help", "options", "current")} | ({"proposed": d["proposed"]} if d.get("proposed") else {}) | ({"note": d["note"]} if d.get("note") else {})
           for d in decisions if d["source"] == "LLM"]
    out = {
        "ats": ats, "url": fields.get("page", {}).get("url"), "company": ctx.company, "decided_at": dt.datetime.now().isoformat(timespec="seconds"),
        "decisions": decisions,
        "ask_user": [{"id": d["id"], "label": d["label"], "note": d.get("note")} for d in decisions if d["source"] == "ASK_USER"],
        "llm": llm,
        "flags": sorted({"ai_prohibited" for d in decisions if (d.get("note") or "").startswith("posting bars AI")}),
        "decide_model": mode,
    }
    if laya_summary:
        out["laya"] = laya_summary
    (jd / "decisions.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    ctx.save_rules()
    n = len(decisions)
    print(f"decide: {n} fields → {sum(1 for d in decisions if d['value'] is not None)} resolved, {len(out['ask_user'])} ASK_USER, {len(llm)} LLM" + (f"; FLAGS: {', '.join(out['flags'])}" if out["flags"] else ""))
    if laya_summary:
        ls = laya_summary
        print(f"laya ({mode}): " + (f"{ls['model']} on {ls['fields']} fields, {ls['adopted']} adopted, {ls['cached']} cached, {ls['logged']} shadow rows" if ls["ready"] else f"unavailable — {ls['error']} (fields marked LLM)"))
    for d in decisions:
        v = d["value"]
        vs = (json.dumps(v, ensure_ascii=False)[:50] if v is not None else "-")
        print(f"  {d['id']:4} {d['source'] or '-':40.40} {d['by'] or '-':5} {vs}{'  # ' + d['note'] if d.get('note') else ''}")
    if out["ask_user"]:
        print("ASK_USER: " + "; ".join(f"{x['id']} {x['label']}" for x in out["ask_user"]))
    if llm:
        print("LLM needed for: " + "; ".join(f"{x['id']} {x['label'][:40]}" for x in llm))
    print(f"→ {jd / 'decisions.json'}")
    return 0


def cmd_merge(a):
    ctx = Ctx(a.data, a.company, a.today)
    jd = Path(a.job_dir)
    dec = load_json(jd / "decisions.json")
    fields = load_json(jd / "fields.json")
    by_id = {f["id"]: f for f in fields["fields"]}
    answers = load_json(a.llm)
    ats = dec.get("ats", "generic")
    merged = 0
    for d in dec["decisions"]:
        if d["id"] not in answers:
            continue
        f = by_id.get(d["id"], {"type": d["type"], "options": None})
        ans = answers[d["id"]]
        cache = True
        if isinstance(ans, dict):
            cache = ans.get("cache", True)
            ans = ans.get("value")
        if ans is None or ans == "ASK_USER":
            d.update(source="ASK_USER", by="llm", value=None)
            continue
        source = None
        if isinstance(ans, str) and re.match(r"^(profile|answer|const|option):", ans):
            source = ans
            v, err = ctx.resolve(ans, f)
            if v is None:
                d.update(source="ASK_USER", by="llm", value=None, note=err)
                continue
        else:
            v = ans
            source = "const:" + (json.dumps(v) if not isinstance(v, str) else v)
        if f.get("options") and f["type"] != "checkbox" and not isinstance(v, list):
            i = match_option(f["options"], v)
            if i >= 0:
                v = f["options"][i]
        d.update(source=source, by="llm", value=v, confidence=0.9)
        merged += 1
        ln = norm(d["label"])
        if cache and not FREE_TEXT_RE.search(ln) and not EEO_RE.search(ln) and len(str(v)) <= 120:
            key = (ats, ln, d["type"])
            ctx.rules[key] = {"ats": ats, "label_norm": ln, "type": d["type"], "source": source, "by": "llm", "hits": "1", "last_used": ctx.today}
    dec["llm"] = [x for x in dec["llm"] if x["id"] not in answers]
    dec["ask_user"] = [{"id": d["id"], "label": d["label"], "note": d.get("note")} for d in dec["decisions"] if d["source"] == "ASK_USER"]
    (jd / "decisions.json").write_text(json.dumps(dec, indent=1, ensure_ascii=False))
    ctx.save_rules()
    # shadow rows for the fields the skill just answered (their Laya predictions were saved by decide)
    logged = 0
    lj = load_json(jd / "laya.json", {})
    if lj.get("preds"):
        mode, model = dec.get("decide_model", "llm"), (lj.get("summary") or {}).get("model")
        rows = []
        for d in dec["decisions"]:
            if d["id"] in answers and d["id"] in lj["preds"] and d["source"] != "LLM":
                rows += laya.shadow_rows(d, lj["preds"][d["id"]], shadow_meta(ctx, ats, jd, d, mode, model))
        logged = laya.append_shadow(ctx.data, rows)
    print(f"merge: {merged} LLM answers applied; {len(dec['llm'])} still LLM; {len(dec['ask_user'])} ASK_USER; rules file {ctx.rules_path} ({len(ctx.rules)} rows)" + (f"; {logged} shadow rows" if logged else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("decide", cmd_decide), ("merge", cmd_merge)):
        p = sub.add_parser(name)
        p.add_argument("--job-dir", required=True)
        p.add_argument("--data", default=str(DATA_DEFAULT), help="~/job-search")
        p.add_argument("--company", default="")
        p.add_argument("--today", default=None)
        p.add_argument("--model", choices=["llm", "shadow", "laya"], default=None, help="override settings.csv decide_model")
        if name == "merge":
            p.add_argument("--llm", required=True, help="JSON file with the skill's answers for LLM-marked fields")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

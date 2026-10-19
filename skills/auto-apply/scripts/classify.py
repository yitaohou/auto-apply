#!/usr/bin/env python3
"""classify.py — rank captured postings (references/search-loop.md).

    classify.py rank  --data ~/job-search --run 2026-10-14 [--capture extra.jsonl …] [--engine laya|rules]
    classify.py merge --data … --run … --answers review_answers.json     # rank again with the judge's answers
    classify.py queue --data … --run … [--tiers A,B]                       # append to queue.txt, print the OK line
    classify.py report --data … --run …                                    # laya vs rules agreement
    classify.py export --data … --run … [--answers …]                      # laya-train rows → data/laya/search_train.jsonl

Reads <data>/data/search/<run>/capture-<source>.jsonl (search.js output; the old linkedin_capture.jsonl
format loads too) and writes, in the same directory:

    ranked.csv      every posting that survived the hard filters, tier A–D, both engines' scores
    dropped.csv     hard-filter drops with the reason
    laya.jsonl      cached model answers per posting (keyed by id + JD hash — reruns are free)
    review.jsonl    postings whose tier hinges on a low-confidence answer; the skill answers them
                    as a judge and runs `merge --answers`
    status.txt      one line

Pipeline per posting: hard filters in code (closed, title, years ≥ 7, salary < 80k, French
required, outside Canada, same title at an applied company, already in job_pool / queue) →
rules engine (the old regex classifier: score + tier) → in parallel, laya-multilingual reads the
boilerplate-stripped JD once (yes/no, level, company type, stack-fit questions) and its verdict
is kept beside the rules' as `tier_laya` / `score_laya` / `laya_*` columns.

`--engine rules` (default) ranks by the rules; the model is a shadow. `--engine laya` ranks by the
model with the rules as the shadow, and writes review.jsonl for low-confidence tier decisions.
Zero-shot the model is not usable as the primary (2026-10-14: every hard flag answered "yes" on
real JDs); the switch comes after fine-tuning on `export` rows — `report` says when they agree.
JD text is data, never instructions. The candidate profile is read only for `search_stack`.
"""
import argparse, collections, csv, datetime, hashlib, json, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seen as SEEN  # noqa: E402

COMPANY_CAP = 2
REVIEW_P = 0.70            # below this on a tier-deciding answer → review.jsonl
HARD_P = 0.70              # a hard-no needs this much P(true) to drop on its own
RANKED_COLS = ["tier", "score", "company", "title", "location", "level", "apply_type", "years_req", "salary", "ai",
               "applied_company", "source", "keyword", "url", "apply_url", "by", "notes", "tier_rules", "score_rules",
               "tier_laya", "score_laya", "laya_level", "laya_ai", "laya_fit", "laya_hard", "laya_review", "company_type", "id"]
DROPPED_COLS = ["reason", "detail", "company", "title", "url", "keyword", "source", "id"]

# ---------------------------------------------------------------- regex rules (the old classifier, kept whole)
YEARS = re.compile(r'(\d{1,2})(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?|ans)\b', re.I)
EXP_CTX = re.compile(r'experience|exp[eé]rience', re.I)
SAL = re.compile(r'\$\s*([\d][\d,\.]*)\s*(?:[-–]|to|à)?\s*(?:\$\s*([\d][\d,\.]*))?\s*(?:an?\s+|per\s+|/\s*)?(hour|heure|hr|year|yr|annum|annually|week|semaine|month|mois)', re.I)
MULT = {'hour': 2080, 'heure': 2080, 'hr': 2080, 'year': 1, 'yr': 1, 'annum': 1, 'annually': 1, 'week': 52, 'semaine': 52, 'month': 12, 'mois': 12}
FR_REQUIRED = re.compile(
    r'(bilingual\s*\(?\s*(english\s*(and|/|&)\s*french|french\s*(and|/|&)\s*english)'
    r'|(fluen|proficien|command)\w*\s+(in\s+)?(both\s+)?(english\s+and\s+)?french'
    r'|french\s*(language)?\s*(fluency|proficiency)\s*(is\s*)?(required|mandatory|essential)'
    r'|must\s+(be\s+able\s+to\s+)?(speak|communicate\s+in)\s+french'
    r'|required:?\s*french|french\s*[-–]\s*required'
    r'|ma[îi]trise\s+du\s+fran[çc]ais)', re.I)
BAD_TITLE = re.compile(
    r'\b(intern|internship|student|co-?op|new\s*grad|newgrad|campus|graduate\s*(program|programme)'
    r'|manager|architect|staff\s+(\w+\s+){0,3}(engineer|developer)|(engineer|developer)\s+staff|principal|director'
    r'|vice\s*president|\bvp\b|head\s+of|supervisor|supervising)\b', re.I)
LEAD_TITLE = re.compile(r'\b(lead|leader)\b', re.I)
CLOSED = re.compile(r'job has expired|no longer available|no longer accepting applications|cette offre.*expiré', re.I)
NOT_CANADA = re.compile(r'\b(united states|usa|u\.s\.a?\b|new york|california|texas|florida|seattle|austin|boston|chicago|'
                        r'united kingdom|\buk\b|london, england|india|bangalore|hyderabad|germany|berlin|france|paris|australia|sydney|singapore|mexico|brazil)\b', re.I)
CANADA = re.compile(r'\b(canada|ontario|\bon\b|toronto|gta|mississauga|markham|vaughan|waterloo|ottawa|british columbia|\bbc\b|vancouver|burnaby|victoria|'
                    r'alberta|\bab\b|calgary|edmonton|quebec|qu[ée]bec|montr[ée]al|\bqc\b|manitoba|winnipeg|saskatchewan|regina|saskatoon|nova scotia|halifax|'
                    r'new brunswick|newfoundland|pei|prince edward|remote)\b', re.I)
BANK = re.compile(
    r'\b(bank|banque|bmo|rbc|td\b|scotiabank|scotia|cibc|tangerine|desjardins|national bank|laurentian|eq bank|equitable|manulife|sun life|canada life'
    r'|great-west|wealthsimple|citi|citibank|jpmorgan|jp morgan|goldman|morgan stanley|hsbc|barclays|capital one|american express|amex|wells fargo'
    r'|bank of america|credit union|vancity|coast capital|meridian|servus|atb financial|simplii|koho|neo financial|fidelity|blackrock|mastercard|visa)\b', re.I)
AI_TITLE = re.compile(r'\b(ai|a\.i\.|artificial intelligence|machine learning|ml|mlops|llm|llms|nlp|genai|gen ai|generative|deep learning|computer vision'
                      r'|agentic|ai agents?|rag|applied scientist|data scientist|foundation model|prompt engineer\w*|conversational)\b', re.I)
AI_TERMS = [r'\bai\b', r'artificial intelligence', r'machine learning', r'\bml\b', r'mlops', r'\bllms?\b', r'large language model', r'generative ai',
            r'\bgenai\b', r'deep learning', r'neural network', r'\bnlp\b', r'natural language processing', r'computer vision', r'\brag\b',
            r'retrieval[- ]augmented', r'vector (database|db|store)', r'embeddings?', r'fine[- ]?tun\w*', r'prompt engineering', r'\bagentic\b',
            r'ai agents?', r'\bopenai\b', r'\banthropic\b', r'\bgpt\b', r'\bclaude\b', r'\bgemini\b', r'langchain', r'langgraph', r'llamaindex',
            r'hugging ?face', r'pytorch', r'tensorflow', r'transformers?\b', r'foundation models?', r'model (training|inference|serving)']
AI_RE = [re.compile(x, re.I) for x in AI_TERMS]
# User rule 2026-09-18/20: the tech bonus counts only the stacks the candidate actually works in —
# TS/JS, React, Next, Angular, Python, Django, AWS and the LLM-engineering stack. Node/GraphQL/REST/
# Docker/Kotlin/Postgres deliberately out. Same list feeds the model's "candidate stack" header line.
TECH = ["typescript", "javascript", "react", "next.js", "angular", "python", "django", "aws", "langchain", "llamaindex", "openai",
        "anthropic", "claude", "rag", "retrieval-augmented", "embedding", "vector", "prompt engineering", "agentic", "ai agent", "mcp"]
STACK_LINE = ("TypeScript/JavaScript, React, Next.js, Angular, Python, Django, AWS, LLM application engineering "
              "(LangChain/LlamaIndex, OpenAI/Anthropic APIs, RAG, embeddings/vector stores, agents, MCP)")
SUFFIX = re.compile(r'\b(inc|ltd|llc|corp|corporation|limited|co|canada|plc|gmbh|incorporated)\b\.?', re.I)
GENERIC_FIRST = {"people", "cloud", "global", "digital", "north", "south", "first", "smart", "software", "group", "canada", "canadian", "american",
                 "united", "national", "general", "talent", "hired", "staffing", "solutions", "technologies", "technology", "services", "systems",
                 "prime", "alpha", "royal", "pacific", "atlantic", "ontario", "toronto", "vancouver", "montreal", "quebec", "world", "future", "super",
                 "green", "black", "white", "silver", "great", "capital", "cyber", "quantum", "bright", "creative", "agile", "modern", "simple", "clear",
                 "vision", "insight", "innovative", "innovation", "advanced", "premier", "elite", "stealth", "greater", "senior", "junior", "remote",
                 "international", "enterprise"}


def min_years(text):
    best = 0
    for m in YEARS.finditer(text):
        s, e = max(0, m.start() - 80), min(len(text), m.end() + 80)
        if EXP_CTX.search(text[s:e]):
            v = int(m.group(1))
            if v <= 20:
                best = max(best, v)
    return best


def max_salary(text):
    best = None
    for m in SAL.finditer(text):
        unit = MULT[m.group(3).lower()]
        for g in (m.group(1), m.group(2)):
            if g:
                try:
                    v = float(g.replace(',', '')) * unit
                except ValueError:
                    continue
                if 20000 <= v <= 500000:
                    best = max(best or 0, v)
    return best


def ai_signal(title, jd):
    m = AI_TITLE.search(title or "")
    if m:
        return "title:" + m.group(0)
    hits = [(len(r.findall(jd)), r.pattern) for r in AI_RE]
    hits = [(n, p) for n, p in hits if n]
    distinct, total = len(hits), sum(n for n, _ in hits)
    if (distinct >= 3 and total >= 5) or total >= 8:
        return f"jd:{distinct}terms/{total}x"
    return ""


def norm_company(name):
    name = (name or "").strip()
    full = re.sub(r'[^a-z0-9]', '', SUFFIX.sub(" ", name).lower()) or re.sub(r'[^a-z0-9]', '', name.lower())
    words = re.findall(r'[A-Za-z]+', name)
    first = words[0].lower() if words and len(words[0]) >= 5 and words[0].lower() not in GENERIC_FIRST else None
    return full, first


def norm_title(t):
    return re.sub(r'[^a-z0-9]', '', (t or "").lower())


def title_bonus(t):
    t = t.lower()
    if re.search(r'full[- ]?stack', t) or re.search(r'software (engineer|developer)', t):
        return 4
    if re.search(r'front[- ]?end', t):
        return 3.5
    if re.search(r'back[- ]?end', t):
        return 3
    if re.search(r'web developer', t):
        return 2
    if re.search(r'\b(ai|llm|genai|applied ai|ai/ml|ml)\b.{0,12}\b(engineer|developer)|forward[- ]deployed|solutions? engineer', t):
        return 3
    return 0


def location_bonus(loc):
    loc = loc.lower()
    if re.search(r'toronto|gta|mississauga|markham|vaughan|richmond hill|waterloo', loc) or "remote" in loc:
        return 3
    if re.search(r'\bon\b|ontario|vancouver|burnaby|richmond, bc|surrey', loc):
        return 1.5
    if re.search(r'calgary', loc):
        return 0.75
    return 0


def tier_of(s):
    return "A" if s >= 12 else "B" if s >= 9 else "C" if s >= 6 else "D"


def clean_location(loc):
    return (loc or "").split(" · ")[0].strip()


# ---------------------------------------------------------------- boilerplate stripping (for the model state only)
BOILER_HEAD = re.compile(
    r'^\W*(equal (employment )?opportunit\w*|eeo|diversity|inclusion|diversity[, &/]+(equity|inclusion)|our commitment|commitment to diversity'
    r'|benefits?|perks|what we offer|what\'s in it for you|why (join|work)|compensation( and benefits)?|total rewards'
    r'|about (us|the company|the team|our company)|who we are|our (story|mission|values|culture)|company (overview|description)'
    r'|accessibility|accommodations?|accommodation requests?|privacy|legal|disclaimer|notice to (recruiters|agencies)|agency notice'
    r'|how to apply|application process|interview process|next steps)\b.{0,40}:?\W*$', re.I)
ANY_HEAD = re.compile(r'^\W*[A-Z][^.!?]{2,70}:?\s*$')   # a short capitalised line without sentence punctuation
HARD_SIGNAL = re.compile(r'french|fran[çc]ais|bilingual|clearance|citizen|security|intern|co-?op|student|new grad|years|sponsor|visa|'
                         r'authori[sz]ed|united states|u\.s\.|relocat|on-?site|hybrid|remote|ai tool|generative ai|chatgpt', re.I)
JD_STATE_CHARS = 24000   # ~8K tokens of English; the server truncates anyway, this keeps the request honest


def strip_boilerplate(jd):
    out, skipping, dropped = [], False, 0
    for raw in (jd or "").split("\n"):
        line = raw.strip()
        if not line:
            if not skipping:
                out.append("")
            continue
        is_head = len(line) <= 72 and (ANY_HEAD.match(line) or BOILER_HEAD.match(line))
        if is_head:
            skipping = bool(BOILER_HEAD.match(line))
            if skipping:
                dropped += 1
                continue
        if skipping and not HARD_SIGNAL.search(line):
            continue
        out.append(line)
    text = re.sub(r'\n{3,}', '\n\n', "\n".join(out)).strip()
    return text[:JD_STATE_CHARS], dropped


# ---------------------------------------------------------------- model questions
LEVELS = {"junior": "entry level, 0–2 years, junior, associate",
          "intermediate": "intermediate / mid-level, roughly 2–5 years, no 'senior' in the title",
          "senior": "senior engineer, 5+ years, mentors others but is not a manager",
          "staff_plus": "staff, principal, lead, architect, manager, director or above"}
COMPANY_TYPES = {"bank": "a bank, insurer, or other financial institution (or a role placed inside one)",
                 "agency": "a staffing agency, consultancy, or IT services firm placing contractors at clients",
                 "product": "a company building its own software product",
                 "other": "anything else: public sector, non-profit, retail, manufacturing, healthcare provider"}
def yn(instructions, yes, no):
    return {"type": "choice", "instructions": instructions, "criteria": {"yes": yes, "no": no}}


# Zero-shot, the noul form saturates at P(true) ≈ 0.95 on every real JD (2026-10-14, 20 postings),
# so every hard flag is a yes/no choice — same information, and choice rows are what laya-train
# consumes (`classify.py export`).
QUESTIONS = {
    "intern": yn("Is this posting an internship, co-op term, student placement, or new-graduate programme?",
                 "an internship, co-op, student or new-grad posting", "a regular position for experienced hires"),
    "clearance": yn("Does the role require a government security clearance or reliability status?",
                    "a clearance or reliability status is required", "no clearance is mentioned"),
    "french_required": yn("Is French required for this role?", "French is required, mandatory or essential",
                          "French is not required, or is only an asset"),
    "us_only": yn("Must the candidate live in, or be authorized to work in, the United States rather than Canada?",
                  "US residence or US work authorization is required", "Canada or anywhere else is acceptable, or nothing is stated"),
    "ai_ban": yn("Does the posting forbid candidates from using AI tools when preparing their application?",
                 "AI assistance in the application is prohibited", "nothing of the kind is stated"),
    "ai_core": yn("Is building AI, machine-learning, or LLM-based systems the core of this job's day-to-day work?",
                  "AI/ML/LLM development is the central responsibility", "AI is incidental, a tool, or absent"),
    "level": {"type": "choice", "instructions": "What seniority level is this role?", "criteria": LEVELS},
    "company_type": {"type": "choice", "instructions": "What kind of employer posted this role?", "criteria": COMPANY_TYPES},
    "stack_fit": {"type": "score", "instructions": "How much of the technology stack this job requires does the candidate (stack listed in the header) already have?",
                  "criteria": ["none of the required technologies", "a minority of them", "about half", "most of them", "all the core technologies"]},
}
HARD_NO = ("intern", "clearance", "french_required", "us_only", "ai_ban")
LEVEL_OUT = {"junior": "junior", "intermediate": "mid", "senior": "senior", "staff_plus": "senior"}


def candidate_stack(data):
    """The header line the model sees. `candidate_profile.json: search_stack` overrides STACK_LINE."""
    try:
        prof = json.load(open(Path(data) / "candidate_profile.json", encoding="utf-8"))
        if isinstance(prof.get("search_stack"), str) and prof["search_stack"].strip():
            return prof["search_stack"].strip()
    except (OSError, json.JSONDecodeError):
        pass
    return STACK_LINE


def state_for(rec, stack):
    stripped, _ = strip_boilerplate(rec.get("jd") or "")
    return (f"Job title: {rec.get('title') or ''}\nCompany: {rec.get('company') or ''}\nLocation: {clean_location(rec.get('location'))}\n"
            f"Candidate stack: {stack}\n\nJob description:\n{stripped}")


def jd_hash(rec, stack):
    return hashlib.sha1((state_for(rec, stack)).encode("utf-8", "replace")).hexdigest()[:16]


class Model:
    """laya-multilingual through laya_client; every answer cached in laya.jsonl."""

    def __init__(self, data, run_dir, enabled, log):
        self.cache_path = run_dir / "laya.jsonl"
        self.cache = {}
        self.client = None
        self.log = log
        self.calls = 0
        if self.cache_path.exists():
            for line in open(self.cache_path, encoding="utf-8"):
                try:
                    o = json.loads(line)
                    self.cache[o["key"]] = o["answers"]
                except (json.JSONDecodeError, KeyError):
                    pass
        if enabled:
            import laya_client
            self.lc = laya_client
            self.client = laya_client.LayaClient(data, log=log)
            if not self.client.ready:
                log(f"laya unavailable ({self.client.error}) — rules engine is primary for this pass")
                self.client = None

    @property
    def ready(self):
        return self.client is not None

    def answers(self, keyed, stack, batch=6):
        """keyed: {key: rec} → {key: answers}; asks the server only for keys not in the cache."""
        out = {k: self.cache[k] for k in keyed if k in self.cache}
        todo = [(k, r) for k, r in keyed.items() if k not in self.cache]
        if not todo or not self.ready:
            return out
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            res = self.client.batch([state_for(r, stack) for _, r in chunk], QUESTIONS, model=self.client.ml_model, max_len=self.lc.ML_MAX_LEN, timeout=900)
            self.calls += 1
            if len(res) != len(chunk):
                self.log(f"laya batch returned {len(res)}/{len(chunk)} ({self.client.error}) — remaining postings fall back to rules")
                self.client = None
                return out
            with open(self.cache_path, "a", encoding="utf-8") as f:
                for (k, _), a in zip(chunk, res):
                    self.cache[k] = a
                    out[k] = a
                    f.write(json.dumps({"key": k, "answers": a, "at": datetime.datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            self.log(f"  laya {min(i + batch, len(todo))}/{len(todo)}")
        return out


# ---------------------------------------------------------------- data loading
def load_capture(paths):
    recs, bad = {}, 0
    for p in paths:
        if not p.exists():
            continue
        for line in open(p, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(o, dict) or not (o.get("id") or o.get("url")):
                bad += 1
                continue
            src = o.get("source") or ("indeed" if "indeed." in (o.get("url") or "") else "linkedin")
            o["source"] = src
            o["id"] = str(o.get("id") or SEEN.canonical(o["url"]) or o["url"])
            recs[f"{src}:{o['id']}"] = o   # latest capture wins
    return recs, bad


def load_applied(data):
    fulls, firsts, raw, titles = set(), set(), {}, {}

    def add(name, title):
        full, first = norm_company(name)
        keys = []
        if full:
            fulls.add(full)
            keys.append(full)
        if first:
            firsts.add(first)
            keys.append("w:" + first)
        for k in keys:
            raw.setdefault(k, name)
            if title:
                titles.setdefault(k, set()).add(norm_title(title))
    for fn, pred in ((Path(data) / "data" / "application_log.csv", lambda s: s.startswith("submitted")),
                     (Path(data) / "data" / "job_pool.csv", lambda s: s == "submitted")):
        try:
            for r in csv.DictReader(open(fn, newline="", encoding="utf-8")):
                if pred((r.get("status") or "").strip().lower()):
                    add(r.get("company"), r.get("job_title"))
        except FileNotFoundError:
            pass
    return fulls, firsts, raw, titles


def applied_match(company, title, applied):
    fulls, firsts, raw, titles = applied
    full, first = norm_company(company)
    key = full if full and full in fulls else ("w:" + first if first and first in firsts else None)
    if not key:
        return None, False
    return raw.get(key) or company, bool(norm_title(title)) and norm_title(title) in titles.get(key, set())


def load_overrides(data):
    out = {}
    for fn in ("search_overrides.csv", "linkedin_overrides.csv"):
        try:
            for r in csv.DictReader(open(Path(data) / "data" / fn, newline="", encoding="utf-8")):
                if (r.get("id") or "").strip() and (r.get("tier") or "").strip().upper() in ("A", "B", "C", "D"):
                    out[r["id"].strip()] = (r["tier"].strip().upper(), (r.get("note") or "").strip())
        except FileNotFoundError:
            pass
    return out


def load_queue_urls(data):
    urls = set()
    q = Path(data) / "queue.txt"
    if not q.exists():
        return urls
    for line in open(q, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                line = json.loads(line).get("url") or ""
            except json.JSONDecodeError:
                continue
        if line:
            urls.add(line)
            c = SEEN.canonical(line)
            if c:
                urls.add(c)
    return urls


# ---------------------------------------------------------------- the pass
def hard_filter(o, applied, pool_urls):
    """→ (reason, detail) or (None, None). Code only, full JD text."""
    title, jd, loc = o.get("title") or "", o.get("jd") or "", clean_location(o.get("location"))
    if o.get("closed") is True or CLOSED.search(jd[:600]):
        return "closed", ""
    if not jd.strip():
        return "no_jd", ""
    url_c = SEEN.canonical(o.get("url") or "") or o.get("url") or ""
    if url_c in pool_urls or (o.get("url") or "") in pool_urls:
        return "in_pool", ""
    m = BAD_TITLE.search(title)
    if m:
        return "title", m.group(0)
    if LEAD_TITLE.search(title):
        return "title", "lead"
    if NOT_CANADA.search(loc) and not CANADA.search(loc):
        return "location", loc[:60]
    m = FR_REQUIRED.search(jd)
    if m:
        return "fr_required", m.group(0)[:60]
    y = min_years(jd)
    if y >= 7:
        return "years", str(y)
    s = max_salary(jd)
    if s is not None and s < 80000:
        return "salary", str(int(s))
    _, same_title = applied_match(o.get("company"), title, applied)
    if same_title:
        return "applied_same_title", o.get("company") or ""
    return None, None


def score_rules(o, stack_terms):
    t, loc, jd = o.get("title") or "", clean_location(o.get("location")), (o.get("jd") or "").lower()
    s = 0.0
    if ai_signal(t, jd):
        s += 3
    if BANK.search(o.get("company") or ""):
        s -= 6
    s += title_bonus(t)
    s += min(6, sum(1 for k in stack_terms if re.search(r'(?<![a-z])' + re.escape(k.lower()) + r'(?![a-z])', jd)))
    s += location_bonus(loc)
    if min_years(jd) in (5, 6):
        s -= 1
    if re.search(r'\b(senior|sr\.?)\b', t, re.I):
        s -= 1.5
    return round(s, 1)


def score_laya(o, a, stack_terms):
    """Same formula, model answers substituted where the plan says so. → (score, level, notes[], review[], hard)."""
    t, loc, jd = o.get("title") or "", clean_location(o.get("location")), (o.get("jd") or "").lower()
    notes, review = [], []

    def p(q):
        return float((a.get(q) or {}).get("p") or 0)
    hard = None
    for q in HARD_NO:
        row = a.get(q) or {}
        if not row:
            continue
        if row.get("choice") == "yes" and p(q) >= HARD_P:
            hard = f"{q}:{p(q):.2f}"
            break
        if p(q) < REVIEW_P:
            review.append(q)
    s = 0.0
    ai_core = a.get("ai_core") or {}
    is_ai = (ai_core.get("choice") == "yes") if ai_core and p("ai_core") >= REVIEW_P else bool(ai_signal(t, jd))
    if ai_core and p("ai_core") < REVIEW_P:
        review.append("ai_core")
    if is_ai:
        s += 3
        notes.append("ai")
    bank = BANK.search(o.get("company") or "") or ((a.get("company_type") or {}).get("choice") == "bank" and p("company_type") >= REVIEW_P)
    if bank:
        s -= 6
        notes.append("bank:-6")
    s += title_bonus(t)
    sf = a.get("stack_fit") or {}
    if sf and sf.get("score") is not None:
        fit = float(sf["score"])
        s += round(max(0.0, min(4.0, fit - 1)) * 1.5, 1)
        if p("stack_fit") < REVIEW_P:
            review.append("stack_fit")
    else:
        s += min(6, sum(1 for k in stack_terms if re.search(r'(?<![a-z])' + re.escape(k.lower()) + r'(?![a-z])', jd)))
    s += location_bonus(loc)
    if min_years(jd) in (5, 6):
        s -= 1
    lv = a.get("level") or {}
    level = lv.get("choice") if lv else None
    if lv and p("level") < REVIEW_P:
        review.append("level")
        level = None
    if level is None:
        level = "senior" if re.search(r'\b(senior|sr\.?)\b', t, re.I) else ("junior" if re.search(r'\b(junior|jr\.?)\b', t, re.I) else "intermediate")
    if level == "senior":
        s -= 1.5
    if level == "staff_plus":
        hard = hard or "staff_plus"
    return round(s, 1), level, notes, review, hard


SHADOW_D_SAMPLE = 200   # shadow mode: every rules-A/B/C posting plus this many rules-D ones


def rules_row(o, stack_terms):
    """The rules engine's verdict for one posting: (score, level, notes)."""
    sr = score_rules(o, stack_terms)
    t = o.get("title") or ""
    level = ("senior" if re.search(r'\b(senior|sr\.?)\b', t, re.I) else "junior" if re.search(r'\b(junior|jr\.?)\b', t, re.I)
             else "intermediate" if re.search(r'\b(intermediate|mid[- ]level)\b', t, re.I) else "unspecified")
    notes = []
    if ai_signal(t, (o.get("jd") or "").lower()):
        notes.append("ai")
    if BANK.search(o.get("company") or ""):
        notes.append("bank:-6")
    return sr, level, notes


def run_pass(args, log):
    data, run_dir = Path(args.data).expanduser(), Path(args.data).expanduser() / "data" / "search" / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    caps = sorted(run_dir.glob("capture*.jsonl")) + [Path(p).expanduser() for p in (args.capture or [])]
    recs, bad = load_capture(caps)
    applied = load_applied(data)
    pool_urls = SEEN.load_pool_urls(data) | load_queue_urls(data)
    overrides = load_overrides(data)
    stack = candidate_stack(data)
    stack_terms = TECH
    answers_override = {}
    if args.answers:
        answers_override = json.load(open(Path(args.answers).expanduser(), encoding="utf-8"))
    use_model = args.engine == "laya" or args.shadow
    model = Model(data, run_dir, enabled=use_model, log=log)

    kept, dropped, survivors = [], [], {}
    for key, o in recs.items():
        reason, detail = hard_filter(o, applied, pool_urls)
        base = dict(company=o.get("company") or "", title=o.get("title") or "", url=o.get("url") or "", keyword=o.get("keyword") or "",
                    source=o["source"], id=o["id"])
        if reason:
            dropped.append(dict(reason=reason, detail=detail, **base))
            continue
        survivors[key] = o

    # which postings the model sees: all of them as the primary engine; in shadow mode the rules
    # A/B/C set plus a fixed sample of D (enough signal for the agreement report, bounded cost)
    rules_by_key = {k: rules_row(o, stack_terms) for k, o in survivors.items()}
    if args.engine == "laya":
        model_keys = list(survivors)
    elif args.shadow:
        model_keys = [k for k, (sr, _, _) in rules_by_key.items() if tier_of(sr) != "D"]
        d_keys = sorted(k for k, (sr, _, _) in rules_by_key.items() if tier_of(sr) == "D")
        model_keys += d_keys[:SHADOW_D_SAMPLE]
    else:
        model_keys = []
    hashes = {k: jd_hash(survivors[k], stack) for k in model_keys}
    answers = model.answers({hashes[k]: survivors[k] for k in model_keys}, stack) if model_keys and (model.ready or model.cache) else {}

    by_model, review_rows = 0, []
    for key, o in survivors.items():
        a = dict(answers.get(hashes.get(key, "")) or {})
        judged = answers_override.get(o["id"]) or answers_override.get(key) or {}
        for q, v in judged.items():   # the judge's answers replace the model's, at full confidence
            if q in QUESTIONS:
                if QUESTIONS[q]["type"] == "score":
                    a[q] = {"type": "score", "score": float(v), "p": 1.0}
                elif isinstance(v, bool):
                    a[q] = {"type": "choice", "choice": "yes" if v else "no", "p": 1.0}
                else:
                    a[q] = {"type": "choice", "choice": str(v), "p": 1.0}
        sr, r_level, r_notes = rules_by_key[key]
        row = dict(company=o.get("company") or "", title=o.get("title") or "", location=clean_location(o.get("location")),
                   apply_type=o.get("apply_type") or "unknown", years_req=min_years(o.get("jd") or "") or "",
                   salary=(int(max_salary(o.get("jd") or "")) if max_salary(o.get("jd") or "") is not None else ""),
                   applied_company=False, source=o["source"], keyword=o.get("keyword") or "", url=o.get("url") or "",
                   apply_url=o.get("apply_url") or o.get("url") or "", tier_rules=tier_of(sr), score_rules=sr,
                   tier_laya="", score_laya="", laya_level="", laya_ai="", laya_fit="", laya_hard="", laya_review="",
                   company_type="", by="rules", notes=[], id=o["id"], _key=key)
        laya = None
        if a:
            s, level, notes, review, hard = score_laya(o, a, stack_terms)
            laya = dict(score=s, level=level, notes=notes, review=review, hard=hard)
            row.update(tier_laya="D" if hard else tier_of(s), score_laya=s, laya_level=level, laya_ai=("ai" in notes),
                       laya_fit=(a.get("stack_fit") or {}).get("score", ""), laya_hard=hard or "", laya_review=",".join(review),
                       company_type=(a.get("company_type") or {}).get("choice", ""))
            by_model += 1
        if args.engine == "laya" and laya:
            row.update(score=laya["score"], tier=row["tier_laya"], level=laya["level"], ai=row["laya_ai"], notes=list(laya["notes"]),
                       by="laya+judge" if judged else "laya")
            if laya["hard"]:
                row["notes"].append("hard:" + laya["hard"])
            if laya["review"] and not judged:
                row["notes"].append("review:" + ",".join(laya["review"]))
                stripped, _ = strip_boilerplate(o.get("jd") or "")
                review_rows.append({"id": o["id"], "key": key, "title": row["title"], "company": row["company"], "url": row["url"],
                                    "questions": {q: QUESTIONS[q]["instructions"] for q in laya["review"]},
                                    "model": {q: a.get(q) for q in laya["review"]}, "jd": stripped[:6000]})
        else:
            row.update(score=sr, tier=tier_of(sr), level=r_level, ai=("ai" in r_notes), notes=list(r_notes), by="rules")
        m, _ = applied_match(row["company"], row["title"], applied)
        if m:
            row["applied_company"] = True
            row["score"] = round(row["score"] - 6, 1)
            if row["tier"] != "D":
                row["tier"] = tier_of(row["score"])
            row["notes"].append(f"applied_company:{m}")
        kept.append(row)

    kept.sort(key=lambda r: (-r["score"], r["company"].lower(), r["title"].lower(), r["id"]))
    cnt = collections.Counter()
    for r in kept:
        if r["tier"] == "D":
            continue
        k = norm_company(r["company"])[0]
        if cnt[k] >= COMPANY_CAP:
            r["tier"] = "D"
            r["notes"].append("company_cap")
        else:
            cnt[k] += 1
    for r in kept:
        ov = overrides.get(r["id"])
        if ov:
            r["tier"] = ov[0]
            r["notes"].append("user_override:" + ov[0] + (" " + ov[1] if ov[1] else ""))
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    kept.sort(key=lambda r: (order[r["tier"]], -r["score"], r["company"].lower(), r["title"].lower(), r["id"]))
    dropped.sort(key=lambda r: (r["reason"], r["company"].lower(), r["title"].lower(), r["url"]))
    for r in kept:
        r["notes"] = " ".join(r["notes"])

    atomic_csv(run_dir / "ranked.csv", RANKED_COLS, kept)
    atomic_csv(run_dir / "dropped.csv", DROPPED_COLS, dropped)
    with open(run_dir / "review.jsonl", "w", encoding="utf-8") as f:
        for r in review_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tiers = collections.Counter(r["tier"] for r in kept)
    reasons = collections.Counter(r["reason"] for r in dropped)
    line = (f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(recs)} captured ({bad} bad lines) · engine {args.engine}"
            f" · {by_model} seen by the model{' (shadow)' if args.engine != 'laya' and by_model else ''}"
            f"{'' if model.ready or not use_model else ' · laya unavailable'} · A/B/C/D {tiers['A']}/{tiers['B']}/{tiers['C']}/{tiers['D']}"
            f" · review {len(review_rows)} · dropped " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    (run_dir / "status.txt").write_text(line + "\n", encoding="utf-8")
    log(line)
    return kept, dropped, review_rows


def atomic_csv(path, cols, rows):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


# ---------------------------------------------------------------- queue
def cmd_queue(args, log):
    data, run_dir = Path(args.data).expanduser(), Path(args.data).expanduser() / "data" / "search" / args.run
    ranked = run_dir / "ranked.csv"
    if not ranked.exists():
        print("ERR NO_RESULTS ranked.csv missing — run `classify.py rank` first")
        return 1
    tiers = {t.strip().upper() for t in args.tiers.split(",") if t.strip()}
    existing = SEEN.load_pool_urls(data) | load_queue_urls(data)
    rows = [r for r in csv.DictReader(open(ranked, newline="", encoding="utf-8")) if r["tier"] in tiers]
    queue = data / "queue.txt"
    appended, by_kw = 0, collections.Counter()
    with open(queue, "a", encoding="utf-8") as f:
        if queue.exists() and queue.stat().st_size and not open(queue, "rb").read()[-1:] == b"\n":
            f.write("\n")   # never glue onto a line another writer left unterminated
        for r in rows:
            url = r["url"]
            c = SEEN.canonical(url) or url
            if url in existing or c in existing:
                continue
            existing.add(url); existing.add(c)
            f.write(json.dumps({"url": url, "title": r["title"][:200], "company": r["company"][:120], "location": r["location"][:120],
                                "level": LEVEL_OUT.get(r.get("level") or "", r.get("level") or "unspecified"), "tier": r["tier"],
                                "score": float(r["score"]) if r["score"] else 0}, ensure_ascii=False) + "\n")
            appended += 1
            by_kw[r.get("keyword") or "?"] += 1
    flags = []
    for fn in run_dir.glob("summary*.json"):  # one per source, written by search.js finish()
        try:
            flags.append(bool(json.load(open(fn)).get("exhausted", False)))
        except (OSError, json.JSONDecodeError):
            pass
    depth = "NO_SUMMARY" if not flags else "EXHAUSTED" if all(flags) else "MORE_AVAILABLE"
    kw = " BY_KEYWORD " + ",".join(f'"{k}"={v}' for k, v in sorted(by_kw.items())) if by_kw else ""
    print(f"OK APPENDED {appended} {depth}{kw}")
    return 0


def cmd_report(args, log):
    """Shadow report: where the model's tier differs from the rules engine's, and how confidently."""
    run_dir = Path(args.data).expanduser() / "data" / "search" / args.run
    rows = list(csv.DictReader(open(run_dir / "ranked.csv", newline="", encoding="utf-8")))
    seen_rows = [r for r in rows if r["tier_laya"]]
    if not seen_rows:
        print("no model-scored rows yet (run `rank` with the server up)")
        return 0
    ab = lambda t: t in ("A", "B")
    agree = sum(1 for r in seen_rows if r["tier_laya"] == r["tier_rules"])
    agree_ab = sum(1 for r in seen_rows if ab(r["tier_laya"]) == ab(r["tier_rules"]))
    hard = collections.Counter(r["laya_hard"].split(":")[0] for r in seen_rows if r["laya_hard"])
    print(f"{len(seen_rows)} postings seen by the model · tier agreement {agree / len(seen_rows):.0%} · A/B-boundary agreement {agree_ab / len(seen_rows):.0%}")
    print("rules→laya  " + "  ".join(f"{a}→{b}:{n}" for (a, b), n in sorted(collections.Counter((r["tier_rules"], r["tier_laya"]) for r in seen_rows).items())))
    print("model hard-nos: " + (", ".join(f"{k}={v}" for k, v in hard.most_common()) or "none")
          + f" · level {dict(collections.Counter(r['laya_level'] for r in seen_rows))}"
          + f" · needs review {sum(1 for r in seen_rows if r['laya_review'])}")
    print("\nA/B-boundary disagreements (rules is the primary engine until this list looks right):")
    for r in seen_rows:
        if ab(r["tier_laya"]) != ab(r["tier_rules"]):
            print(f"  rules {r['tier_rules']}({r['score_rules']})  laya {r['tier_laya']}({r['score_laya']}) {r['laya_hard'] or ''}  "
                  f"{r['company'][:28]} — {r['title'][:46]}")
    return 0


# ---------------------------------------------------------------- export (fine-tuning rows)
INTERN_TITLE = re.compile(r'\b(intern|internship|student|co-?op|new\s*grad|newgrad|campus|graduate\s*(program|programme))\b', re.I)
INTERN_BODY = re.compile(r'\b(internship|co-?op (term|placement|student)|new[- ]grad(uate)?|currently enrolled|work term)\b', re.I)
STAFF_TITLE = re.compile(r'\b(staff|principal|lead|leader|architect|manager|director|head of|vp|vice president)\b', re.I)
CLEARANCE = re.compile(r'\b(security clearance|reliability status|secret clearance|enhanced reliability)\b', re.I)
US_ONLY = re.compile(r'\b(authorized to work in the (united states|u\.?s\.?)|us (citizens?|residents?) only|must (reside|live|be located) in the (united states|u\.?s\.?))\b', re.I)
AI_BAN = re.compile(r'\b(do not use|without the use of|no use of|prohibit\w*)\b.{0,40}\b(ai|chatgpt|generative)\b', re.I)


def weak_labels(o):
    """Rules-derived labels for the choice questions — only where the regexes have real evidence.
    `None` = no label (the row is skipped for that question)."""
    t, jd = o.get("title") or "", o.get("jd") or ""
    lab = {}
    lab["intern"] = "yes" if INTERN_TITLE.search(t) else ("no" if not INTERN_BODY.search(jd) else None)
    lab["clearance"] = "yes" if CLEARANCE.search(jd) else "no"
    lab["french_required"] = "yes" if FR_REQUIRED.search(jd) else ("no" if not re.search(r'french|fran[çc]ais|bilingual', jd, re.I) else None)
    lab["us_only"] = "yes" if US_ONLY.search(jd) else ("no" if CANADA.search(clean_location(o.get("location"))) else None)
    lab["ai_ban"] = "yes" if AI_BAN.search(jd) else "no"
    ai = ai_signal(t, jd.lower())
    lab["ai_core"] = "yes" if ai.startswith("title:") else ("no" if not any(r.search(jd) for r in AI_RE) else None)
    lab["level"] = ("staff_plus" if STAFF_TITLE.search(t) else "senior" if re.search(r'\b(senior|sr\.?)\b', t, re.I)
                    else "junior" if re.search(r'\b(junior|jr\.?|entry[- ]level)\b', t, re.I)
                    else "intermediate" if re.search(r'\b(intermediate|mid[- ]level)\b', t, re.I) else None)
    lab["company_type"] = "bank" if BANK.search(o.get("company") or "") else None
    return {k: v for k, v in lab.items() if v is not None}


def cmd_export(args, log):
    """laya-train rows: one per (posting, choice question) with a label. Labels come from the regex
    rules (weak but plentiful) and from the judge's answers file (strong); the judge wins. A fixed
    fraction of the judged postings (--holdout, every question of theirs) goes to search_eval.jsonl
    instead, so `laya_eval.py eval --rows … --model autoapply-search` measures against labels the
    model never saw. Rules rows are capped per (question, label) (--cap) — the regexes produce
    thousands of easy "no" rows that would swamp the judged ones and make training take a day."""
    import random
    data, run_dir = Path(args.data).expanduser(), Path(args.data).expanduser() / "data" / "search" / args.run
    caps = sorted(run_dir.glob("capture*.jsonl")) + [Path(p).expanduser() for p in (args.capture or [])]
    recs, _ = load_capture(caps)
    judged = json.load(open(Path(args.answers).expanduser(), encoding="utf-8")) if args.answers else {}
    stack = candidate_stack(data)
    out_dir = data / "data" / "laya"
    out_dir.mkdir(parents=True, exist_ok=True)
    judged_keys = sorted(k for k, o in recs.items() if o["id"] in judged or k in judged)
    random.Random(19).shuffle(judged_keys)
    hold = set(judged_keys[:int(len(judged_keys) * args.holdout)])
    n, per_q, by, capped = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    keys = list(recs); random.Random(23).shuffle(keys)  # so the cap keeps a random subset, not the first N captured
    recs = {k: recs[k] for k in keys}
    with open(out_dir / "search_train.jsonl", "w", encoding="utf-8") as ftr, open(out_dir / "search_eval.jsonl", "w", encoding="utf-8") as fev:
        for key, o in recs.items():
            if not (o.get("jd") or "").strip():
                continue
            labels = weak_labels(o)
            ans = judged.get(o["id"]) or judged.get(key) or {}
            for q, v in ans.items():
                if q in QUESTIONS and QUESTIONS[q]["type"] == "choice":
                    labels[q] = ("yes" if v else "no") if isinstance(v, bool) else str(v)
            state = state_for(o, stack)
            f = fev if key in hold else ftr
            for q, v in labels.items():
                if v not in QUESTIONS[q]["criteria"]:
                    continue
                src = "judge" if q in ans else "rules"
                if src == "rules" and key not in hold:
                    if capped[(q, v)] >= args.cap:
                        continue
                    capped[(q, v)] += 1
                f.write(json.dumps({"state": state, "questions": {q: QUESTIONS[q]}, "expected": {q: v},
                                    "meta": {"id": o["id"], "source": o["source"], "q": q, "by": src}}, ensure_ascii=False) + "\n")
                n["eval" if key in hold else "train"] += 1
                if key not in hold:
                    per_q[q] += 1; by[src] += 1
    log(f"wrote {n['train']} train rows ({by['rules']} rules + {by['judge']} judge) to {out_dir}/search_train.jsonl · "
        + ", ".join(f"{q}={c}" for q, c in sorted(per_q.items())))
    log(f"wrote {n['eval']} eval rows from {len(hold)} judged postings to {out_dir}/search_eval.jsonl")
    import laya_client
    log(f"train with: {data}/.venv-laya/bin/laya-train --data {out_dir}/search_train.jsonl --eval {out_dir}/search_eval.jsonl "
        f"--base {laya_client.ML_MODEL} --out {out_dir}/search-checkpoint --max-len 4096 --device mps"
        f"   (restart laya-serve: the checkpoint loads as model {laya_client.ML_FT_MODEL} and classify.py uses it automatically)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["rank", "merge", "queue", "report", "export"])
    ap.add_argument("--data", default="~/job-search")
    ap.add_argument("--run", default=datetime.date.today().isoformat())
    ap.add_argument("--capture", action="append", help="extra capture files (old linkedin_capture.jsonl format accepted)")
    ap.add_argument("--engine", choices=["rules", "laya"], default="rules", help="primary engine (rules until the model is fine-tuned)")
    ap.add_argument("--shadow", dest="shadow", action="store_true", default=True, help="also ask the model and keep its columns (default)")
    ap.add_argument("--no-shadow", dest="shadow", action="store_false", help="rules only; no model server needed")
    ap.add_argument("--answers", help="merge/export: JSON {id: {question: answer}} from the judge")
    ap.add_argument("--holdout", type=float, default=0.2, help="export: fraction of judged postings kept for search_eval.jsonl")
    ap.add_argument("--cap", type=int, default=300, help="export: max rules-labelled rows per (question, label)")
    ap.add_argument("--tiers", default="A,B")
    args = ap.parse_args(argv)
    run_dir = Path(args.data).expanduser() / "data" / "search" / args.run
    run_dir.mkdir(parents=True, exist_ok=True)

    def log(m):
        print(m, file=sys.stderr)
        with open(run_dir / "classify.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().strftime('%H:%M:%S')} {m}\n")
    if args.cmd in ("rank", "merge"):
        if args.cmd == "merge" and not args.answers:
            ap.error("merge needs --answers")
        run_pass(args, log)
        return 0
    if args.cmd == "queue":
        return cmd_queue(args, log)
    if args.cmd == "export":
        return cmd_export(args, log)
    return cmd_report(args, log)


if __name__ == "__main__":
    sys.exit(main())

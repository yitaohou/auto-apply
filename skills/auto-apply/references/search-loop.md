# Search loop — search.js → classify.py → queue.txt

**Status: implemented** (`scripts/search.js`, `scripts/classify.py`, `scripts/seen.py`); the
`job-searcher` agent is a thin shell around the two scripts. Validated against the local
fixtures (`scripts/fixtures/linkedin-search.html`, `indeed-search.html`, `indeed-viewjob.html`);
the live LinkedIn "AI-powered" results UI must be checked with `probe: true` and the user
present before the first full crawl after any site change.

## Why

The old `job-searcher` was asked to *execute* a crawling protocol — walk pages, respect
pacing, maintain a seen set, rank by a dozen rules — and the rules file recorded eleven
distinct ways it failed (false `EXHAUSTED`, ignored pacing → two-day LinkedIn ban, two agents
resetting each other's `$TMPDIR` seen file). Crawling and ranking are now code; the agent
launches, waits, relays.

## Files — `~/job-search/data/search/<run>/`

Every file is suffixed with the source so LinkedIn and Indeed can run side by side.

| File | Writer | Content |
|---|---|---|
| `capture-<source>.jsonl` | search.js | one record per posting: `source,id,url,title,company,location,posted,keyword,search_location,apply_type (easy_apply/external/indeed/unknown),apply_url,closed,jd (≤14 000 chars),captured_at`, `pay` on Indeed. Append-only; `source:id` is the dedupe key and is preloaded on resume |
| `state-<source>.json` | search.js | per keyword (LinkedIn) or `keyword|location` (Indeed): `{next, thin/empty, done, blocked?, pagination?}` — a rerun with the same `run` resumes here |
| `search-<source>.log` | search.js | progress; the last line is `DONE …`, `STOPPED <reason> …`, or `CRASH: …` |
| `summary-<source>.json` | search.js | `{source, run, captured, pages, keywords: {kw: {captured, pages, exhausted, next}}, stopped, exhausted}` |
| `gen-<source>.lock` | search.js | generation token — a newer launch makes the older instance exit (`superseded`) |
| `probe-<source>.json` | search.js | output of `probe: true` |
| `ranked.csv`, `dropped.csv`, `status.txt`, `classify.log` | classify.py | the ranking (below) |
| `laya.jsonl` | classify.py | cached model answers keyed by `id + JD hash`; reruns are free |
| `review.jsonl`, `review_answers.json` | classify.py / skill | judge round, only under `--engine laya` |
| `seen.txt` | seen.py CLI | per-run seen set for ad-hoc `seen.py check` calls (replaces the old `$TMPDIR` file); classify.py dedupes on its own through `seen.canonical` |

`~/job-search/data/LINKEDIN_STOP` (outside the run dir) is the fleet stop: search.js writes
it on the first security challenge / auth wall and every LinkedIn instance exits at its next
check. The user deletes it; nothing retries on its own.

## search.js

```
ego-browser nodejs <<'EOF'
const m = await import('PLUGIN/scripts/search.js');
await m.run({ source: 'linkedin', keywords: ['software engineer', 'full stack developer'], hours: 168,
              data: '/Users/me/job-search', run: '2026-10-14', space: 'aa-search-2026-10-14-linkedin' });
EOF
```

Options: `source` linkedin|indeed · `keywords` · `data` · `run` · `hours` (168) · `locations`
(Indeed `l=` values; default = 12 Canadian metros) · `geoId` (LinkedIn, default Canada) ·
`maxPages` per keyword / pair (40 / 10) · `maxResults` (0 = no cap) · `probe` · `pace` ·
`fixtures` (tests only).

Built in, not configurable from the call: one task space, one tab on the source host
(extra tabs are closed); LinkedIn 15 s between pages, 2 s between cards, 10 min rest every
30 min, `start=` up to 975; Indeed 18 s between pages, 10 s between job pages, 5 min rest
every 30 min, `start=` up to 300; a LinkedIn page with 0 rows cools 90 s and retries once;
a keyword ends after two consecutive pages under 5 rows (LinkedIn) or two pages with no
unseen card (Indeed); Indeed structural block (no cards and a body under 400 chars) cools
30 s × 3 then parks the pair; a blocked Indeed job page backs off 75 s once.

LinkedIn facts the script relies on (2026-10): results at
`/jobs/search-results/?keywords=&geoId=&f_TPR=r<seconds>&start=`; cards are
`div[role="button"][componentkey^="job-card-component-ref-<id>"]` (classic
`a[href*="/jobs/view/"]` as fallback); the pane identifies itself by `currentJobId` in the
URL, and the record is what the pane shows — card text is only a fallback, because the
virtualised list recycles nodes; JD = `[id^="JobDetails_AboutTheJob"]` or the body slice
"About the job" → "Set alert for similar jobs" / "More jobs" / "People also viewed";
apply type from the visible button right of the list (`on company website` → external,
`Easy Apply` → easy_apply), `.artdeco-pill` excluded. Pagination is auto-detected: if two
consecutive `start=` offsets render the same id set, the next-page control is tried once;
if the list still does not change the keyword ends (`state.pagination = none`).

Indeed facts: `ca.indeed.com/jobs?q=&l=&fromage=<days>&start=`, cards `[data-jk]` with
one zero-size phantom per page (ignored), job page `[data-testid="viewjob-main-content"]`,
expired pages recorded with `closed: true` so classify drops them.

`probe: true` loads the first page of the first keyword and writes `probe-<source>.json`:
UI variant, card count, a three-card sample, the pane for the first card (does its id match
the card?), and for LinkedIn whether `start=25` changes the list and which next control
exists. Run it with the user present after any site change.

## classify.py

```
python3 PLUGIN/scripts/classify.py rank  --data ~/job-search --run 2026-10-14 [--no-shadow] [--engine rules|laya]
python3 PLUGIN/scripts/classify.py queue --data ~/job-search --run 2026-10-14 --tiers A,B
python3 PLUGIN/scripts/classify.py report --data ~/job-search --run 2026-10-14
python3 PLUGIN/scripts/classify.py export --data ~/job-search --run 2026-10-14 [--answers review_answers.json]
```

Per posting: **hard filters** in code — closed, no JD, already in `job_pool.csv` / `queue.txt`
(canonical URL), excluded title (intern / co-op / new grad / manager / architect / staff /
principal / director / VP / lead), outside Canada, French required, ≥ 7 years, salary
< $80k, same title at a company already applied to — → `dropped.csv` with the reason.
Then the **rules engine** (the user's ranking rules: AI roles +3, candidate-stack hits up
to +6 for TypeScript/JavaScript/React/Next/Angular/Python/Django/AWS/LLM stack only,
banks −6, applied company −6, senior −1.5, 5–6 years −1, Toronto/GTA/remote +3,
Ontario/Vancouver +1.5, Calgary +0.75; two postings per company among A–C; overrides from
`search_overrides.csv`) → score → tier A ≥ 12, B ≥ 9, C ≥ 6, else D.

In the **shadow** (default on; `--no-shadow` to skip), `laya-multilingual` reads the
boilerplate-stripped JD once per posting — title, company, location, the candidate stack
line, then the JD with EEO / benefits / about-us sections cut by heading — and answers:
intern? clearance? French required? US only? AI-assisted applications banned? AI core to the
role? (yes/no), level (junior / intermediate / senior / staff+), company type (bank /
agency / product / other), stack fit 1–5. Its tier and score are kept beside the rules'
(`tier_laya`, `score_laya`, `laya_level`, `laya_ai`, `laya_fit`, `laya_hard`,
`laya_review`, `company_type`) for every non-D posting plus a 200-row D sample; every
answer is cached in `laya.jsonl`. The server is started on demand through
`laya_client.py`; when it is unavailable the pass is rules-only and says so.

**Zero-shot the model is not usable as the primary** (2026-10-14, 20 real JDs: every yes/no
hard flag answered "yes" at P ≈ 0.75–0.97 regardless of context length; level = senior for
nearly all). `--engine laya` exists for after fine-tuning: it ranks by the model, keeps the
rules as the shadow, and writes `review.jsonl` for postings whose tier hinges on an answer
under P 0.70 — the skill answers those as a judge (`review_answers.json`) and runs
`merge`. `report` prints tier agreement and the A/B-boundary disagreements; switch engines
only when that list looks right to the user.

**Fine-tuning path:** `export` writes `data/laya/search_train.jsonl` — one laya-train row
per (posting, choice question) with a label from the regex rules where they have real
evidence (title says intern, JD says bilingual required, …) and from the judge's answers
where present (judge wins; `--answers` takes the same `{id: {question: answer}}` file as
`merge`). Rules rows are capped per (question, label) (`--cap 300`) so the easy "no"s do
not swamp the judged ones; `--holdout 0.2` of the judged postings go to
`search_eval.jsonl` instead. Train with the command `export` prints
(`laya-train … --base multilingual --out data/laya/search-checkpoint --max-len 4096`),
restart the server — the checkpoint loads as `autoapply-search` and `rank` uses it
automatically — then score it: `laya_eval.py eval --rows data/laya/search_eval.jsonl
--model autoapply-search --max-len 4096`. Zero-shot on that holdout (2026-10-19, 60 JDs)
the hard flags agree 15–30 % (everything "yes"), level 53 %, company type 57 %.

`queue` appends `{"url","title","company","location","level","tier","score"}` lines for
the chosen tiers, deduped against `job_pool.csv` and the existing queue (a trailing
unterminated line is left alone), and prints
`OK APPENDED <n> EXHAUSTED|MORE_AVAILABLE|NO_SUMMARY BY_KEYWORD "kw"=n,…` — the depth
flag comes from the `summary-*.json` files of the run.

## The agent

`agents/job-searcher.md`: parse the six fields and `sources=`, launch `search.js` once per
source in the background, wait for `DONE | STOPPED | CRASH` in each log, run `rank` then
`queue --tiers A,B`, relay the one line. It never opens a results page, reads a JD, or
touches `queue.txt` / `job_pool.csv` directly.

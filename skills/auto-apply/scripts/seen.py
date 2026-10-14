#!/usr/bin/env python3
"""Deduplication and URL canonicalisation for the search side (classify.py imports it).

Reads only the job_url column of job_pool.csv. No other column is ever loaded.

    seen.py [--data DIR] [--run ID] init            rebuild the seen set from job_pool.csv; prints the count
    seen.py canonical <url>                          print the canonical posting URL; exit 1 if unrecognised
    seen.py [--data DIR] [--run ID] check <url>      exit 0 if new (and record it), exit 1 if already seen
    seen.py [--data DIR] [--run ID] cleanup          remove the state file

The state file is scoped to a run: `<data>/data/search/<run>/seen.txt` (default run = today's
date). Earlier versions kept one file in $TMPDIR, which every concurrent agent re-initialised —
two searches in flight reset each other's seen set and re-queued the same postings.
"""
import csv
import os
import re
import sys
from datetime import date
from urllib.parse import parse_qs, urlparse

DATA = os.path.expanduser("~/job-search")

LINKEDIN_ID = re.compile(r"^\d+$")
INDEED_ID = re.compile(r"^[0-9a-f]{10,20}$")


def pool_path(data=DATA):
    return os.path.join(os.path.expanduser(data), "data", "job_pool.csv")


def state_path(data=DATA, run=None):
    run = run or date.today().isoformat()
    d = os.path.join(os.path.expanduser(data), "data", "search", str(run))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "seen.txt")


def canonical(raw):
    """Reduce a browser address bar URL to the posting's stable address."""
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    query = parse_qs(parsed.query)

    if "linkedin.com" in host:
        for key in ("currentJobId", "jobId"):
            for value in query.get(key, []):
                if LINKEDIN_ID.match(value):
                    return "https://www.linkedin.com/jobs/view/" + value
        match = re.search(r"/jobs/view/(\d+)", parsed.path)
        if match:
            return "https://www.linkedin.com/jobs/view/" + match.group(1)
        return None

    if "indeed." in host:
        for key in ("vjk", "jk"):
            for value in query.get(key, []):
                if INDEED_ID.match(value):
                    return "https://ca.indeed.com/viewjob?jk=" + value
        return None

    if parsed.scheme == "https" and parsed.netloc:
        return parsed.scheme + "://" + parsed.netloc + parsed.path
    return None


def load_pool_urls(data=DATA):
    """Canonical job_url values from job_pool.csv (raw value kept too, for exact matches)."""
    urls = set()
    pool = pool_path(data)
    if not os.path.exists(pool):
        return urls
    with open(pool, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get("job_url") or "").strip()
            if url:
                urls.add(url)
                c = canonical(url)
                if c:
                    urls.add(c)
    return urls


def read_state(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def main(argv):
    args = list(argv[1:])
    data, run = DATA, None
    while args and args[0] in ("--data", "--run"):
        flag = args.pop(0)
        if not args:
            print(f"{flag} needs a value", file=sys.stderr)
            return 2
        if flag == "--data":
            data = args.pop(0)
        else:
            run = args.pop(0)
    cmd = args[0] if args else ""

    if cmd == "init":
        urls = load_pool_urls(data)
        with open(state_path(data, run), "w", encoding="utf-8") as f:
            for url in sorted(urls):
                f.write(url + "\n")
        print(len(urls))
        return 0

    if cmd == "canonical":
        if len(args) < 2:
            print("canonical requires a URL", file=sys.stderr)
            return 2
        result = canonical(args[1].strip())
        if not result:
            print("unrecognised URL", file=sys.stderr)
            return 1
        print(result)
        return 0

    if cmd == "check":
        if len(args) < 2:
            print("check requires a URL", file=sys.stderr)
            return 2
        url = args[1].strip()
        path = state_path(data, run)
        seen = read_state(path)
        if url in seen or (canonical(url) or url) in seen:
            return 1
        with open(path, "a", encoding="utf-8") as f:
            f.write((canonical(url) or url) + "\n")
        return 0

    if cmd == "cleanup":
        path = state_path(data, run)
        if os.path.exists(path):
            os.remove(path)
        return 0

    print("usage: seen.py [--data DIR] [--run ID] init | canonical <url> | check <url> | cleanup",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

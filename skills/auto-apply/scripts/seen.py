#!/usr/bin/env python3
"""Deduplication and URL canonicalisation for the job-searcher agent.

Reads only the job_url column of job_pool.csv. No other column is ever loaded.

    seen.py init             rebuild the seen set from job_pool.csv; prints the count
    seen.py canonical <url>  print the canonical posting URL; exit 1 if unrecognised
    seen.py check <url>      exit 0 if new (and record it), exit 1 if already seen
    seen.py cleanup          remove the state file
"""
import csv
import os
import re
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

POOL = os.path.expanduser("~/job-search/data/job_pool.csv")
STATE = os.path.join(tempfile.gettempdir(), "job-searcher-seen.txt")

LINKEDIN_ID = re.compile(r"^\d+$")
INDEED_ID = re.compile(r"^[0-9a-f]{10,20}$")


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


def load_pool_urls():
    urls = set()
    if not os.path.exists(POOL):
        return urls
    with open(POOL, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get("job_url") or "").strip()
            if url:
                urls.add(url)
    return urls


def read_state():
    if not os.path.exists(STATE):
        return set()
    with open(STATE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""

    if cmd == "init":
        urls = load_pool_urls()
        with open(STATE, "w", encoding="utf-8") as f:
            for url in sorted(urls):
                f.write(url + "\n")
        print(len(urls))
        return 0

    if cmd == "canonical":
        if len(argv) < 3:
            print("canonical requires a URL", file=sys.stderr)
            return 2
        result = canonical(argv[2].strip())
        if not result:
            print("unrecognised URL", file=sys.stderr)
            return 1
        print(result)
        return 0

    if cmd == "check":
        if len(argv) < 3:
            print("check requires a URL", file=sys.stderr)
            return 2
        url = argv[2].strip()
        if url in read_state():
            return 1
        with open(STATE, "a", encoding="utf-8") as f:
            f.write(url + "\n")
        return 0

    if cmd == "cleanup":
        if os.path.exists(STATE):
            os.remove(STATE)
        return 0

    print("usage: seen.py init | canonical <url> | check <url> | cleanup",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

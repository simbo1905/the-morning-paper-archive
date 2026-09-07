#!/usr/bin/env -S uv -S python3
"""Test one-shot Tavily search for 10 missing papers.

For each paper:
  1. One Tavily search (title + lead author + year + venue)
  2. Pick best PDF URL from results
  3. Download it
  4. Sanity check: pdftotext produces non-junk text

Reports: X out of 10 found with a one-shot search.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

# Load Tavily key
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
if not TAVILY_API_KEY:
    for line in (BASE_DIR / ".env").read_text().splitlines():
        if line.startswith("TAVILY_API_KEY="):
            TAVILY_API_KEY = line.split("=", 1)[1].strip()

TAVILY_URL = "https://api.tavily.com/search"
PAPERS_DIR = BASE_DIR / "papers"
SKIP_DOMAINS = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets",
                "simbo1905", "bbc.co.uk", "youtube.com", "twitter.com", "x.com",
                "amazon.com", "wikipedia.org"]


def tavily_search(query, max_results=10):
    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
    }).encode()
    req = urllib.request.Request(TAVILY_URL, data=payload, headers={
        "Content-Type": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def pick_best_url(results):
    """Pick the best PDF URL from Tavily results."""
    for r in results.get("results", []):
        url = r.get("url", "")
        if not url or any(d in url for d in SKIP_DOMAINS):
            continue
        if url.lower().endswith(".pdf"):
            return url
    # Fallback: arxiv abs -> pdf
    for r in results.get("results", []):
        url = r.get("url", "")
        if "arxiv.org/abs/" in url:
            return url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"
    # Fallback: known repos
    for r in results.get("results", []):
        url = r.get("url", "")
        if any(d in url for d in ["usenix.org", "cidrdb.org", "vldb.org",
                                   "semanticscholar.org", "hal.science"]):
            return url
    return None


def download(url, dest, timeout=30):
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
            if len(data) < 1000:
                return False, "too small"
            if dest.suffix == ".pdf" and data[:5] != b"%PDF-":
                return False, "not a PDF"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True, f"{len(data)} bytes"
    except Exception as e:
        return False, str(e)


def sanity_check(pdf_path):
    """Run pdftotext and check the output is not junk."""
    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/pdftotext", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=15
        )
        text = result.stdout.strip()
        if len(text) < 100:
            return False, f"too short ({len(text)} chars)"
        # Check it's not just garbage characters
        alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
        if alpha_ratio < 0.3:
            return False, f"low alpha ratio ({alpha_ratio:.2f})"
        return True, f"{len(text)} chars, alpha={alpha_ratio:.2f}"
    except Exception as e:
        return False, str(e)


def main():
    # Read first 10 missing papers
    records = []
    for line in (BASE_DIR / "papers_not_found.txt").read_text().splitlines()[:10]:
        if line.strip():
            records.append(json.loads(line))

    print(f"Testing {len(records)} papers with one-shot Tavily search")
    print()

    found = 0
    not_found = 0

    for i, rec in enumerate(records):
        slug = rec["blog_slug"]
        title = rec.get("paper_title", "")
        authors = rec.get("paper_authors", [])
        year = rec.get("paper_year", "")
        venue = rec.get("paper_venue", "")
        date_path = rec.get("blog_date", "")

        author_str = " ".join(authors[:2]) if isinstance(authors, list) and authors else ""
        query = f"{title} {author_str} {year} {venue} pdf".strip()

        print(f"  [{i}] {slug[:50]} ...", end=" ", flush=True)
        t0 = time.monotonic()

        # Search
        try:
            result = tavily_search(query, max_results=10)
        except Exception as e:
            print(f"SEARCH ERROR: {e}")
            not_found += 1
            continue

        url = pick_best_url(result)
        if not url:
            print("NO URL FOUND")
            not_found += 1
            continue

        # Download
        parts = date_path.split("/")
        folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
        if url.lower().endswith(".pdf"):
            dest = folder / f"{slug}.pdf"
        else:
            dest = folder / f"{slug}.html"

        ok, msg = download(url, dest)
        if not ok:
            print(f"DOWNLOAD FAIL: {msg}")
            not_found += 1
            continue

        # Sanity check
        if dest.suffix == ".pdf":
            ok_check, check_msg = sanity_check(dest)
            if ok_check:
                found += 1
                print(f"OK ({time.monotonic()-t0:.1f}s) {dest.name} [{check_msg}]")
            else:
                print(f"JUNK PDF ({check_msg})")
                dest.unlink(missing_ok=True)
                not_found += 1
        else:
            found += 1
            print(f"OK-HTML ({time.monotonic()-t0:.1f}s) {dest.name}")

    print()
    print("=" * 60)
    print(f"Result: {found}/{len(records)} found with one-shot Tavily search")
    print(f"  Found: {found}")
    print(f"  Not found: {not_found}")
    print("=" * 60)


if __name__ == "__main__":
    main()

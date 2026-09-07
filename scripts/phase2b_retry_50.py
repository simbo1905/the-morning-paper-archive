#!/usr/bin/env -S uv -S python3
"""Phase 2b: Second-pass Tavily search for 50 still-missing papers.

For each paper in papers_still_missing.txt:
  1. Try multiple Tavily queries (preprint, homepage, author, exclude paywalled)
  2. Pick best PDF URL from results across all queries
  3. Download to papers/YYYY/MM/DD/slug.pdf
  4. Sanity check: pdftotext produces non-junk text
  5. Still not found -> papers_final_missing.txt

Resume: skips papers already downloaded.

Usage:
  python3 scripts/phase2b_retry_50.py
"""
from __future__ import annotations

import json
import os
import re
import ssl
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

if not TAVILY_API_KEY:
    print("ERROR: TAVILY_API_KEY not found", file=sys.stderr)
    sys.exit(1)

TAVILY_URL = "https://api.tavily.com/search"
PAPERS_DIR = BASE_DIR / "papers"
INPUT_FILE = BASE_DIR / "papers_still_missing.txt"
FINAL_MISSING_FILE = BASE_DIR / "papers_final_missing.txt"
PROGRESS_FILE = BASE_DIR / "phase2b_progress.json"
TIMINGS_FILE = BASE_DIR / ".tmp" / "bench" / "phase2b_timings.jsonl"
TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

PDF_EXTENSIONS = [".pdf", ".html", ".docx", ".ps", ".doc"]
SKIP_DOMAINS = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets",
                "simbo1905", "bbc.co.uk", "youtube.com", "twitter.com", "x.com",
                "amazon.com", "wikipedia.org"]
PAYWALL_DOMAINS = ["dl.acm.org", "ieeexplore.ieee.org", "sciencedirect.com",
                   "springer.com", "link.springer.com", "wiley.com",
                   "onlinelibrary.wiley.com", "tandfonline.com"]

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


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
    # Priority 1: direct .pdf URLs (not paywalled)
    for r in results.get("results", []):
        url = r.get("url", "")
        if not url or any(d in url for d in SKIP_DOMAINS):
            continue
        if any(d in url for d in PAYWALL_DOMAINS):
            continue
        if url.lower().endswith(".pdf"):
            return url
    # Priority 2: arxiv abs -> pdf
    for r in results.get("results", []):
        url = r.get("url", "")
        if "arxiv.org/abs/" in url:
            return url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"
    # Priority 3: known open-access repos
    for r in results.get("results", []):
        url = r.get("url", "")
        if any(d in url for d in ["usenix.org", "cidrdb.org", "vldb.org",
                                   "semanticscholar.org", "hal.science",
                                   "sigops.org", "researchgate.net",
                                   "drops.dagstuhl.de", "jmlr.org",
                                   "proceedings.mlr.press", "openreview.net",
                                   "arxiv.org"]):
            return url
    # Priority 4: any non-paywalled URL that looks like a paper
    for r in results.get("results", []):
        url = r.get("url", "")
        if not url or any(d in url for d in SKIP_DOMAINS):
            continue
        if any(d in url for d in PAYWALL_DOMAINS):
            continue
        if any(ext in url.lower() for ext in [".pdf", ".ps", ".doc", ".html"]):
            return url
    return None


def build_queries(rec):
    """Build multiple search queries for a paper."""
    title = rec.get("paper_title", "")
    authors = rec.get("paper_authors", [])
    year = rec.get("paper_year", "")
    venue = rec.get("paper_venue", "")

    lead_author = ""
    if isinstance(authors, list) and authors:
        lead_author = authors[0]
    elif isinstance(authors, str) and authors:
        lead_author = authors

    queries = []

    # Query 1: title + author + preprint pdf
    q1 = f'"{title}" {lead_author} preprint pdf'
    queries.append(q1)

    # Query 2: title + author + filetype:pdf -paywalled
    q2 = f'"{title}" {lead_author} filetype:pdf'
    queries.append(q2)

    # Query 3: title + year + venue + pdf (no quotes for broader match)
    q3 = f'{title} {lead_author} {year} {venue} pdf'
    queries.append(q3)

    # Query 4: title + "full text" OR "preprint" OR "draft"
    q4 = f'"{title}" {lead_author} "full text" OR "preprint" OR "draft"'
    queries.append(q4)

    # Query 5: title + author homepage/edu
    q5 = f'"{title}" {lead_author} site:edu OR site:ac OR "home page"'
    queries.append(q5)

    return queries


def download(url, dest, timeout=30):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            data = resp.read()
            if len(data) < 1000:
                return False, "too small"
            if dest.suffix == ".pdf" and data[:5] != b"%PDF-":
                # Maybe it's HTML redirect or something
                if b"<html" in data[:500].lower():
                    # Save as HTML
                    dest_html = dest.with_suffix(".html")
                    dest_html.parent.mkdir(parents=True, exist_ok=True)
                    dest_html.write_bytes(data)
                    return True, f"html {len(data)} bytes"
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
        alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
        if alpha_ratio < 0.3:
            return False, f"low alpha ratio ({alpha_ratio:.2f})"
        return True, f"{len(text)} chars"
    except Exception as e:
        return False, str(e)


def check_existing(rec):
    parts = rec.get("blog_date", "").split("/")
    if len(parts) != 3:
        return None
    folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
    slug = rec["blog_slug"]
    for ext in PDF_EXTENSIONS:
        p = folder / f"{slug}{ext}"
        if p.exists() and p.stat().st_size > 1000:
            return p
    return None


def main():
    records = []
    for line in INPUT_FILE.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))

    total = len(records)
    print(f"Phase 2b: Second-pass Tavily search for {total} missing papers")
    print(f"Strategy: up to 5 queries per paper (preprint, homepage, author, exclude paywalled)")
    print()

    found_count = 0
    not_found_count = 0
    already_count = 0

    FINAL_MISSING_FILE.unlink(missing_ok=True)

    for i, rec in enumerate(records):
        slug = rec["blog_slug"]

        # Skip if already downloaded
        existing = check_existing(rec)
        if existing:
            already_count += 1
            print(f"  [{i+1}/{total}] {slug[:50]} ... ALREADY HAVE")
            continue

        title = rec.get("paper_title", "")
        date_path = rec.get("blog_date", "")
        parts = date_path.split("/")
        if len(parts) != 3:
            not_found_count += 1
            with open(FINAL_MISSING_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue

        folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
        queries = build_queries(rec)

        print(f"  [{i+1}/{total}] {slug[:50]} ...", end=" ", flush=True)
        t0 = time.monotonic()

        found_url = None
        queries_tried = 0

        for qi, query in enumerate(queries):
            queries_tried += 1
            try:
                result = tavily_search(query, max_results=10)
            except Exception as e:
                print(f"Q{qi+1} ERR ", end="", flush=True)
                time.sleep(1)
                continue

            url = pick_best_url(result)
            if url:
                found_url = url
                print(f"Q{qi+1} HIT ", end="", flush=True)
                break
            else:
                print(f"Q{qi+1} miss ", end="", flush=True)
            time.sleep(0.5)

        wall = time.monotonic() - t0

        if not found_url:
            print(f"NOT FOUND ({wall:.1f}s, {queries_tried} queries)")
            not_found_count += 1
            with open(FINAL_MISSING_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            with open(TIMINGS_FILE, "a") as f:
                f.write(json.dumps({"index": i, "blog_slug": slug, "status": "no_url",
                                    "queries_tried": queries_tried,
                                    "wall_seconds": round(wall, 2),
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
            continue

        # Download
        if found_url.lower().endswith(".pdf"):
            dest = folder / f"{slug}.pdf"
        else:
            dest = folder / f"{slug}.html"

        ok, msg = download(found_url, dest)

        if not ok:
            print(f"DL FAIL: {msg[:50]} ({wall:.1f}s)")
            not_found_count += 1
            with open(FINAL_MISSING_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            with open(TIMINGS_FILE, "a") as f:
                f.write(json.dumps({"index": i, "blog_slug": slug, "status": "download_fail",
                                    "url": found_url, "error": msg[:100],
                                    "wall_seconds": round(wall, 2),
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
            continue

        # Sanity check for PDFs
        if dest.suffix == ".pdf":
            ok_check, check_msg = sanity_check(dest)
            if not ok_check:
                print(f"JUNK: {check_msg} ({wall:.1f}s)")
                dest.unlink(missing_ok=True)
                not_found_count += 1
                with open(FINAL_MISSING_FILE, "a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                with open(TIMINGS_FILE, "a") as f:
                    f.write(json.dumps({"index": i, "blog_slug": slug, "status": "junk_pdf",
                                        "url": found_url, "error": check_msg,
                                        "wall_seconds": round(wall, 2),
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
                continue

        found_count += 1
        print(f"OK ({wall:.1f}s) {dest.name}")
        with open(TIMINGS_FILE, "a") as f:
            f.write(json.dumps({"index": i, "blog_slug": slug, "status": "found",
                                "url": found_url, "file": str(dest.relative_to(BASE_DIR)),
                                "queries_tried": queries_tried,
                                "wall_seconds": round(wall, 2),
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")

        PROGRESS_FILE.write_text(json.dumps({
            "index": i + 1, "total": total,
            "found": found_count, "not_found": not_found_count,
            "already": already_count,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }))

    PROGRESS_FILE.write_text(json.dumps({
        "index": total, "total": total,
        "found": found_count, "not_found": not_found_count,
        "already": already_count,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))

    print()
    print("=" * 70)
    print(f"Done. {found_count} found, {not_found_count} still missing, {already_count} already had")
    print(f"Final missing: {FINAL_MISSING_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()

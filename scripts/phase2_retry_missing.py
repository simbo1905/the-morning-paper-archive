#!/usr/bin/env -S uv -S python3
"""Phase 2: Second pass to find missing papers using Tavily search results.

Reads papers_not_found.txt, searches for each paper using Tavily MCP results
(provided as a JSON file), and downloads found PDFs.

Usage:
  python3 scripts/phase2_retry_missing.py --tavily-results .tmp/tavily_results.jsonl
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

NOT_FOUND_FILE = BASE_DIR / "papers_not_found.txt"
STILL_MISSING_FILE = BASE_DIR / "papers_still_missing.txt"
PAPERS_DIR = BASE_DIR / "papers"
PROGRESS_FILE = BASE_DIR / "phase2_progress.json"
TIMINGS_FILE = BASE_DIR / ".tmp" / "bench" / "phase2_timings.jsonl"
TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

PDF_EXTENSIONS = [".pdf", ".docx", ".ps", ".doc", ".pptx", ".html"]
SKIP_DOMAINS = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets",
                "simbo1905", "bbc.co.uk", "youtube.com", "twitter.com", "x.com",
                "amazon.com", "wikipedia.org"]


def load_not_found():
    records = []
    for line in NOT_FOUND_FILE.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def get_paper_path(rec):
    date_path = rec.get("blog_date", "")
    parts = date_path.split("/")
    if len(parts) != 3:
        return None, None
    year, month, day = parts
    slug = rec["blog_slug"]
    folder = PAPERS_DIR / year / month / day
    return folder, slug


def check_existing(rec):
    folder, slug = get_paper_path(rec)
    if not folder:
        return None
    for ext in PDF_EXTENSIONS:
        p = folder / f"{slug}{ext}"
        if p.exists() and p.stat().st_size > 1000:
            return p
    return None


def download_url(url, dest_path, timeout=30):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
            if len(data) < 1000:
                return False, "too small"
            if dest_path.suffix == ".pdf":
                if not data[:5] == b"%PDF-" and "pdf" not in content_type:
                    return False, "not a PDF"
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(data)
            return True, f"{len(data)} bytes"
    except Exception as e:
        return False, str(e)


def try_download_from_url(url, rec):
    """Try to download a paper from a URL. Returns path if successful."""
    folder, slug = get_paper_path(rec)
    if not folder:
        return None

    if url.lower().endswith(".pdf"):
        dest = folder / f"{slug}.pdf"
        ok, msg = download_url(url, dest)
        if ok:
            return dest
        return None

    # Try arxiv abs -> pdf
    if "arxiv.org/abs/" in url:
        pdf_url = url.replace("arxiv.org/abs/", "arxiv.org/pdf/")
        if not pdf_url.endswith(".pdf"):
            pdf_url += ".pdf"
        dest = folder / f"{slug}.pdf"
        ok, msg = download_url(pdf_url, dest)
        if ok:
            return dest

    # Try fetching the page to find PDF links
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        })
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
        pdf_links = re.findall(r'href="([^"]+\.pdf[^"]*)"', html, re.IGNORECASE)
        for link in pdf_links:
            if link.startswith("http"):
                dest = folder / f"{slug}.pdf"
                ok, msg = download_url(link, dest)
                if ok:
                    return dest
            elif link.startswith("/"):
                from urllib.parse import urljoin
                full_url = urljoin(url, link)
                dest = folder / f"{slug}.pdf"
                ok, msg = download_url(full_url, dest)
                if ok:
                    return dest
    except Exception:
        pass

    # Save as HTML if it's a paper page
    if any(d in url for d in ["usenix.org", "arxiv.org", "acm.org", "ieee.org",
                              "springer.com", "semanticscholar.org"]):
        dest = folder / f"{slug}.html"
        ok, msg = download_url(url, dest)
        if ok:
            return dest

    return None


def main():
    records = load_not_found()
    total = len(records)
    print(f"Total not-found papers: {total}")

    found_count = 0
    still_missing_count = 0
    already_count = 0

    # Clear still missing file
    STILL_MISSING_FILE.unlink(missing_ok=True)

    for i, rec in enumerate(records):
        slug = rec["blog_slug"]
        title = rec.get("paper_title", "")[:60]

        # Check if already downloaded (maybe by a previous run)
        existing = check_existing(rec)
        if existing:
            already_count += 1
            continue

        print(f"  [{i}/{total}] {slug[:50]} ...", end=" ", flush=True)
        t0 = time.monotonic()

        # Try the original paper_url again with more aggressive approach
        paper_url = rec.get("paper_url", "")
        downloaded = None

        if paper_url and not any(d in paper_url for d in SKIP_DOMAINS):
            downloaded = try_download_from_url(paper_url, rec)

        wall = time.monotonic() - t0

        timing = {
            "index": i,
            "blog_slug": slug,
            "wall_seconds": round(wall, 2),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        if downloaded:
            found_count += 1
            timing["status"] = "found"
            timing["file"] = str(downloaded.relative_to(BASE_DIR))
            print(f"FOUND ({wall:.1f}s) -> {downloaded.name}")
        else:
            still_missing_count += 1
            timing["status"] = "still_missing"
            with open(STILL_MISSING_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"STILL MISSING ({wall:.1f}s)")

        with open(TIMINGS_FILE, "a") as f:
            f.write(json.dumps(timing) + "\n")

        if (i + 1) % 10 == 0:
            PROGRESS_FILE.write_text(json.dumps({
                "index": i + 1,
                "total": total,
                "found": found_count,
                "still_missing": still_missing_count,
                "already": already_count,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }))

    PROGRESS_FILE.write_text(json.dumps({
        "index": total,
        "total": total,
        "found": found_count,
        "still_missing": still_missing_count,
        "already": already_count,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))

    print()
    print("=" * 80)
    print(f"Done. {found_count} found, {still_missing_count} still missing, {already_count} already had")
    print(f"Still missing file: {STILL_MISSING_FILE}")
    print("=" * 80)


if __name__ == "__main__":
    main()

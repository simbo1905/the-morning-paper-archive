#!/usr/bin/env -S uv -S python3
"""Phase 2: Search for missing papers using web search results.

Reads papers_not_found.txt and a file of search results (.tmp/phase2_search_results.jsonl),
downloads found PDFs to papers/YYYY/MM/DD/slug.pdf.

The search results file is populated by subagents using web_search.
Each line: {"blog_slug": "...", "blog_date": "...", "pdf_url": "..." or null}

Usage:
  python3 scripts/phase2_download_from_search.py
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
SEARCH_RESULTS_FILE = BASE_DIR / ".tmp" / "phase2_search_results.jsonl"
STILL_MISSING_FILE = BASE_DIR / "papers_still_missing.txt"
PAPERS_DIR = BASE_DIR / "papers"
PROGRESS_FILE = BASE_DIR / "phase2_progress.json"
TIMINGS_FILE = BASE_DIR / ".tmp" / "bench" / "phase2_timings.jsonl"
TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

PDF_EXTENSIONS = [".pdf", ".docx", ".ps", ".doc", ".pptx", ".html"]


def load_not_found():
    records = []
    for line in NOT_FOUND_FILE.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def load_search_results():
    results = {}
    if SEARCH_RESULTS_FILE.exists():
        for line in SEARCH_RESULTS_FILE.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                results[rec["blog_slug"]] = rec.get("pdf_url")
    return results


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


def main():
    records = load_not_found()
    search_results = load_search_results()
    total = len(records)

    print(f"Total not-found papers: {total}")
    print(f"Search results available: {len(search_results)}")
    print()

    found_count = 0
    still_missing_count = 0
    already_count = 0

    STILL_MISSING_FILE.unlink(missing_ok=True)

    for i, rec in enumerate(records):
        slug = rec["blog_slug"]

        existing = check_existing(rec)
        if existing:
            already_count += 1
            continue

        pdf_url = search_results.get(slug)

        folder, slug_name = get_paper_path(rec)
        if not folder:
            still_missing_count += 1
            with open(STILL_MISSING_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue

        downloaded = None
        if pdf_url:
            if pdf_url.lower().endswith(".pdf"):
                dest = folder / f"{slug_name}.pdf"
            else:
                dest = folder / f"{slug_name}.html"
            ok, msg = download_url(pdf_url, dest)
            if ok:
                downloaded = dest

        if downloaded:
            found_count += 1
            if (i + 1) % 50 == 0 or i < 5:
                print(f"  [{i}/{total}] {slug[:50]} -> FOUND ({downloaded.name})")
        else:
            still_missing_count += 1
            with open(STILL_MISSING_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{total} | found={found_count} missing={still_missing_count} already={already_count}")

    PROGRESS_FILE.write_text(json.dumps({
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

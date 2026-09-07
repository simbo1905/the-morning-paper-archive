#!/usr/bin/env -S uv -S python3
"""Phase 1b: Fetch and download papers using Tavily search.

For each metadata record:
  1. If paper_url is a direct PDF link, download it
  2. Otherwise, use Tavily to search for the paper PDF
  3. Download to papers/YYYY/MM/DD/slug.pdf (or .docx, .ps, .html)
  4. Papers not found -> append to papers_not_found.txt

Processes in batches of 10. Resume capability via checking existing files.

Usage:
  python3 scripts/phase1b_fetch_papers.py
  python3 scripts/phase1b_fetch_papers.py --start 0 --batch 10
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

METADATA_FILE = BASE_DIR / "phase1a_metadata.jsonl"
PAPERS_DIR = BASE_DIR / "papers"
NOT_FOUND_FILE = BASE_DIR / "papers_not_found.txt"
PROGRESS_FILE = BASE_DIR / "phase1b_progress.json"
TIMINGS_FILE = BASE_DIR / ".tmp" / "bench" / "phase1b_timings.jsonl"
TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

# Tavily API key from environment
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
if not TAVILY_API_KEY:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("TAVILY_API_KEY="):
                TAVILY_API_KEY = line.split("=", 1)[1].strip()
                break

TAVILY_URL = "https://api.tavily.com/search"

PDF_EXTENSIONS = [".pdf", ".docx", ".ps", ".doc", ".pptx", ".html"]
SKIP_DOMAINS = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets",
                "simbo1905", "bbc.co.uk", "youtube.com", "twitter.com", "x.com",
                "amazon.com", "wikipedia.org"]


def load_metadata():
    records = []
    for line in METADATA_FILE.read_text().splitlines():
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
            # Check if it's actually a PDF when saving as PDF
            if dest_path.suffix == ".pdf":
                if not data[:5] == b"%PDF-" and "pdf" not in content_type:
                    return False, "not a PDF"
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(data)
            return True, f"{len(data)} bytes"
    except Exception as e:
        return False, str(e)


def try_direct_download(rec):
    paper_url = rec.get("paper_url")
    if not paper_url:
        return None

    folder, slug = get_paper_path(rec)
    if not folder:
        return None

    # If URL ends with .pdf, try direct download
    if paper_url.lower().endswith(".pdf"):
        dest = folder / f"{slug}.pdf"
        ok, msg = download_url(paper_url, dest)
        if ok:
            return dest
        return None

    # Try arxiv-style URLs (abs -> pdf)
    if "arxiv.org/abs/" in paper_url:
        pdf_url = paper_url.replace("arxiv.org/abs/", "arxiv.org/pdf/")
        if not pdf_url.endswith(".pdf"):
            pdf_url += ".pdf"
        dest = folder / f"{slug}.pdf"
        ok, msg = download_url(pdf_url, dest)
        if ok:
            return dest

    # Try USENIX direct
    if "usenix.org" in paper_url and not paper_url.endswith(".pdf"):
        # Try adding /download or finding PDF link
        pass

    # Try ACM DL
    if "dl.acm.org" in paper_url:
        pass

    return None


def tavily_search(query, max_results=5):
    if not TAVILY_API_KEY:
        return None, "No TAVILY_API_KEY"

    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "include_raw_content": False,
    }).encode()

    try:
        req = urllib.request.Request(TAVILY_URL, data=payload, headers={
            "Content-Type": "application/json",
        }, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        return result, None
    except Exception as e:
        return None, str(e)


def find_pdf_in_tavily(rec):
    title = rec.get("paper_title", "")
    if not title:
        return None

    authors = rec.get("paper_authors", [])
    year = rec.get("paper_year", "")
    venue = rec.get("paper_venue", "")

    # Build search query
    author_str = ""
    if authors:
        if isinstance(authors, list):
            author_str = " ".join(authors[:2])
        else:
            author_str = str(authors)

    query = f'"{title}" {author_str} {year} filetype:pdf'
    if venue:
        query += f' {venue}'

    result, err = tavily_search(query)
    if err:
        return None

    if not result or "results" not in result:
        return None

    for r in result.get("results", []):
        url = r.get("url", "")
        if not url:
            continue
        if any(d in url for d in SKIP_DOMAINS):
            continue
        if url.lower().endswith(".pdf"):
            return url

    # If no direct PDF, try first result that looks like a paper repository
    for r in result.get("results", []):
        url = r.get("url", "")
        if not url:
            continue
        if any(d in url for d in SKIP_DOMAINS):
            continue
        if any(d in url for d in ["arxiv.org", "usenix.org", "acm.org", "ieee.org",
                                  "springer.com", "sciencedirect.com", "researchgate.net",
                                  "semanticscholar.org", "drops.dagstuhl.de"]):
            return url

    return None


def find_pdf_from_url(url):
    """Try to find a PDF link from a non-PDF URL by fetching the page."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        })
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
    except Exception:
        # If we can't fetch the page, try known URL transformations
        if "arxiv.org/abs/" in url:
            return url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"
        return None

    # Look for PDF links in the HTML
    pdf_links = re.findall(r'href="([^"]+\.pdf[^"]*)"', html, re.IGNORECASE)
    for link in pdf_links:
        if link.startswith("http"):
            return link
        if link.startswith("/"):
            from urllib.parse import urljoin
            return urljoin(url, link)

    # arxiv abs -> pdf
    if "arxiv.org/abs/" in url:
        return url.replace("arxiv.org/abs/", "arxiv.org/pdf/") + ".pdf"

    # USENIX: look for download links
    if "usenix.org" in url:
        # Try /download path
        download_url = url.rstrip("/") + "/download"
        return download_url

    return None


def save_progress(index, total, found, not_found, errors):
    PROGRESS_FILE.write_text(json.dumps({
        "index": index,
        "total": total,
        "found": found,
        "not_found": not_found,
        "errors": errors,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))


def append_not_found(rec, reason=""):
    with open(NOT_FOUND_FILE, "a") as f:
        f.write(json.dumps({
            "blog_url": rec.get("blog_url", ""),
            "blog_slug": rec.get("blog_slug", ""),
            "blog_date": rec.get("blog_date", ""),
            "paper_title": rec.get("paper_title", ""),
            "paper_authors": rec.get("paper_authors", []),
            "paper_year": rec.get("paper_year", 0),
            "paper_venue": rec.get("paper_venue", ""),
            "paper_url": rec.get("paper_url", ""),
            "blog_summary": rec.get("blog_summary", ""),
            "topics": rec.get("topics", []),
            "tags": rec.get("tags", []),
            "reason": reason,
        }, ensure_ascii=False) + "\n")


def main():
    start_index = 0
    batch_size = 10

    args = sys.argv[1:]
    if "--start" in args:
        start_index = int(args[args.index("--start") + 1])
    if "--batch" in args:
        batch_size = int(args[args.index("--batch") + 1])

    records = load_metadata()
    total = len(records)

    # Filter to only records with paper titles (skip non-paper posts)
    paper_records = [r for r in records if r.get("paper_title")]
    non_paper = total - len(paper_records)
    print(f"Total records: {total}")
    print(f"Paper records: {len(paper_records)}")
    print(f"Non-paper (no title): {non_paper}")
    print(f"Batch size: {batch_size}")
    print()

    found_count = 0
    not_found_count = 0
    error_count = 0
    already_count = 0

    for i, rec in enumerate(paper_records):
        if i < start_index:
            continue

        slug = rec["blog_slug"]
        title = rec.get("paper_title", "")[:60]

        # Check if already downloaded
        existing = check_existing(rec)
        if existing:
            already_count += 1
            if (i + 1) % 50 == 0:
                print(f"  [{i}/{len(paper_records)}] ALREADY: {slug[:50]}")
            continue

        print(f"  [{i}/{len(paper_records)}] {slug[:50]} ...", end=" ", flush=True)
        t0 = time.monotonic()

        # Step 1: Try direct download from paper_url
        downloaded = try_direct_download(rec)

        # Step 2: If paper_url is not a PDF, try to find PDF from the page
        if not downloaded and rec.get("paper_url"):
            paper_url = rec["paper_url"]
            if not paper_url.lower().endswith(".pdf"):
                pdf_url = find_pdf_from_url(paper_url)
                if pdf_url:
                    folder, slug_name = get_paper_path(rec)
                    if folder:
                        dest = folder / f"{slug_name}.pdf"
                        ok, msg = download_url(pdf_url, dest)
                        if ok:
                            downloaded = dest

        # Step 3: Use Tavily to search for the paper (only if key available)
        if not downloaded and TAVILY_API_KEY:
            pdf_url = find_pdf_in_tavily(rec)
            if pdf_url:
                folder, slug_name = get_paper_path(rec)
                if folder:
                    if pdf_url.lower().endswith(".pdf"):
                        dest = folder / f"{slug_name}.pdf"
                    else:
                        dest = folder / f"{slug_name}.html"
                    ok, msg = download_url(pdf_url, dest)
                    if ok:
                        downloaded = dest
                    else:
                        # Try the found URL as HTML
                        if not pdf_url.lower().endswith(".pdf"):
                            dest = folder / f"{slug_name}.html"
                            ok, msg = download_url(pdf_url, dest)
                            if ok:
                                downloaded = dest

        wall = time.monotonic() - t0

        timing = {
            "index": i,
            "blog_slug": slug,
            "paper_title": rec.get("paper_title", ""),
            "wall_seconds": round(wall, 2),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        if downloaded:
            found_count += 1
            timing["status"] = "found"
            timing["file"] = str(downloaded.relative_to(BASE_DIR))
            print(f"FOUND ({wall:.1f}s) -> {downloaded.name}")
        else:
            not_found_count += 1
            timing["status"] = "not_found"
            append_not_found(rec, "Phase 1b: no PDF found via direct or Tavily")
            print(f"NOT FOUND ({wall:.1f}s)")

        with open(TIMINGS_FILE, "a") as f:
            f.write(json.dumps(timing) + "\n")

        if (i + 1) % 10 == 0:
            save_progress(i + 1, len(paper_records), found_count, not_found_count, error_count)

    save_progress(len(paper_records), len(paper_records), found_count, not_found_count, error_count)

    print()
    print("=" * 80)
    print(f"Done. {found_count} found, {not_found_count} not found, {already_count} already had")
    print(f"Not found file: {NOT_FOUND_FILE}")
    print("=" * 80)


if __name__ == "__main__":
    main()

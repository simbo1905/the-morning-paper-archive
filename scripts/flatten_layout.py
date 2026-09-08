#!/usr/bin/env -S uv -S python3
"""Flatten the paper layout to pages/YYYYMMDD.md + pages/YYYYMMDD.json.

- Reads content/YYYY/MM/slug.txt (blog post text) -> pages/YYYYMMDD.md
- Reads papers/YYYY/MM/DD/slug.json (final JTD JSON) -> pages/YYYYMMDD.json
- Short fixed-length filenames for Whoosh index efficiency
- The .json contains: blog_summary, paper_abstract, paper_title, paper_authors,
  paper_year, paper_venue, blog_url, blog_slug, topics, tags, figures,
  paper_file (relative path to the PDF), extraction_method, ocr_used

Usage:
  python3 scripts/flatten_layout.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

CONTENT_DIR = BASE_DIR / "content"
PAPERS_DIR = BASE_DIR / "papers"
PAGES_DIR = BASE_DIR / "pages"
METADATA_FILE = BASE_DIR / "phase1a_metadata.jsonl"


def url_to_date(url: str) -> str | None:
    """Extract YYYYMMDD from blog URL."""
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return None


def url_to_slug(url: str) -> str | None:
    """Extract slug from blog URL."""
    m = re.match(r'https://blog\.acolyer\.org/\d{4}/\d{2}/\d{2}/(.+?)/?$', url)
    if m:
        return m.group(1)
    return None


def url_to_date_path(url: str) -> str | None:
    """Extract YYYY/MM/DD from blog URL."""
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return None


def find_content_file(url: str) -> Path | None:
    """Find the crawled blog post text file."""
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/(.+?)/?$', url)
    if not m:
        return None
    year, month, day, slug = m.groups()
    # Content files are at content/YYYY/MM/slug.txt
    p = CONTENT_DIR / year / month / f"{slug}.txt"
    if p.exists():
        return p
    return None


def find_final_json(url: str) -> Path | None:
    """Find the final JTD JSON file."""
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/(.+?)/?$', url)
    if not m:
        return None
    year, month, day, slug = m.groups()
    p = PAPERS_DIR / year / month / day / f"{slug}.json"
    if p.exists():
        return p
    return None


def content_to_markdown(content_path: Path, url: str) -> str:
    """Convert the crawled blog post text to markdown."""
    raw = content_path.read_text(encoding="utf-8", errors="replace")
    
    # Parse header (URL, Title, ---)
    lines = raw.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("Title:"):
            title = line[6:].strip()
        if line.strip() == "---":
            body_start = i + 1
            break
    
    body = "\n".join(lines[body_start:]).strip()
    
    # Build markdown
    md_parts = []
    md_parts.append(f"---")
    md_parts.append(f"blog_url: {url}")
    md_parts.append(f"blog_title: {title}")
    md_parts.append(f"---")
    md_parts.append("")
    md_parts.append(body)
    
    return "\n".join(md_parts)


def main():
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load metadata
    records = []
    for line in METADATA_FILE.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    
    print(f"Flattening {len(records)} records to pages/")
    
    md_count = 0
    json_count = 0
    missing_content = 0
    missing_json = 0
    
    # Track used filenames to handle duplicate dates
    used_names: set[str] = set()
    
    for rec in records:
        url = rec.get("blog_url", "")
        date8 = url_to_date(url)
        slug = url_to_slug(url)
        date_path = url_to_date_path(url)
        
        if not date8 or not slug:
            continue
        
        # Handle duplicate dates by appending -2, -3, etc.
        base_name = date8
        file_name = base_name
        while file_name in used_names:
            # Find the suffix number
            parts = file_name.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                file_name = f"{parts[0]}-{int(parts[1]) + 1}"
            else:
                file_name = f"{base_name}-2"
        used_names.add(file_name)
        
        # Write markdown file
        content_path = find_content_file(url)
        if content_path:
            md_path = PAGES_DIR / f"{file_name}.md"
            md = content_to_markdown(content_path, url)
            md_path.write_text(md, encoding="utf-8")
            md_count += 1
        else:
            missing_content += 1
        
        # Write JSON metadata file
        final_json_path = find_final_json(url)
        if final_json_path:
            data = json.loads(final_json_path.read_text())
            
            # Build compact JSON for the pages/ folder
            # Include only what's needed for the SPA
            compact = {
                "date": file_name,
                "file_name": file_name,
                "slug": slug,
                "blog_url": data.get("blog_url", url),
                "blog_title": data.get("blog_title", ""),
                "blog_summary": data.get("blog_summary", ""),
                "paper_title": data.get("paper_title", ""),
                "paper_authors": data.get("paper_authors", []),
                "paper_year": data.get("paper_year") or 0,
                "paper_venue": data.get("paper_venue") or "",
                "paper_url": data.get("paper_url") or "",
                "paper_abstract": data.get("paper_abstract") or "",
                "paper_file": data.get("paper_file") or "",
                "topics": data.get("topics", []),
                "tags": data.get("tags", []),
                "figures": data.get("figures", []),
                "extraction_method": data.get("extraction_method") or "",
                "ocr_used": data.get("ocr_used", False),
            }
            
            json_path = PAGES_DIR / f"{file_name}.json"
            json_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
            json_count += 1
        else:
            # Write a minimal JSON from the metadata record
            compact = {
                "date": file_name,
                "file_name": file_name,
                "slug": slug,
                "blog_url": url,
                "blog_title": rec.get("blog_title", ""),
                "blog_summary": rec.get("blog_summary", ""),
                "paper_title": rec.get("paper_title", ""),
                "paper_authors": rec.get("paper_authors", []),
                "paper_year": rec.get("paper_year") or 0,
                "paper_venue": rec.get("paper_venue", "") or "",
                "paper_url": rec.get("paper_url", "") or "",
                "paper_abstract": "",
                "paper_file": "",
                "topics": rec.get("topics", []),
                "tags": rec.get("tags", []),
                "figures": [],
                "extraction_method": "",
                "ocr_used": False,
            }
            json_path = PAGES_DIR / f"{file_name}.json"
            json_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
            json_count += 1
            missing_json += 1
    
    print(f"Done.")
    print(f"  Markdown files: {md_count}")
    print(f"  JSON files:     {json_count}")
    print(f"  Missing content: {missing_content}")
    print(f"  Missing final JSON: {missing_json}")
    
    # Check for duplicates
    dates = set()
    dupes = 0
    for rec in records:
        d = url_to_date(rec.get("blog_url", ""))
        if d:
            if d in dates:
                dupes += 1
                print(f"  DUPLICATE DATE: {d} - {rec.get('blog_slug', '')}")
            dates.add(d)
    if dupes:
        print(f"  Total duplicates: {dupes}")
    
    # Show size
    import subprocess
    result = subprocess.run(["du", "-sh", str(PAGES_DIR)], capture_output=True, text=True)
    print(f"  pages/ size: {result.stdout.strip()}")


if __name__ == "__main__":
    main()

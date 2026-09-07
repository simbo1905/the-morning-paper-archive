#!/usr/bin/env -S uv -S python3
"""Phase 6: Build final JTD-compliant JSON per paper.

For each paper in phase1a_metadata.jsonl:
  1. Find the downloaded paper file (papers/YYYY/MM/DD/slug.pdf|html|ps)
  2. Find the text extract (papers/YYYY/MM/DD/slug.txt)
  3. Find the figures JSON (papers/YYYY/MM/DD/slug.figures.json)
  4. Extract abstract from paper text
  5. Determine extraction method and OCR usage from phase4 log
  6. Build JSON conforming to paper-meta.jdt.json (RFC 8927 JTD)
  7. Write to papers/YYYY/MM/DD/slug.json

Usage:
  python3 scripts/phase6_build_json.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

PAPERS_DIR = BASE_DIR / "papers"
METADATA_FILE = BASE_DIR / "phase1a_metadata.jsonl"
EXTRACTION_LOG = BASE_DIR / ".tmp" / "phase4_extraction_log.jsonl"
PROGRESS_FILE = BASE_DIR / "phase6_progress.json"
FINAL_MISSING_FILE = BASE_DIR / "papers_final_missing.txt"

PDF_EXTENSIONS = [".pdf", ".html", ".ps", ".doc", ".docx"]


def load_extraction_log():
    """Load Phase 4 extraction log to get method/OCR info per file."""
    log = {}
    if EXTRACTION_LOG.exists():
        for line in EXTRACTION_LOG.read_text().splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                    log[rec.get("file", "")] = rec
                except json.JSONDecodeError:
                    continue
    return log


def find_paper_file(slug: str, date_path: str) -> Path | None:
    """Find the downloaded paper file."""
    parts = date_path.split("/")
    if len(parts) != 3:
        return None
    folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
    for ext in PDF_EXTENSIONS:
        p = folder / f"{slug}{ext}"
        if p.exists() and p.stat().st_size > 1000:
            return p
    return None


def find_text_file(slug: str, date_path: str) -> Path | None:
    """Find the extracted text file."""
    parts = date_path.split("/")
    if len(parts) != 3:
        return None
    folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
    p = folder / f"{slug}.txt"
    if p.exists() and p.stat().st_size > 50:
        return p
    return None


def find_figures_file(slug: str, date_path: str) -> Path | None:
    """Find the figures JSON file."""
    parts = date_path.split("/")
    if len(parts) != 3:
        return None
    folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
    p = folder / f"{slug}.figures.json"
    if p.exists():
        return p
    return None


def extract_abstract(text: str) -> str | None:
    """Extract the abstract from paper text."""
    # Try to find "Abstract" section
    # Common patterns: "Abstract", "ABSTRACT", "Abstract:", "Abstract."
    patterns = [
        r'(?:^|\n)\s*Abstract\s*[:.]?\s*\n+(.*?)(?:\n\s*(?:1\s+Introduction|Introduction\s*\n|I\s+Introduction|1\.\s+Introduction|Keywords|Categories and Subject|CCS\s+Concepts|Index\s+Terms))',
        r'(?:^|\n)\s*ABSTRACT\s*[:.]?\s*\n+(.*?)(?:\n\s*(?:1\s+Introduction|Introduction\s*\n|I\s+Introduction|1\.\s+Introduction|Keywords|Categories and Subject|CCS\s+Concepts|Index\s+Terms))',
        r'(?:^|\n)\s*Abstract\s*[:.]?\s*(.*?)(?:\n\n(?:1\s|Introduction|Keywords|Categories|CCS|Index))',
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            abstract = m.group(1).strip()
            # Clean up whitespace
            abstract = re.sub(r'\s+', ' ', abstract)
            # Limit to reasonable length
            if len(abstract) > 2000:
                abstract = abstract[:2000] + "..."
            if len(abstract) > 50:
                return abstract

    # Fallback: try to get first few paragraphs after title
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r'^\s*Abstract\s*[:.]?\s*$', line, re.IGNORECASE):
            # Get next few lines
            abstract_lines = []
            for j in range(i + 1, min(i + 20, len(lines))):
                line = lines[j].strip()
                if not line:
                    if abstract_lines:
                        break
                    continue
                if re.match(r'^(1\s+Introduction|Introduction|Keywords|Categories|CCS|Index)', line, re.IGNORECASE):
                    break
                abstract_lines.append(line)
            if abstract_lines:
                abstract = " ".join(abstract_lines)
                abstract = re.sub(r'\s+', ' ', abstract)
                if len(abstract) > 2000:
                    abstract = abstract[:2000] + "..."
                if len(abstract) > 50:
                    return abstract

    # Last resort: first 500 chars of text
    first_500 = text[:500].strip()
    if len(first_500) > 100:
        return first_500

    return None


def main():
    # Load metadata
    records = []
    for line in METADATA_FILE.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))

    total = len(records)
    print(f"Phase 6: Building JTD-compliant JSON for {total} papers")

    # Load extraction log
    extraction_log = load_extraction_log()
    print(f"  Loaded {len(extraction_log)} extraction log entries")

    # Load final missing papers
    missing_slugs = set()
    if FINAL_MISSING_FILE.exists():
        for line in FINAL_MISSING_FILE.read_text().splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                    missing_slugs.add(rec.get("blog_slug", ""))
                except json.JSONDecodeError:
                    continue
    print(f"  Missing papers: {len(missing_slugs)}")

    ok_count = 0
    no_file_count = 0
    no_text_count = 0
    error_count = 0

    for i, rec in enumerate(records):
        slug = rec.get("blog_slug", "")
        date_path = rec.get("blog_date", "")
        title = rec.get("paper_title", "")

        if not title:
            # Non-paper post (end-of-term etc.)
            continue

        print(f"  [{i+1}/{total}] {slug[:50]} ...", end=" ", flush=True)

        # Find paper file
        paper_file = find_paper_file(slug, date_path)
        if not paper_file:
            if slug in missing_slugs:
                print("MISSING (known)")
            else:
                print("MISSING (unexpected!)")
                no_file_count += 1
            # Still write a JSON with what we have
            parts = date_path.split("/")
            if len(parts) == 3:
                folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
                folder.mkdir(parents=True, exist_ok=True)
                json_path = folder / f"{slug}.json"
                json_path.write_text(json.dumps({
                    "blog_url": rec.get("blog_url", ""),
                    "blog_title": rec.get("blog_title", ""),
                    "blog_date": date_path,
                    "blog_slug": slug,
                    "blog_summary": rec.get("blog_summary", ""),
                    "paper_title": title,
                    "paper_authors": rec.get("paper_authors", []),
                    "paper_year": rec.get("paper_year", 0),
                    "paper_venue": rec.get("paper_venue", "") or None,
                    "paper_url": rec.get("paper_url", "") or None,
                    "paper_url_source": "blog.acolyer.org",
                    "paper_abstract": None,
                    "paper_file": None,
                    "paper_text_file": None,
                    "topics": rec.get("topics", []),
                    "tags": rec.get("tags", []),
                    "figures": [],
                    "extraction_method": None,
                    "ocr_used": None,
                }, indent=2, ensure_ascii=False))
            continue

        # Find text file
        text_file = find_text_file(slug, date_path)
        if not text_file:
            print("NO TEXT")
            no_text_count += 1
            continue

        # Find figures file
        figures_file = find_figures_file(slug, date_path)
        figures = []
        if figures_file:
            try:
                fig_data = json.loads(figures_file.read_text())
                figures = fig_data.get("figures", [])
            except Exception:
                pass

        # Extract abstract from text
        paper_text = text_file.read_text(encoding="utf-8", errors="replace")
        abstract = extract_abstract(paper_text)

        # Get extraction method from log
        paper_rel = str(paper_file.relative_to(BASE_DIR))
        log_entry = extraction_log.get(paper_rel, {})
        extraction_method = log_entry.get("method", "unknown")
        ocr_used = "tesseract" in extraction_method or "ocr" in extraction_method

        # Build the final JSON
        text_rel = str(text_file.relative_to(BASE_DIR))

        final_json = {
            "blog_url": rec.get("blog_url", ""),
            "blog_title": rec.get("blog_title", ""),
            "blog_date": date_path,
            "blog_slug": slug,
            "blog_summary": rec.get("blog_summary", ""),
            "paper_title": title,
            "paper_authors": rec.get("paper_authors", []),
            "paper_year": rec.get("paper_year", 0),
            "paper_venue": rec.get("paper_venue", "") or None,
            "paper_url": rec.get("paper_url", "") or None,
            "paper_url_source": "blog.acolyer.org",
            "paper_abstract": abstract,
            "paper_file": paper_rel,
            "paper_text_file": text_rel,
            "topics": rec.get("topics", []),
            "tags": rec.get("tags", []),
            "figures": figures,
            "extraction_method": extraction_method,
            "ocr_used": ocr_used,
        }

        # Write JSON
        parts = date_path.split("/")
        if len(parts) == 3:
            folder = PAPERS_DIR / parts[0] / parts[1] / parts[2]
            json_path = folder / f"{slug}.json"
            json_path.write_text(json.dumps(final_json, indent=2, ensure_ascii=False))

            ok_count += 1
            fig_count = len(figures)
            abs_status = "abs" if abstract else "no-abs"
            print(f"OK ({abs_status}, {fig_count} figs, {extraction_method})")

            if (i + 1) % 100 == 0:
                PROGRESS_FILE.write_text(json.dumps({
                    "index": i + 1, "total": total,
                    "ok": ok_count, "no_file": no_file_count,
                    "no_text": no_text_count, "errors": error_count,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }))

    PROGRESS_FILE.write_text(json.dumps({
        "index": total, "total": total,
        "ok": ok_count, "no_file": no_file_count,
        "no_text": no_text_count, "errors": error_count,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))

    print()
    print("=" * 70)
    print(f"Done. Processed {total} metadata records.")
    print(f"  JSON written:   {ok_count}")
    print(f"  No paper file:   {no_file_count}")
    print(f"  No text file:    {no_text_count}")
    print(f"  Errors:          {error_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()

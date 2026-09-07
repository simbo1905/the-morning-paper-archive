#!/usr/bin/env -S uv -S python3
"""Phase 4: Text extraction / OCR for all downloaded papers.

For each paper file in papers/YYYY/MM/DD/:
  1. If .pdf: try pdftotext -> slug.txt
  2. If pdftotext output is empty/garbage (scanned): use pdfimages + tesseract OCR
  3. If .html: extract text with simple HTML stripping
  4. Log extraction method used
  5. Skip if slug.txt already exists (resume capable)

Output: papers/YYYY/MM/DD/slug.txt
Log: .tmp/phase4_extraction_log.jsonl

Usage:
  python3 scripts/phase4_extract_text.py
  python3 scripts/phase4_extract_text.py --start 100
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

PAPERS_DIR = BASE_DIR / "papers"
LOG_FILE = BASE_DIR / ".tmp" / "phase4_extraction_log.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

PDFTOTEXT = "/opt/homebrew/bin/pdftotext"
PDFIMAGES = "/opt/homebrew/bin/pdfimages"
TESSERACT = "/opt/homebrew/bin/tesseract"

PDF_EXTENSIONS = [".pdf", ".html", ".ps", ".doc", ".docx"]


def is_text_good(text: str) -> bool:
    """Check if extracted text is usable (not junk)."""
    if len(text) < 100:
        return False
    alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if alpha_ratio < 0.3:
        return False
    return True


def extract_pdf(pdf_path: Path, txt_path: Path) -> tuple[str, str]:
    """Extract text from PDF. Returns (method, text)."""
    # Try pdftotext first
    try:
        result = subprocess.run(
            [PDFTOTEXT, str(pdf_path), str(txt_path)],
            capture_output=True, text=True, timeout=30
        )
        if txt_path.exists() and txt_path.stat().st_size > 0:
            text = txt_path.read_text(errors="replace")
            if is_text_good(text):
                return "pdftotext", text
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    # pdftotext failed or produced junk — try OCR
    return extract_pdf_ocr(pdf_path, txt_path)


def extract_pdf_ocr(pdf_path: Path, txt_path: Path) -> tuple[str, str]:
    """Extract text from scanned/old PDF using pdftoppm + tesseract.

    Uses pdftoppm to render pages as images, then OCRs each page.
    Temp files go to .tmp/ inside the repo (sandbox-safe).
    """
    import shutil

    tmpdir = BASE_DIR / ".tmp" / f"ocr_{pdf_path.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        # Render pages as PNG at 300 DPI
        try:
            subprocess.run(
                ["/opt/homebrew/bin/pdftoppm", "-png", "-r", "300",
                 str(pdf_path), str(tmpdir / "page")],
                capture_output=True, timeout=120
            )
        except subprocess.TimeoutExpired:
            return "ocr_failed", ""
        except Exception:
            return "ocr_failed", ""

        # Find rendered page images
        pages = sorted(tmpdir.glob("page-*.png"))
        if not pages:
            # Try without dash (some versions use different naming)
            pages = sorted(tmpdir.glob("page*.png"))
        if not pages:
            return "ocr_failed", ""

        # OCR each page and concatenate
        all_text = []
        for page in pages:
            try:
                result = subprocess.run(
                    [TESSERACT, str(page), "-", "-l", "eng"],
                    capture_output=True, text=True, timeout=30
                )
                if result.stdout.strip():
                    all_text.append(result.stdout.strip())
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                continue

        text = "\n\n".join(all_text)
        if is_text_good(text):
            txt_path.write_text(text)
            return "tesseract_ocr", text
        else:
            # Write whatever we got
            if text:
                txt_path.write_text(text)
            return "ocr_low_quality", text

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def extract_html(html_path: Path, txt_path: Path) -> tuple[str, str]:
    """Extract text from HTML file."""
    raw = html_path.read_bytes().decode("utf-8", errors="replace")
    # Remove script and style tags
    raw = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    # Remove all tags
    text = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Expand to readable lines
    text = text.replace(". ", ".\n")
    if is_text_good(text):
        txt_path.write_text(text)
        return "html_strip", text
    return "html_low_quality", text


def extract_ps(ps_path: Path, txt_path: Path) -> tuple[str, str]:
    """Extract text from PostScript file."""
    try:
        result = subprocess.run(
            [PDFTOTEXT, str(ps_path), str(txt_path)],
            capture_output=True, text=True, timeout=30
        )
        if txt_path.exists() and txt_path.stat().st_size > 0:
            text = txt_path.read_text(errors="replace")
            if is_text_good(text):
                return "ps_pdftotext", text
    except Exception:
        pass
    # Try raw string extraction
    raw = ps_path.read_bytes().decode("latin-1", errors="replace")
    # Extract text between parentheses (PostScript string syntax)
    texts = re.findall(r"\(([^)]+)\)", raw)
    text = " ".join(texts)
    text = re.sub(r"\s+", " ", text).strip()
    if is_text_good(text):
        txt_path.write_text(text)
        return "ps_raw", text
    return "ps_failed", ""


def main():
    start_index = 0
    if "--start" in sys.argv:
        start_index = int(sys.argv[sys.argv.index("--start") + 1])

    # Find all paper files
    paper_files = []
    for f in sorted(PAPERS_DIR.rglob("*")):
        if f.is_file() and f.suffix in PDF_EXTENSIONS:
            paper_files.append(f)

    total = len(paper_files)
    print(f"Phase 4: Text extraction for {total} paper files")

    # Count already done
    already = 0
    to_process = []
    for f in paper_files:
        txt_path = f.with_suffix(".txt")
        if txt_path.exists() and txt_path.stat().st_size > 50:
            already += 1
        else:
            to_process.append(f)

    print(f"  Already extracted: {already}")
    print(f"  To process: {len(to_process)}")
    print()

    if start_index > 0:
        to_process = to_process[start_index:]
        print(f"  Starting from index {start_index}, {len(to_process)} remaining")
        print()

    pdftotext_count = 0
    ocr_count = 0
    html_count = 0
    ps_count = 0
    failed_count = 0
    low_quality_count = 0

    for i, paper_path in enumerate(to_process):
        txt_path = paper_path.with_suffix(".txt")

        # Skip if already done
        if txt_path.exists() and txt_path.stat().st_size > 50:
            continue

        slug = paper_path.stem
        rel = str(paper_path.relative_to(BASE_DIR))
        print(f"  [{i+1}/{len(to_process)}] {slug[:50]} ...", end=" ", flush=True)
        t0 = time.monotonic()

        method = "unknown"
        text_len = 0

        try:
            if paper_path.suffix == ".pdf":
                method, text = extract_pdf(paper_path, txt_path)
                text_len = len(text)
                if method == "pdftotext":
                    pdftotext_count += 1
                elif method == "tesseract_ocr":
                    ocr_count += 1
                elif method == "ocr_failed":
                    failed_count += 1
                elif method == "ocr_low_quality":
                    low_quality_count += 1

            elif paper_path.suffix == ".html":
                method, text = extract_html(paper_path, txt_path)
                text_len = len(text)
                if method == "html_strip":
                    html_count += 1
                else:
                    low_quality_count += 1

            elif paper_path.suffix == ".ps":
                method, text = extract_ps(paper_path, txt_path)
                text_len = len(text)
                if method.startswith("ps_"):
                    ps_count += 1
                    if method == "ps_failed":
                        failed_count += 1

            else:
                method = f"unsupported_{paper_path.suffix}"
                failed_count += 1

        except Exception as e:
            method = f"error: {str(e)[:80]}"
            failed_count += 1

        wall = time.monotonic() - t0
        print(f"{method} ({text_len} chars, {wall:.1f}s)")

        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({
                "file": rel,
                "method": method,
                "text_length": text_len,
                "wall_seconds": round(wall, 2),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }) + "\n")

        if (i + 1) % 50 == 0:
            print(f"  --- Progress: {i+1}/{len(to_process)} | "
                  f"pdftotext={pdftotext_count} ocr={ocr_count} "
                  f"html={html_count} ps={ps_count} "
                  f"low_q={low_quality_count} failed={failed_count}")

    print()
    print("=" * 70)
    print(f"Done. Processed {len(to_process)} files.")
    print(f"  pdftotext:     {pdftotext_count}")
    print(f"  tesseract OCR: {ocr_count}")
    print(f"  html strip:    {html_count}")
    print(f"  postscript:    {ps_count}")
    print(f"  low quality:   {low_quality_count}")
    print(f"  failed:        {failed_count}")
    print(f"  already had:   {already}")
    print(f"  total papers:  {total}")
    print(f"  log: {LOG_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()

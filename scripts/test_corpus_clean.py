#!/usr/bin/env python3
"""Assert the flattened corpus is clean for the WASM search index.

Checks wasm-test/search_data.jsonl and pages/*.json:
- 995 lines, 995 unique dates, zero duplicate dates
- zero null paper_year
- duplicate-day posts (20141204-2, 20150323-2) present under their own stems
- pages/*.json count == 995, every page date == its filename stem

Usage:
  python3 scripts/test_corpus_clean.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JSONL = REPO / "wasm-test" / "search_data.jsonl"
PAGES = REPO / "pages"

EXPECTED = 995
EXPECTED_SUFFIX_DATES = {"20141204-2", "20150323-2"}


def main() -> int:
    failures: list[str] = []

    lines = [ln for ln in JSONL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records = [json.loads(ln) for ln in lines]

    if len(lines) != EXPECTED:
        failures.append(f"JSONL has {len(lines)} lines, expected {EXPECTED}")

    dates = [r["date"] for r in records]
    unique_dates = set(dates)
    if len(unique_dates) != EXPECTED:
        failures.append(
            f"JSONL has {len(unique_dates)} unique dates, expected {EXPECTED}"
        )

    dupes = {d: dates.count(d) for d in unique_dates if dates.count(d) > 1}
    if dupes:
        failures.append(f"duplicate dates in JSONL: {dupes}")

    null_years = [r["date"] for r in records if r.get("paper_year") is None]
    if null_years:
        failures.append(f"null paper_year at dates: {sorted(null_years)}")

    for d in sorted(EXPECTED_SUFFIX_DATES):
        if d not in unique_dates:
            failures.append(f"expected suffix date {d!r} not present in JSONL")

    page_files = sorted(glob.glob(str(PAGES / "*.json")))
    if len(page_files) != EXPECTED:
        failures.append(f"pages/*.json count is {len(page_files)}, expected {EXPECTED}")

    mismatched = []
    for p in page_files:
        stem = os.path.basename(p)[:-5]
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        if d.get("date") != stem:
            mismatched.append((stem, d.get("date")))
    if mismatched:
        failures.append(f"page date != filename stem: {sorted(mismatched)}")

    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"OK: {len(lines)} lines, {len(unique_dates)} unique dates, "
          f"0 null years, 0 dupes, pages={len(page_files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

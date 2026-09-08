#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["whoosh-reloaded>=2.7.5"]
# ///
"""Whoosh oracle index for auditing the hand-rolled WASM BM25 search.

Three sources:
  default    wasm-test/search_data.jsonl — the exact file the shipped
             browser search consumes. Audited from a snapshot copied to
             .tmp/delegation/shipped_search_data.jsonl so concurrent
             regeneration of the live file cannot skew the audit.
  --pages    pages/*.json — recreates the corpus of the abandoned
             tantivy-native index (995 docs, metadata fields) to get
             the oracle and expected index size for that design.
  --md       pages/*.md — the corrected design: index the final page
             text (full blog post) for the reachable set only (JSONL
             date AND an md page). Summaries come from IndexedDB JSON
             in the UI, so they are not indexed.

Subcommands:
  build [--pages|--md] [--report PATH]
                              audit + build .whoosh/, print size breakdown,
                              optionally write a markdown report
  search "query" [--limit N]  BM25 multi-field search
  fuzzy "term" [--dist N]     Levenshtein fuzzy term search
  battery [--pages|--md]      run the queries used to test the live site

Usage:
  ./scripts/whoosh_oracle.py build
  ./scripts/whoosh_oracle.py build --pages
  ./scripts/whoosh_oracle.py build --md
  ./scripts/whoosh_oracle.py build --md --report .tmp/delegation/reports/item00.md
  ./scripts/whoosh_oracle.py search "paxos consensus"
  ./scripts/whoosh_oracle.py battery --md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
JSONL = REPO / "wasm-test" / "search_data.jsonl"
SNAPSHOT = REPO / ".tmp" / "delegation" / "shipped_search_data.jsonl"
PAGES = REPO / "pages"
ARCHIVE = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
)) / "pages"
INDEX_DIR = REPO / ".whoosh"
TANTIVY_BLOB = REPO / "wasm-test" / "tantivy-native" / "tantivy_index.blob"

TEXT_FIELDS = [
    "paper_title",
    "paper_authors",
    "paper_venue",
    "blog_summary",
    "paper_abstract",
    "topics",
    "tags",
]

TEXT_FIELDS_BY_MODE = {
    "default": TEXT_FIELDS,
    "archive": TEXT_FIELDS,
    "pages": TEXT_FIELDS,
    "md": ["blog_title", "body"],
}

DISPLAY_FIELD = {
    "default": "paper_title",
    "archive": "paper_title",
    "pages": "paper_title",
    "md": "blog_title",
}

SOURCE_LABEL = {
    "default": "SHIPPED JSONL SNAPSHOT (what the live WASM eats)",
    "archive": "REAL ARCHIVE",
    "pages": "pages/*.json (abandoned tantivy-native corpus)",
    "md": "pages/*.md reachable from the shipped JSONL (corrected design)",
}

BATTERY = [
    ("blockchai", False, 0),
    ("quantum blockchain", False, 0),
    ("consensus", False, 0),
    ("consensuz", True, 2),
    ("paxos consensus", False, 0),
    ("distributed transactions", False, 0),
    ("compiler optimization", False, 0),
]


def snapshot_jsonl() -> Path:
    """Copy the shipped JSONL to the audit snapshot (other items regenerate it)."""
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(JSONL, SNAPSHOT)
    return SNAPSHOT


def jsonl_date_set() -> set[str]:
    """Unique dates in the shipped JSONL snapshot (993 of 995 lines)."""
    dates = set()
    for line in snapshot_jsonl().read_text().splitlines():
        if not line.strip():
            continue
        doc = json.loads(line)
        if doc.get("date"):
            dates.add(doc["date"])
    return dates


def _load_jsonl(source: Path):
    from whoosh.fields import Schema, TEXT, ID, NUMERIC

    schema = Schema(
        date=ID(stored=True, unique=True),
        slug=ID(stored=True),
        paper_title=TEXT(stored=True),
        paper_authors=TEXT(stored=True),
        paper_year=NUMERIC(stored=True),
        paper_venue=TEXT(stored=True),
        blog_summary=TEXT(stored=True),
        paper_abstract=TEXT(stored=True),
        topics=TEXT(stored=True),
        tags=TEXT(stored=True),
    )
    docs = []
    failures = []
    total = 0
    for lineno, line in enumerate(source.read_text().splitlines(), 1):
        if not line.strip():
            continue
        total += 1
        try:
            doc = json.loads(line)
        except json.JSONDecodeError as e:
            failures.append((lineno, "JSON error", str(e)))
            continue
        missing = [f for f in ("date", "slug", "paper_title") if not doc.get(f)]
        bad_types = []
        if not isinstance(doc.get("paper_authors"), list):
            bad_types.append("paper_authors")
        if not isinstance(doc.get("topics"), list):
            bad_types.append("topics")
        if not isinstance(doc.get("tags"), list):
            bad_types.append("tags")
        if not isinstance(doc.get("paper_year"), int):
            bad_types.append("paper_year")
        if missing or bad_types:
            failures.append(
                (lineno, "schema mismatch",
                 f"missing={missing} bad_types={bad_types} date={doc.get('date')!r}")
            )
            continue
        docs.append({
            "date": doc["date"],
            "slug": doc.get("slug", ""),
            "paper_title": doc.get("paper_title", ""),
            "paper_authors": " ".join(doc.get("paper_authors") or []),
            "paper_year": doc["paper_year"],
            "paper_venue": doc.get("paper_venue", ""),
            "blog_summary": doc.get("blog_summary", ""),
            "paper_abstract": doc.get("paper_abstract", ""),
            "topics": " ".join(doc.get("topics") or []),
            "tags": " ".join(doc.get("tags") or []),
        })
    return schema, docs, failures, total


def load_archive():
    """Load the real archive: pages/*.json, deduping ' 2'/' 3' sync copies."""
    docs = []
    failures = []
    seen = {}
    for path in sorted(ARCHIVE.glob("*.json")):
        date = re.sub(r"\s+\d+$", "", path.stem)
        if date in seen and seen[date] <= path.stat().st_mtime:
            continue
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            failures.append((path.name, str(e)))
            continue
        seen[date] = path.stat().st_mtime
        doc["date"] = date
        docs.append(doc)
    return docs, failures


def _load_pages():
    """Load pages/*.json: all 995 docs, tantivy-lenient field coercion."""
    from whoosh.fields import Schema, TEXT, ID, NUMERIC

    schema = Schema(
        date=ID(stored=True, unique=True),
        slug=ID(stored=True),
        paper_title=TEXT(stored=True),
        paper_authors=TEXT(stored=True),
        paper_year=NUMERIC(stored=True),
        paper_venue=TEXT(stored=True),
        blog_summary=TEXT(stored=True),
        paper_abstract=TEXT(stored=True),
        topics=TEXT(stored=True),
        tags=TEXT(stored=True),
    )
    docs = []
    failures = []
    total = 0
    for path in sorted(PAGES.glob("*.json")):
        total += 1
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            failures.append((path.name, "JSON error", str(e)))
            continue
        try:
            year = int(raw.get("paper_year") or 0)
        except (TypeError, ValueError):
            year = 0
        authors = raw.get("paper_authors")
        if isinstance(authors, list):
            authors = " ".join(str(a) for a in authors)
        elif not isinstance(authors, str):
            authors = ""
        doc = {
            "date": path.stem,
            "slug": raw.get("slug") or "",
            "paper_title": raw.get("paper_title") or "",
            "paper_authors": authors,
            "paper_year": year,
            "paper_venue": raw.get("paper_venue") or "",
            "blog_summary": raw.get("blog_summary") or "",
            "paper_abstract": raw.get("paper_abstract") or "",
            "topics": " ".join(raw.get("topics") or []),
            "tags": " ".join(raw.get("tags") or []),
        }
        docs.append(doc)
    return schema, docs, failures, total


def parse_md():
    """Load pages/*.md for the reachable set: JSONL date AND pages/<date>.md.

    Returns (schema, docs, unreachable) where each doc has
    date, slug, blog_title, body (full md minus front matter).
    """
    from whoosh.fields import Schema, TEXT, ID

    schema = Schema(
        date=ID(stored=True, unique=True),
        slug=ID(stored=True),
        blog_title=TEXT(stored=True),
        body=TEXT(),
    )
    dates = jsonl_date_set()
    docs = []
    unreachable = []
    for path in sorted(PAGES.glob("*.md")):
        if path.stem not in dates:
            unreachable.append(path.stem)
            continue
        text = path.read_text()
        meta = {}
        body = text
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            try:
                end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
                for line in lines[1:end]:
                    key, _, value = line.partition(":")
                    if key.strip():
                        meta[key.strip()] = value.strip()
                body = "\n".join(lines[end + 1:])
            except StopIteration:
                pass
        blog_url = meta.get("blog_url", "")
        slug = blog_url.rstrip("/").rsplit("/", 1)[-1] if blog_url else ""
        docs.append({
            "date": path.stem,
            "slug": slug,
            "blog_title": meta.get("blog_title", ""),
            "body": body,
        })
    return schema, docs, unreachable


def parse_source(mode="default"):
    """Parse the chosen source exactly as the shipped pipeline does."""
    if mode == "pages":
        return _load_pages()
    if mode == "md":
        schema, docs, unreachable = parse_md()
        return schema, docs, [], len(docs) + len(unreachable)
    if mode == "archive":
        from whoosh.fields import Schema, TEXT, ID, NUMERIC

        schema = Schema(
            date=ID(stored=True, unique=True),
            slug=ID(stored=True),
            paper_title=TEXT(stored=True),
            paper_authors=TEXT(stored=True),
            paper_year=NUMERIC(stored=True),
            paper_venue=TEXT(stored=True),
            blog_summary=TEXT(stored=True),
            paper_abstract=TEXT(stored=True),
            topics=TEXT(stored=True),
            tags=TEXT(stored=True),
        )
        docs, failures = load_archive()
        failures = [(name, "read error", detail) for name, detail in failures]
        return schema, docs, failures, len(docs) + len(failures)
    return _load_jsonl(snapshot_jsonl())


def write_index(schema, docs, mode="default"):
    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True)
    from whoosh import index

    ix = index.create_in(str(INDEX_DIR), schema)
    writer = ix.writer()
    fields = schema.names()
    for doc in docs:
        writer.add_document(**{name: doc[name] for name in fields if name in doc})
    writer.commit()
    (INDEX_DIR / "corpus_mode.txt").write_text(mode + "\n")
    files = [f for f in INDEX_DIR.rglob("*") if f.is_file() and f.name != "corpus_mode.txt"]
    return len(files), sum(f.stat().st_size for f in files)


def build(mode="default", report_path=None):
    schema, docs, failures, total = parse_source(mode)
    unreachable = []
    if mode == "md":
        _, _, unreachable = parse_md()

    print(f"Input: {mode} — {SOURCE_LABEL[mode]}")
    print(f"Records found   : {total}")
    print(f"Records indexed : {len(docs)}")
    print(f"Records rejected: {len(failures)}")
    if failures:
        print("\nREJECTED RECORDS (invisible to the WASM search):")
        for ident, kind, detail in failures:
            print(f"  {ident}: {kind}: {detail}")

    dates = [d["date"] for d in docs]
    dupes = {x: dates.count(x) for x in set(dates) if dates.count(x) > 1}
    if dupes:
        print(f"\nDUPLICATE DATES (IndexedDB is keyed by date; dupes clobber each other):")
        for d, n in sorted(dupes.items()):
            print(f"  {d}: {n} records")

    if unreachable:
        print("\nUNREACHABLE MD PAGES:")
        for stem in sorted(unreachable):
            print(f"  {stem}: unreachable — no JSONL date")

    nfiles, size = write_index(schema, docs, mode)
    print(f"\nIndexed {len(docs)} docs into {INDEX_DIR.relative_to(REPO)} "
          f"({nfiles} files, {size:,} bytes)")

    counts = battery_counts(mode)
    print("\nBATTERY RESULT COUNTS (total hits, no limit):")
    for query, hits in counts.items():
        print(f"  {query!r}: {hits}")

    if mode == "pages" and TANTIVY_BLOB.exists():
        blob = TANTIVY_BLOB.stat().st_size
        pct = (size / blob * 100) if blob else 0.0
        print(f"\nTANTIVY COMPARISON: whoosh --pages index {size:,} bytes vs "
              f"abandoned tantivy-native blob {TANTIVY_BLOB.name} {blob:,} bytes "
              f"({pct:.1f}%)")

    if report_path:
        write_report(report_path, mode, total, len(docs), failures, unreachable,
                     nfiles, size, counts)
        print(f"\nReport: {Path(report_path)}")
    return counts


def open_index(mode=None):
    from whoosh import index
    if not INDEX_DIR.exists():
        print("No index. Run: ./scripts/whoosh_oracle.py build", file=sys.stderr)
        sys.exit(1)
    marker = INDEX_DIR / "corpus_mode.txt"
    if mode and marker.exists() and marker.read_text().strip() != mode:
        print(f"Index was built for mode '{marker.read_text().strip()}', "
              f"not '{mode}'. Run: ./scripts/whoosh_oracle.py build {'' if mode == 'default' else '--' + mode}",
              file=sys.stderr)
        sys.exit(1)
    return index.open_dir(str(INDEX_DIR))


def _run(mode, query, use_fuzzy=False, dist=2, limit: Optional[int] = 10):
    """Run one query. Returns (total_hits, top_results)."""
    from whoosh.qparser import MultifieldParser, FuzzyTermPlugin

    ix = open_index(mode)
    with ix.searcher() as s:
        qp = MultifieldParser(TEXT_FIELDS_BY_MODE[mode], schema=ix.schema)
        if use_fuzzy:
            qp.add_plugin(FuzzyTermPlugin())
            q = qp.parse(f"{query}~{dist}")
        else:
            q = qp.parse(query)
        total = len(s.search(q, limit=None))
        results = s.search(q, limit=limit)
        field = DISPLAY_FIELD[mode]
        top = [(r.score, r[field]) for r in results]
        return total, top


def _print_hits(label, query, total, top, limit, dist=None):
    suffix = f"~{dist}" if dist is not None else ""
    print(f"\nWhoosh {label}: '{query}'{suffix} -> {total} hits (showing up to {limit})")
    for score, title in top:
        print(f"  [{score:7.3f}] {title[:70]}")


def search(query, limit=10, mode="default", quiet=False):
    total, top = _run(mode, query, limit=limit)
    if not quiet:
        _print_hits("search", query, total, top, limit)
    return top


def fuzzy(term, dist=2, limit=10, mode="default", quiet=False):
    total, top = _run(mode, term, use_fuzzy=True, dist=dist, limit=limit)
    if not quiet:
        _print_hits("fuzzy", term, total, top, limit, dist=dist)
    return top


def battery_counts(mode="default"):
    """Total hit count for every battery query (limit=None)."""
    counts = {}
    for query, use_fuzzy, dist in BATTERY:
        key = f"{query}~{dist}" if use_fuzzy else query
        counts[key] = _run(mode, query, use_fuzzy=use_fuzzy, dist=dist, limit=None)[0]
    return counts


def battery(mode="default"):
    print("=" * 70)
    print(f"ORACLE BATTERY — {SOURCE_LABEL[mode]}")
    print("=" * 70)
    counts = {}
    for query, use_fuzzy, dist in BATTERY:
        total, top = _run(mode, query, use_fuzzy=use_fuzzy, dist=dist, limit=5)
        counts[f"{query}~{dist}" if use_fuzzy else query] = total
        _print_hits("fuzzy" if use_fuzzy else "search", query, total, top, 5,
                    dist=dist if use_fuzzy else None)
    return counts


def _fmt_list(items):
    items = list(items)
    return "; ".join(str(i) for i in items) if items else "(none)"


def write_report(path, mode, total, indexed, failures, unreachable,
                 nfiles, size, counts):
    """Update the markdown report: replace this mode's section, keep others."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"<!-- whoosh-oracle mode={mode} -->",
        f"## mode: {mode}",
        f"- source: {SOURCE_LABEL[mode]}",
        f"- docs found: {total}",
        f"- docs indexed: {indexed}",
        f"- docs rejected: {len(failures)}",
        f"- rejected list: {_fmt_list(failures)}",
        f"- unreachable md pages ({len(unreachable)}): "
        f"{_fmt_list(stem + ' — unreachable, no JSONL date' for stem in unreachable)}",
        f"- index: .whoosh — {nfiles} files, {size:,} bytes",
    ]
    if mode == "pages" and TANTIVY_BLOB.exists():
        blob = TANTIVY_BLOB.stat().st_size
        pct = (size / blob * 100) if blob else 0.0
        lines.append(
            f"- tantivy comparison: whoosh --pages index {size:,} bytes vs "
            f"abandoned faked tantivy-native blob "
            f"(wasm-test/tantivy-native/tantivy_index.blob) {blob:,} bytes "
            f"— whoosh index is {pct:.1f}% of the blob"
        )
    lines.append("- battery result counts:")
    lines.append("")
    lines.append("| query | total hits |")
    lines.append("|---|---|")
    for query, hits in counts.items():
        lines.append(f"| `{query}` | {hits} |")
    section = "\n".join(lines) + "\n"

    existing = path.read_text() if path.exists() else ""
    marker_start = f"<!-- whoosh-oracle mode={mode} -->"
    parts = existing.split("<!-- whoosh-oracle mode=")
    kept = [parts[0].lstrip("\n")] if parts and parts[0].strip() else []
    for part in parts[1:]:
        if part.startswith(mode + " -->"):
            continue  # drop the old section for this mode
        kept.append("<!-- whoosh-oracle mode=" + part)
    text = "\n".join(kept) + ("\n" if kept else "") + section
    path.write_text(text)


def resolve_mode(args) -> str:
    if getattr(args, "pages", False):
        return "pages"
    if getattr(args, "md", False):
        return "md"
    if getattr(args, "archive", False):
        return "archive"
    return "default"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--archive", action="store_true",
                        help="use the real archive ($MORNING_PAPER_DIR/pages) instead of the shipped JSONL")
    shared.add_argument("--pages", action="store_true",
                        help="index pages/*.json (abandoned tantivy-native corpus)")
    shared.add_argument("--md", action="store_true",
                        help="index reachable pages/*.md (corrected design)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("build", parents=[shared])
    bp.add_argument("--report", metavar="PATH", help="write a markdown report to PATH")
    sp = sub.add_parser("search", parents=[shared])
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10)
    fp = sub.add_parser("fuzzy", parents=[shared])
    fp.add_argument("term")
    fp.add_argument("--dist", type=int, default=2)
    fp.add_argument("--limit", type=int, default=10)
    sub.add_parser("battery", parents=[shared])
    args = ap.parse_args()
    mode = resolve_mode(args)

    if args.cmd == "build":
        build(mode, report_path=args.report)
    elif args.cmd == "search":
        search(args.query, args.limit, mode=mode)
    elif args.cmd == "fuzzy":
        fuzzy(args.term, args.dist, args.limit, mode=mode)
    elif args.cmd == "battery":
        battery(mode)


if __name__ == "__main__":
    main()

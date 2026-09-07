#!/usr/bin/env python3
"""Build a Whoosh search index over the flattened pages/ layout.

Indexes:
- paper_title (TEXT, analyzed)
- paper_authors (TEXT, analyzed)
- blog_summary (TEXT, analyzed)
- paper_abstract (TEXT, analyzed)
- topics (KEYWORD, comma-separated)
- tags (KEYWORD, comma-separated)
- date (ID, stored)
- slug (ID, stored)
- blog_url (ID, stored)
- paper_year (NUMERIC, stored)
- paper_venue (TEXT, analyzed)

Supports: full-text search, fuzzy search, phrase search, field filtering.

Usage:
  python3 scripts/build_whoosh_index.py
  python3 scripts/build_whoosh_index.py --query "paxos"
  python3 scripts/build_whoosh_index.py --fuzzy "consensus"
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

PAGES_DIR = BASE_DIR / "pages"
INDEX_DIR = BASE_DIR / "whoosh_index"

try:
    from whoosh import index
    from whoosh.fields import Schema, TEXT, ID, KEYWORD, NUMERIC, STORED
    from whoosh.analysis import StemmingAnalyzer
    from whoosh.qparser import QueryParser, MultifieldParser
    from whoosh.query import Term, And, Or
except ImportError:
    print("ERROR: whoosh not installed. Run: pip install whoosh", file=sys.stderr)
    sys.exit(1)


def build_schema():
    stem = StemmingAnalyzer()
    return Schema(
        date=ID(stored=True, unique=True),
        slug=ID(stored=True),
        blog_url=ID(stored=True),
        paper_title=TEXT(analyzer=stem, stored=True),
        paper_authors=TEXT(analyzer=stem, stored=True),
        paper_year=NUMERIC(stored=True),
        paper_venue=TEXT(analyzer=stem, stored=True),
        blog_summary=TEXT(analyzer=stem, stored=True),
        paper_abstract=TEXT(analyzer=stem, stored=True),
        topics=KEYWORD(stored=True, commas=True),
        tags=KEYWORD(stored=True, commas=True),
    )


def build_index():
    """Build the Whoosh index from pages/*.json files."""
    if INDEX_DIR.exists():
        import shutil
        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    schema = build_schema()
    ix = index.create_in(str(INDEX_DIR), schema)
    writer = ix.writer()

    json_files = sorted(PAGES_DIR.glob("*.json"))
    print(f"Building Whoosh index from {len(json_files)} JSON files...")

    count = 0
    for jf in json_files:
        try:
            data = json.loads(jf.read_text())
        except Exception as e:
            print(f"  SKIP {jf.name}: {e}")
            continue

        date = data.get("date", jf.stem)
        slug = data.get("slug", "")
        blog_url = data.get("blog_url", "")
        paper_title = data.get("paper_title", "")
        paper_authors = " ".join(data.get("paper_authors", []))
        paper_year = data.get("paper_year", 0)
        paper_venue = data.get("paper_venue", "")
        blog_summary = data.get("blog_summary", "")
        paper_abstract = data.get("paper_abstract", "")
        topics = ",".join(data.get("topics", []))
        tags = ",".join(data.get("tags", []))

        writer.add_document(
            date=date,
            slug=slug,
            blog_url=blog_url,
            paper_title=paper_title,
            paper_authors=paper_authors,
            paper_year=paper_year if paper_year else 0,
            paper_venue=paper_venue,
            blog_summary=blog_summary,
            paper_abstract=paper_abstract,
            topics=topics,
            tags=tags,
        )
        count += 1

    writer.commit()
    print(f"Indexed {count} documents.")

    # Measure index size
    import subprocess
    result = subprocess.run(["du", "-sh", str(INDEX_DIR)], capture_output=True, text=True)
    print(f"Index size: {result.stdout.strip()}")

    # Count segment files
    files = list(INDEX_DIR.rglob("*"))
    files = [f for f in files if f.is_file()]
    print(f"Index files: {len(files)}")
    for f in sorted(files):
        print(f"  {f.name}: {f.stat().st_size:,} bytes")

    return ix


def search(ix, query_str, limit=10):
    """Run a standard search."""
    from whoosh.qparser import MultifieldParser
    with ix.searcher() as searcher:
        qp = MultifieldParser(["paper_title", "paper_authors", "blog_summary",
                               "paper_abstract", "paper_venue"], schema=ix.schema)
        q = qp.parse(query_str)
        results = searcher.search(q, limit=limit)
        print(f"\nSearch: '{query_str}' -> {len(results)} results")
        for r in results:
            print(f"  [{r['date']}] {r['paper_title'][:70]}")
            print(f"    score: {r.score:.3f}")
        return results


def fuzzy_search(ix, query_str, limit=10, maxdist=2):
    """Run a fuzzy search."""
    from whoosh.qparser import MultifieldParser, FuzzyTermPlugin
    with ix.searcher() as searcher:
        qp = MultifieldParser(["paper_title", "paper_authors", "blog_summary",
                               "paper_abstract", "paper_venue"], schema=ix.schema)
        qp.add_plugin(FuzzyTermPlugin())
        q = qp.parse(f"{query_str}~{maxdist}")
        results = searcher.search(q, limit=limit)
        print(f"\nFuzzy search: '{query_str}'~{maxdist} -> {len(results)} results")
        for r in results:
            print(f"  [{r['date']}] {r['paper_title'][:70]}")
            print(f"    score: {r.score:.3f}")
        return results


def main():
    if "--query" in sys.argv:
        query_str = sys.argv[sys.argv.index("--query") + 1]
        ix = index.open_dir(str(INDEX_DIR))
        search(ix, query_str)
        return

    if "--fuzzy" in sys.argv:
        query_str = sys.argv[sys.argv.index("--fuzzy") + 1]
        ix = index.open_dir(str(INDEX_DIR))
        fuzzy_search(ix, query_str)
        return

    build_index()

    # Run some test searches
    ix = index.open_dir(str(INDEX_DIR))
    print("\n" + "=" * 70)
    print("TEST SEARCHES")
    print("=" * 70)

    search(ix, "paxos consensus")
    search(ix, "distributed transactions")
    search(ix, "compiler optimization")
    fuzzy_search(ix, "consensus", maxdist=2)
    fuzzy_search(ix, "paxos", maxdist=1)


if __name__ == "__main__":
    main()

#!/usr/bin/env -S uv -S python3
"""Tavily search wrapper — searches for a paper and returns PDF URLs.

Usage:
  python3 scripts/tavily_search.py "paper title" "author" 2020 "venue"
  python3 scripts/tavily_search.py --json '{"query": "...", "max_results": 10}'
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))

# Load Tavily key from .env
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
if not TAVILY_API_KEY:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("TAVILY_API_KEY="):
                TAVILY_API_KEY = line.split("=", 1)[1].strip()
                break

if not TAVILY_API_KEY:
    print("ERROR: TAVILY_API_KEY not found in .env or environment", file=sys.stderr)
    sys.exit(1)

TAVILY_URL = "https://api.tavily.com/search"

SKIP_DOMAINS = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets",
                "simbo1905", "bbc.co.uk", "youtube.com", "twitter.com", "x.com",
                "amazon.com", "wikipedia.org"]


def tavily_search(query, max_results=10, search_depth="advanced"):
    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_raw_content": False,
    }).encode()

    req = urllib.request.Request(TAVILY_URL, data=payload, headers={
        "Content-Type": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def find_pdf_urls(results):
    """Extract PDF URLs from Tavily results, filtering out junk domains."""
    pdf_urls = []
    other_urls = []
    for r in results.get("results", []):
        url = r.get("url", "")
        if not url or any(d in url for d in SKIP_DOMAINS):
            continue
        if url.lower().endswith(".pdf"):
            pdf_urls.append(url)
        elif any(d in url for d in ["arxiv.org", "usenix.org", "cidrdb.org",
                                     "semanticscholar.org", "researchgate.net",
                                     "hal.science", "vldb.org"]):
            other_urls.append(url)
    return pdf_urls, other_urls


def main():
    if len(sys.argv) < 2:
        print("Usage: tavily_search.py <query> [max_results]", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--json":
        params = json.loads(sys.argv[2])
        query = params.get("query", "")
        max_results = params.get("max_results", 10)
        search_depth = params.get("search_depth", "advanced")
    else:
        query = " ".join(sys.argv[1:])
        max_results = 10
        search_depth = "advanced"

    try:
        result = tavily_search(query, max_results, search_depth)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    pdf_urls, other_urls = find_pdf_urls(result)

    output = {
        "query": query,
        "pdf_urls": pdf_urls,
        "other_urls": other_urls,
        "results": [{"url": r.get("url", ""), "title": r.get("title", "")}
                     for r in result.get("results", [])],
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

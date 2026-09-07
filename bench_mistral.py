#!/usr/bin/env python3
"""Benchmark Mistral Small on the same 12 blog posts as the cancelled Gemma run.

Logs timing and cost to .tmp/bench/mistral_timings.jsonl
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
BENCH_DIR = BASE_DIR / ".tmp" / "bench"
BENCH_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = BENCH_DIR / "mistral_timings.jsonl"

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
if not MISTRAL_API_KEY:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("MISTRAL_API_KEY="):
                MISTRAL_API_KEY = line.split("=", 1)[1].strip()
                break
if not MISTRAL_API_KEY:
    print("ERROR: MISTRAL_API_KEY not found", file=sys.stderr)
    sys.exit(1)

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MODEL = "mistral-small-latest"
# Mistral Small pricing: $0.30/1M input, $0.50/1M output
PROMPT_PRICE = 0.30 / 1_000_000
COMPLETION_PRICE = 0.50 / 1_000_000

ONTOLOGY_DOMAINS = [
    "distributed-systems", "storage-systems", "compilers", "programming-languages",
    "machine-learning", "systems-security", "operating-systems", "networking",
    "formal-methods", "data-engineering", "human-computer-interaction",
    "algorithms", "cloud-computing"
]

def load_urls():
    return json.loads((BASE_DIR / "urls.json").read_text())

def url_to_path(url):
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/(.+?)/?$', url)
    if not m:
        return None, None, None
    year, month, day, slug = m.groups()
    return (BASE_DIR / "content" / year / month / f"{slug}.txt",
            f"{year}/{month}/{day}", slug)

def read_blog_text(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "---":
            content = "\n".join(lines[i+1:])
            break
    else:
        content = text
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    if len(content) > 8000:
        content = content[:8000] + "\n[...truncated...]"
    return content

def extract_paper_link(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r'class="entry-content"[^>]*>(.*)', html, re.DOTALL)
    if not m:
        return None
    content = m.group(1)[:10000]
    links = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', content)
    skip = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets", "simbo1905"]
    for href, text in links:
        if href.startswith("#") or any(d in href for d in skip):
            continue
        return href
    return None

def build_prompt(blog_text, paper_url):
    paper_url_line = f"\nPaper link found in HTML: {paper_url}" if paper_url else "\nNo paper link found in HTML."
    return f"""You are analyzing a blog post from "the morning paper" (blog.acolyer.org), which reviews academic computer science papers.

Below is the text of the blog post.{paper_url_line}

Please analyze the blog post and return a JSON object with the following fields:
{{
  "blog_summary": "A 2-3 paragraph summary of what the blog author (Adrian Colyer) says about the paper. What did he highlight as good, interesting, or noteworthy? What was the key insight?",
  "paper_title": "The full title of the paper being discussed",
  "paper_authors": ["Author Name 1", "Author Name 2", ...],
  "paper_year": 2018,
  "paper_venue": "Conference or journal name (e.g. FAST'18, SOSP, OSDI, PLDI, POPL, etc.) or empty string",
  "topics": ["domain1", "domain2", ...],
  "tags": ["subtopic1", "subtopic2", ...]
}}

For "topics", choose from these domains: {", ".join(ONTOLOGY_DOMAINS)}.
For "tags", use specific subtopic keywords from the paper (e.g. "consensus-protocols", "paxos", "raft", "crash-consistency", "type-inference", etc.).

Return ONLY the JSON object, no other text.

Blog post text:
---
{blog_text}
---"""

def call_mistral(prompt):
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    t0 = time.monotonic()
    for attempt in range(3):
        try:
            req = urllib.request.Request(MISTRAL_URL, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
            wall = time.monotonic() - t0
            usage = result.get("usage", {})
            return {
                "wall_seconds": round(wall, 2),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "error": None,
            }
        except Exception as e:
            wall = time.monotonic() - t0
            if attempt < 2:
                time.sleep(3)
                continue
            return {
                "wall_seconds": round(wall, 2),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "error": str(e),
            }

def main():
    urls = load_urls()
    # Same 12 posts as the cancelled Gemma run (indices 0-11)
    batch = urls[:12]
    print(f"Model: {MODEL}")
    print(f"Posts: {len(batch)}")
    print(f"Log: {LOG_FILE}")
    print()

    results = []
    for i, url in enumerate(batch):
        path, date_path, slug = url_to_path(url)
        if not path or not path.exists():
            print(f"  [{i}] SKIP (no content)")
            continue

        blog_text = read_blog_text(path)
        paper_url = extract_paper_link(url)
        prompt = build_prompt(blog_text, paper_url)

        print(f"  [{i}] {slug[:50]} ...", end=" ", flush=True)
        record = call_mistral(prompt)
        record["model"] = MODEL
        record["index"] = i
        record["blog_slug"] = slug
        record["paper_url"] = paper_url

        # Cost
        if not record["error"]:
            cost = (record["prompt_tokens"] * PROMPT_PRICE +
                    record["completion_tokens"] * COMPLETION_PRICE)
            record["cost_usd"] = round(cost, 6)
        else:
            record["cost_usd"] = 0.0

        record["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        results.append(record)

        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")

        if record["error"]:
            print(f"ERROR: {record['error'][:80]} ({record['wall_seconds']}s)")
        else:
            print(f"{record['wall_seconds']}s | in={record['prompt_tokens']} out={record['completion_tokens']} cost=${record['cost_usd']:.6f}")

    # Summary
    ok = [r for r in results if not r["error"]]
    if not ok:
        print("\nAll calls failed.")
        return

    avg_wall = sum(r["wall_seconds"] for r in ok) / len(ok)
    avg_in = sum(r["prompt_tokens"] for r in ok) / len(ok)
    avg_out = sum(r["completion_tokens"] for r in ok) / len(ok)
    avg_cost = sum(r["cost_usd"] for r in ok) / len(ok)
    total_cost = sum(r["cost_usd"] for r in ok)

    print()
    print("=" * 90)
    print(f"{'Model':<30} {'Wall/call':>9} {'In tok':>7} {'Out tok':>7} {'Cost/call':>10} {'Batch cost':>10} {'Est 996':>10} {'Est time':>9}")
    print("-" * 90)
    est_996_cost = avg_cost * 996
    est_996_time = (avg_wall * 996) / 60
    print(f"{MODEL:<30} {avg_wall:>8.1f}s {avg_in:>7.0f} {avg_out:>7.0f} ${avg_cost:>9.6f} ${total_cost:>9.6f} ${est_996_cost:>9.2f} {est_996_time:>8.0f}m")
    print("=" * 90)

if __name__ == "__main__":
    main()

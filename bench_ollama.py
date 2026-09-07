#!/usr/bin/env python3
"""Benchmark local Ollama models on the same 12 blog posts.

Free, local, no rate limits. Logs to .tmp/bench/ollama_timings.jsonl
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
LOG_FILE = BENCH_DIR / "ollama_timings.jsonl"

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

MODELS = ["mistral-small:latest", "gemma4:26b"]

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
        return None, None
    year, month, day, slug = m.groups()
    return (BASE_DIR / "content" / year / month / f"{slug}.txt", slug)

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

def call_ollama(model, prompt):
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.3},
    }).encode()
    headers = {"Content-Type": "application/json"}

    t0 = time.monotonic()
    pieces = []
    prompt_eval_count = 0
    eval_count = 0

    try:
        req = urllib.request.Request(OLLAMA_URL, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                if line.strip():
                    item = json.loads(line)
                    pieces.append(item.get("response", ""))
                    if item.get("done"):
                        prompt_eval_count = item.get("prompt_eval_count", 0)
                        eval_count = item.get("eval_count", 0)
    except Exception as e:
        wall = time.monotonic() - t0
        return {"wall_seconds": round(wall, 2), "prompt_tokens": 0, "completion_tokens": 0, "error": str(e)}

    wall = time.monotonic() - t0
    return {
        "wall_seconds": round(wall, 2),
        "prompt_tokens": prompt_eval_count,
        "completion_tokens": eval_count,
        "error": None,
    }

def main():
    urls = load_urls()
    batch = urls[:12]

    print(f"Ollama models: {MODELS}")
    print(f"Posts: {len(batch)}")
    print(f"Log: {LOG_FILE}")
    print()

    all_results = []

    for model in MODELS:
        print(f"\n--- {model} ---")
        results = []
        for i, url in enumerate(batch):
            path, slug = url_to_path(url)
            if not path or not path.exists():
                print(f"  [{i}] SKIP (no content)")
                continue

            blog_text = read_blog_text(path)
            paper_url = extract_paper_link(url)
            prompt = build_prompt(blog_text, paper_url)

            print(f"  [{i}] {slug[:50]} ...", end=" ", flush=True)
            record = call_ollama(model, prompt)
            record["model"] = model
            record["index"] = i
            record["blog_slug"] = slug
            record["cost_usd"] = 0.0  # local, free
            record["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            results.append(record)
            all_results.append(record)

            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")

            if record["error"]:
                print(f"ERROR: {record['error'][:80]} ({record['wall_seconds']}s)")
            else:
                print(f"{record['wall_seconds']}s | in={record['prompt_tokens']} out={record['completion_tokens']}")

    # Summary table
    print()
    print("=" * 95)
    print(f"{'Model':<30} {'Wall/call':>9} {'In tok':>7} {'Out tok':>7} {'Cost/call':>10} {'Est 996':>10} {'Est time':>9}")
    print("-" * 95)

    for model in MODELS:
        mr = [r for r in all_results if r["model"] == model and not r["error"]]
        if not mr:
            print(f"{model:<30} {'FAILED':>9}")
            continue
        avg_wall = sum(r["wall_seconds"] for r in mr) / len(mr)
        avg_in = sum(r["prompt_tokens"] for r in mr) / len(mr)
        avg_out = sum(r["completion_tokens"] for r in mr) / len(mr)
        est_time = (avg_wall * 996) / 60
        print(f"{model:<30} {avg_wall:>8.1f}s {avg_in:>7.0f} {avg_out:>7.0f} {'$0.00':>10} {'$0.00':>10} {est_time:>8.0f}m")

    print("=" * 95)

if __name__ == "__main__":
    main()

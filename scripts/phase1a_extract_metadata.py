#!/usr/bin/env -S uv -S python3
"""Phase 1a: Extract metadata from all 996 morning paper blog posts via Mistral Small Latest.

For each blog post:
  1. Read the crawled text from content/YYYY/MM/slug.txt
  2. Fetch the blog HTML to extract the paper link
  3. Send the blog text + paper link to Mistral Small Latest
  4. Parse the JSON response
  5. Write a record to phase1a_metadata.jsonl

Resume capability: skips slugs already in phase1a_metadata.jsonl.
Progress tracked in phase1a_progress.json.

Usage:
  python3 scripts/phase1a_extract_metadata.py
  python3 scripts/phase1a_extract_metadata.py --start 100  # start from index 100
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

# Load API key from .env
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
PROMPT_PRICE = 0.30 / 1_000_000
COMPLETION_PRICE = 0.50 / 1_000_000

OUTPUT_FILE = BASE_DIR / "phase1a_metadata.jsonl"
PROGRESS_FILE = BASE_DIR / "phase1a_progress.json"
TIMINGS_FILE = BASE_DIR / ".tmp" / "bench" / "phase1a_full_timings.jsonl"
TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

ONTOLOGY_DOMAINS = [
    "distributed-systems", "storage-systems", "compilers", "programming-languages",
    "machine-learning", "systems-security", "operating-systems", "networking",
    "formal-methods", "data-engineering", "human-computer-interaction",
    "algorithms", "cloud-computing"
]

SKIP_DOMAINS = ["blog.acolyer.org", "wordpress.com", "hckrnews", "clusterassets", "simbo1905"]


def load_urls():
    return json.loads((BASE_DIR / "urls.json").read_text())


def url_to_path(url):
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/(.+?)/?$', url)
    if not m:
        return None, None, None, None
    year, month, day, slug = m.groups()
    return (BASE_DIR / "content" / year / month / f"{slug}.txt",
            f"{year}/{month}/{day}", slug, url)


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


def read_blog_title(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r'Title:\s*(.+)', text)
    return m.group(1).strip() if m else ""


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
    for href, text in links:
        if href.startswith("#") or any(d in href for d in SKIP_DOMAINS):
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
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {
                "wall_seconds": round(wall, 2),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "content": content,
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
                "content": "",
                "error": str(e),
            }


def parse_json_response(content):
    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Try to extract JSON from markdown code block
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try to find first { ... } block
    m = re.search(r'\{.*\}', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def load_done_slugs():
    done = set()
    if OUTPUT_FILE.exists():
        for line in OUTPUT_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                done.add(rec.get("blog_slug", ""))
            except json.JSONDecodeError:
                pass
    return done


def save_progress(index, total, ok_count, err_count, total_cost):
    PROGRESS_FILE.write_text(json.dumps({
        "index": index,
        "total": total,
        "ok": ok_count,
        "errors": err_count,
        "total_cost_usd": round(total_cost, 4),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))


def main():
    start_index = 0
    if "--start" in sys.argv:
        idx = sys.argv.index("--start")
        start_index = int(sys.argv[idx + 1])

    urls = load_urls()
    total = len(urls)
    done_slugs = load_done_slugs()

    print(f"Model: {MODEL}")
    print(f"Total posts: {total}")
    print(f"Already done: {len(done_slugs)}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Timings: {TIMINGS_FILE}")
    print()

    ok_count = 0
    err_count = 0
    total_cost = 0.0
    t_start = time.monotonic()

    for i, url in enumerate(urls):
        if i < start_index:
            continue

        path, date_path, slug, blog_url = url_to_path(url)
        if not path or not path.exists():
            print(f"  [{i}/{total}] SKIP (no content): {url}")
            continue

        if slug in done_slugs:
            continue

        blog_text = read_blog_text(path)
        blog_title = read_blog_title(path)
        paper_url = extract_paper_link(url)
        prompt = build_prompt(blog_text, paper_url)

        print(f"  [{i}/{total}] {slug[:50]} ...", end=" ", flush=True)

        result = call_mistral(prompt)

        timing = {
            "model": MODEL,
            "index": i,
            "blog_slug": slug,
            "paper_url": paper_url,
            "wall_seconds": result["wall_seconds"],
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "error": result["error"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        if result["error"]:
            err_count += 1
            timing["cost_usd"] = 0.0
            print(f"ERROR: {result['error'][:80]} ({result['wall_seconds']}s)")
        else:
            cost = (result["prompt_tokens"] * PROMPT_PRICE +
                    result["completion_tokens"] * COMPLETION_PRICE)
            timing["cost_usd"] = round(cost, 6)
            total_cost += cost

            parsed = parse_json_response(result["content"])
            if parsed is None:
                err_count += 1
                print(f"PARSE ERROR ({result['wall_seconds']}s)")
                # Save raw content for debugging
                parsed = {
                    "blog_summary": "",
                    "paper_title": "",
                    "paper_authors": [],
                    "paper_year": 0,
                    "paper_venue": "",
                    "topics": [],
                    "tags": [],
                    "_parse_error": True,
                    "_raw_content": result["content"][:500],
                }
            else:
                ok_count += 1
                print(f"{result['wall_seconds']}s | in={result['prompt_tokens']} out={result['completion_tokens']} ${cost:.6f}")

            record = {
                "blog_url": blog_url,
                "blog_title": blog_title,
                "blog_date": date_path,
                "blog_slug": slug,
                "paper_url": paper_url,
                "blog_summary": parsed.get("blog_summary", ""),
                "paper_title": parsed.get("paper_title", ""),
                "paper_authors": parsed.get("paper_authors", []),
                "paper_year": parsed.get("paper_year", 0),
                "paper_venue": parsed.get("paper_venue", ""),
                "topics": parsed.get("topics", []),
                "tags": parsed.get("tags", []),
            }
            if "_parse_error" in parsed:
                record["_parse_error"] = True
                record["_raw_content"] = parsed.get("_raw_content", "")

            with open(OUTPUT_FILE, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            done_slugs.add(slug)

        with open(TIMINGS_FILE, "a") as f:
            f.write(json.dumps(timing) + "\n")

        # Save progress every 10 posts
        if (i + 1) % 10 == 0:
            save_progress(i + 1, total, ok_count, err_count, total_cost)

    save_progress(total, total, ok_count, err_count, total_cost)

    elapsed = time.monotonic() - t_start
    print()
    print("=" * 80)
    print(f"Done. {ok_count} ok, {err_count} errors, {total} total")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Elapsed: {elapsed/60:.1f} min")
    print("=" * 80)


if __name__ == "__main__":
    main()

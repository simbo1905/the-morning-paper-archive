#!/usr/bin/env python3
"""Benchmark OpenRouter gemma-4-26b-a4b-it with the REAL structured prompt.

Same 12 posts, real prompt, real timings, real token counts, real costs.
Logs to .tmp/bench/openrouter_real_timings.jsonl
"""
import json, re, time, urllib.request, os
from pathlib import Path

BASE = Path(os.environ.get("MORNING_PAPER_DIR", "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper"))
LOG = BASE / ".tmp" / "bench" / "openrouter_real_timings.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)

# Read keys
env_text = (BASE / ".env").read_text()
for line in env_text.splitlines():
    if line.startswith("OPENROUTER_API_KEY="):
        API_KEY = line.split("=", 1)[1].strip()
        break
else:
    print("NO OPENROUTER KEY"); exit(1)

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemma-4-26b-a4b-it"
PROMPT_PRICE = 0.00000007
COMPLETION_PRICE = 0.00000034

ONTOLOGY = "distributed-systems, storage-systems, compilers, programming-languages, machine-learning, systems-security, operating-systems, networking, formal-methods, data-engineering, human-computer-interaction, algorithms, cloud-computing"

urls = json.loads((BASE / "urls.json").read_text())

def read_blog(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip() == "---":
            return "\n".join(lines[i+1:])
    return text

def extract_link(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
    except:
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
    pul = f"\nPaper link found in HTML: {paper_url}" if paper_url else "\nNo paper link found in HTML."
    return f"""You are analyzing a blog post from "the morning paper" (blog.acolyer.org), which reviews academic computer science papers.

Below is the text of the blog post.{pul}

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

For "topics", choose from these domains: {ONTOLOGY}.
For "tags", use specific subtopic keywords from the paper (e.g. "consensus-protocols", "paxos", "raft", "crash-consistency", "type-inference", etc.).

Return ONLY the JSON object, no other text.

Blog post text:
---
{blog_text}
---"""

def call(prompt):
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://blog.acolyer.org",
        "X-Title": "Morning Paper Benchmark",
    }
    t0 = time.monotonic()
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, data=payload, headers=headers, method="POST")
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
                time.sleep(5)
                continue
            return {"wall_seconds": round(wall, 2), "prompt_tokens": 0, "completion_tokens": 0, "error": str(e)}

batch = urls[:12]
results = []
for i, url in enumerate(batch):
    m = re.match(r'https://blog\.acolyer\.org/(\d{4})/(\d{2})/(\d{2})/(.+?)/?$', url)
    if not m:
        continue
    year, month, day, slug = m.groups()
    path = BASE / "content" / year / month / f"{slug}.txt"
    if not path.exists():
        continue

    blog_text = read_blog(path)
    blog_text = re.sub(r"\n{3,}", "\n\n", blog_text).strip()[:8000]
    paper_url = extract_link(url)
    prompt = build_prompt(blog_text, paper_url)

    print(f"  [{i}] {slug[:50]} ...", end=" ", flush=True)
    rec = call(prompt)
    rec["model"] = MODEL
    rec["index"] = i
    rec["blog_slug"] = slug
    if not rec["error"]:
        rec["cost_usd"] = round(rec["prompt_tokens"] * PROMPT_PRICE + rec["completion_tokens"] * COMPLETION_PRICE, 6)
    else:
        rec["cost_usd"] = 0.0
    rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    results.append(rec)

    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")

    if rec["error"]:
        print(f"ERROR: {rec['error'][:80]} ({rec['wall_seconds']}s)")
    else:
        print(f"{rec['wall_seconds']}s | in={rec['prompt_tokens']} out={rec['completion_tokens']} cost=${rec['cost_usd']:.6f}")

ok = [r for r in results if not r["error"]]
if ok:
    avg_wall = sum(r["wall_seconds"] for r in ok) / len(ok)
    avg_in = sum(r["prompt_tokens"] for r in ok) / len(ok)
    avg_out = sum(r["completion_tokens"] for r in ok) / len(ok)
    avg_cost = sum(r["cost_usd"] for r in ok) / len(ok)
    total_cost = sum(r["cost_usd"] for r in ok)
    print()
    print("=" * 95)
    print(f"{'Model':<35} {'Wall/call':>9} {'In tok':>7} {'Out tok':>7} {'Cost/call':>10} {'Batch cost':>10} {'Est 996':>10} {'Est time':>9}")
    print("-" * 95)
    print(f"{MODEL:<35} {avg_wall:>8.1f}s {avg_in:>7.0f} {avg_out:>7.0f} ${avg_cost:>9.6f} ${total_cost:>9.6f} ${avg_cost*996:>9.2f} {avg_wall*996/60:>8.0f}m")
    print("=" * 95)

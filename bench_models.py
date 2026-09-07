#!/usr/bin/env python3
"""Benchmark OpenRouter Gemma models: timing, token usage, and cost per call.

Sends the same representative prompt (a real morning paper blog post) to each
model, records wall time, tokens in/out, and computed cost.  Logs to
.tmp/bench/timings.jsonl and prints a summary table.

Usage:
  python3 bench_models.py [--model MODEL] [--runs N] [--post PATH]
  python3 bench_models.py --all --runs 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(os.environ.get(
    "MORNING_PAPER_DIR",
    "/Users/consensussolutions/Library/Mobile Documents/com~apple~CloudDocs/the-morning-paper",
))
BENCH_DIR = BASE_DIR / ".tmp" / "bench"
BENCH_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = BENCH_DIR / "timings.jsonl"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                OPENROUTER_API_KEY = line.split("=", 1)[1].strip()
                break
if not OPENROUTER_API_KEY:
    print("ERROR: OPENROUTER_API_KEY not found", file=sys.stderr)
    sys.exit(1)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 120

# ── Models to benchmark ──────────────────────────────────────────────────────
# pricing is per-token: {prompt: $/token, completion: $/token}
MODELS = {
    "google/gemma-4-26b-a4b-it:free": {"prompt": 0, "completion": 0},
    "google/gemma-4-31b-it:free":     {"prompt": 0, "completion": 0},
    "google/gemma-4-26b-a4b-it":      {"prompt": 0.00000007, "completion": 0.00000034},
    "google/gemma-4-31b-it":          {"prompt": 0.00000009, "completion": 0.00000034},
    "google/gemma-3-4b-it":           {"prompt": 0.00000005, "completion": 0.0000001},
    "google/gemma-3-12b-it":          {"prompt": 0.00000005, "completion": 0.00000015},
    "google/gemma-3-27b-it":          {"prompt": 0.00000008, "completion": 0.00000045},
    "google/gemma-2-27b-it":          {"prompt": 0.00000065, "completion": 0.00000065},
}

# ── Representative prompt (real blog post) ──────────────────────────────────
SAMPLE_POST = BASE_DIR / "content" / "2018" / "02" / "protocol-aware-recovery-for-consensus-based-storage.txt"

def load_sample_prompt() -> str:
    """Build the same prompt that phase1a sends to the LLM."""
    text = SAMPLE_POST.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "---":
            content = "\n".join(lines[i + 1:])
            break
    else:
        content = text
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    if len(content) > 8000:
        content = content[:8000] + "\n[...truncated...]"

    return f"""You are analyzing a blog post from "the morning paper" (blog.acolyer.org), which reviews academic computer science papers.

Below is the text of the blog post.
Paper link found in HTML: https://www.usenix.org/conference/fast18/presentation/alagappan

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

For "topics", choose from these domains: distributed-systems, storage-systems, compilers, programming-languages, machine-learning, systems-security, operating-systems, networking, formal-methods, data-engineering, human-computer-interaction, algorithms, cloud-computing.
For "tags", use specific subtopic keywords from the paper (e.g. "consensus-protocols", "paxos", "raft", "crash-consistency", "type-inference", etc.).

Return ONLY the JSON object, no other text.

Blog post text:
---
{content}
---"""


def call_openrouter(model: str, prompt: str) -> dict:
    """Call OpenRouter and return timing + usage data."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://blog.acolyer.org",
        "X-Title": "Morning Paper Benchmark",
    }

    t0 = time.monotonic()
    error = None
    result = None

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                OPENROUTER_URL, data=payload, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                result = json.loads(resp.read().decode())
            error = None
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            error = f"HTTP {e.code}: {body[:200]}"
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
            else:
                time.sleep(3)
        except Exception as e:
            error = str(e)
            time.sleep(3)

    wall = time.monotonic() - t0

    if result and "choices" in result:
        usage = result.get("usage", {})
        return {
            "model": model,
            "wall_seconds": round(wall, 2),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "error": None,
        }
    else:
        return {
            "model": model,
            "wall_seconds": round(wall, 2),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "error": error,
        }


def compute_cost(record: dict, pricing: dict) -> float:
    if record["error"]:
        return 0.0
    prompt_cost = record["prompt_tokens"] * pricing["prompt"]
    completion_cost = record["completion_tokens"] * pricing["completion"]
    return round(prompt_cost + completion_cost, 6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=str, default=None, help="Single model to test")
    parser.add_argument("--all", action="store_true", help="Test all models")
    parser.add_argument("--runs", type=int, default=1, help="Runs per model")
    parser.add_argument("--post", type=str, default=None, help="Path to blog post file")
    args = parser.parse_args()

    if args.post:
        global SAMPLE_POST
        SAMPLE_POST = Path(args.post)

    if args.model:
        models = {args.model: MODELS.get(args.model, {"prompt": 0, "completion": 0})}
    elif args.all:
        models = MODELS
    else:
        parser.error("Specify --model or --all")

    prompt = load_sample_prompt()
    prompt_chars = len(prompt)
    print(f"Prompt size: {prompt_chars} chars")
    print(f"Models: {len(models)} | Runs per model: {args.runs}")
    print(f"Log: {LOG_FILE}")
    print()

    results = []

    for model_id, pricing in models.items():
        for run in range(args.runs):
            print(f"  {model_id} run {run + 1}/{args.runs} ...", end=" ", flush=True)
            record = call_openrouter(model_id, prompt)
            record["run"] = run
            record["prompt_chars"] = prompt_chars
            record["cost_usd"] = compute_cost(record, pricing)
            record["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            results.append(record)

            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")

            if record["error"]:
                print(f"ERROR: {record['error'][:80]} ({record['wall_seconds']}s)")
            else:
                print(
                    f"{record['wall_seconds']}s | "
                    f"in={record['prompt_tokens']} out={record['completion_tokens']} "
                    f"cost=${record['cost_usd']:.6f}"
                )

            time.sleep(1)

    # Summary table
    print()
    print("=" * 100)
    print(f"{'Model':<40} {'Wall(s)':>8} {'In tok':>7} {'Out tok':>7} {'Cost/call':>10} {'Est 996':>10} {'Est time':>10}")
    print("-" * 100)

    for model_id in models:
        model_results = [r for r in results if r["model"] == model_id and not r["error"]]
        if not model_results:
            print(f"{model_id:<40} {'FAILED':>8}")
            continue

        avg_wall = sum(r["wall_seconds"] for r in model_results) / len(model_results)
        avg_in = sum(r["prompt_tokens"] for r in model_results) / len(model_results)
        avg_out = sum(r["completion_tokens"] for r in model_results) / len(model_results)
        avg_cost = sum(r["cost_usd"] for r in model_results) / len(model_results)
        est_total_cost = avg_cost * 996
        est_total_time_min = (avg_wall * 996) / 60

        print(
            f"{model_id:<40} {avg_wall:>8.1f} {avg_in:>7.0f} {avg_out:>7.0f} "
            f"${avg_cost:>9.6f} ${est_total_cost:>9.2f} {est_total_time_min:>9.1f}m"
        )

    print("=" * 100)


if __name__ == "__main__":
    main()

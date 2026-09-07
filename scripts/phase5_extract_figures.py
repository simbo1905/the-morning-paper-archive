#!/usr/bin/env -S uv -S python3
"""Phase 5: LLM figure extraction via Mistral Small Latest.

For each paper with extracted text (papers/YYYY/MM/DD/slug.txt):
  1. Send the first ~8000 chars of text to Mistral Small
  2. Ask: how many figures, what are their titles/captions
  3. Parse JSON response
  4. Write to papers/YYYY/MM/DD/slug.figures.json

Resume: skips papers that already have a .figures.json file.

Usage:
  python3 scripts/phase5_extract_figures.py
  python3 scripts/phase5_extract_figures.py --start 100
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

PAPERS_DIR = BASE_DIR / "papers"
PROGRESS_FILE = BASE_DIR / "phase5_progress.json"
TIMINGS_FILE = BASE_DIR / ".tmp" / "bench" / "phase5_timings.jsonl"
TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

MAX_TEXT_CHARS = 8000


def build_prompt(paper_text: str, paper_title: str = "") -> str:
    title_line = f"\nPaper title: {paper_title}" if paper_title else ""
    return f"""You are analyzing the text of an academic computer science paper.{title_line}

Below is the extracted text of the paper (first {MAX_TEXT_CHARS} characters).

Please identify all figures in the paper and return a JSON object with the following structure:
{{
  "figures": [
    {{
      "number": "Figure 1",
      "title": "Short title or label for the figure",
      "caption": "The full caption text of the figure, if present"
    }},
    {{
      "number": "Figure 2",
      "title": "Short title or label for the figure",
      "caption": "The full caption text, if present"
    }}
  ]
}}

Rules:
- Look for patterns like "Figure 1", "Fig. 1", "Fig 1", "Figure 1:" etc.
- For each figure, extract its number, a short title (derived from the caption or context), and the full caption text.
- If no caption is present, set "caption" to null.
- If no figures are found, return an empty array: {{"figures": []}}
- Return ONLY the JSON object, no other text.

Paper text:
---
{paper_text}
---"""


def call_mistral(prompt: str) -> dict:
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


def parse_figures_json(content: str) -> list:
    """Parse the JSON response from Mistral."""
    # Try to extract JSON from the response
    # Sometimes the model wraps it in markdown code blocks
    content = content.strip()
    if content.startswith("```"):
        # Remove markdown code block
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        content = content.strip()

    try:
        data = json.loads(content)
        if isinstance(data, dict) and "figures" in data:
            return data["figures"]
        elif isinstance(data, list):
            return data
        else:
            return []
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        m = re.search(r'\{[^{}]*"figures"[^{}]*\[(?:[^{}\]]*)\][^{}]*\}', content, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
                return data.get("figures", [])
            except json.JSONDecodeError:
                pass
        return []


def main():
    start_index = 0
    if "--start" in sys.argv:
        start_index = int(sys.argv[sys.argv.index("--start") + 1])

    # Find all text files
    txt_files = sorted(PAPERS_DIR.rglob("*.txt"))
    total = len(txt_files)
    print(f"Phase 5: Figure extraction for {total} paper text files")

    # Count already done
    already = 0
    to_process = []
    for txt in txt_files:
        fig_path = txt.with_suffix(".figures.json")
        if fig_path.exists():
            already += 1
        else:
            to_process.append(txt)

    print(f"  Already done: {already}")
    print(f"  To process: {len(to_process)}")
    print()

    if start_index > 0:
        to_process = to_process[start_index:]
        print(f"  Starting from index {start_index}, {len(to_process)} remaining")
        print()

    total_cost = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    ok_count = 0
    error_count = 0

    for i, txt_path in enumerate(to_process):
        slug = txt_path.stem
        fig_path = txt_path.with_suffix(".figures.json")

        # Skip if already done
        if fig_path.exists():
            continue

        rel = str(txt_path.relative_to(BASE_DIR))
        print(f"  [{i+1}/{len(to_process)}] {slug[:50]} ...", end=" ", flush=True)

        # Read paper text (first MAX_TEXT_CHARS chars)
        try:
            text = txt_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"READ ERROR: {e}")
            error_count += 1
            continue

        if len(text) < 100:
            print("SKIP (too short)")
            # Write empty figures
            fig_path.write_text(json.dumps({"figures": []}))
            continue

        paper_text = text[:MAX_TEXT_CHARS]
        if len(text) > MAX_TEXT_CHARS:
            paper_text += "\n[...truncated...]"

        # Get paper title from metadata if available
        paper_title = ""
        # Try to extract title from first few lines
        for line in text[:500].splitlines():
            if len(line.strip()) > 10 and not line.strip().startswith("http"):
                paper_title = line.strip()
                break

        prompt = build_prompt(paper_text, paper_title)
        result = call_mistral(prompt)

        if result["error"]:
            print(f"API ERROR: {result['error'][:50]}")
            error_count += 1
            with open(TIMINGS_FILE, "a") as f:
                f.write(json.dumps({
                    "file": rel, "status": "error",
                    "error": result["error"][:100],
                    "wall_seconds": result["wall_seconds"],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }) + "\n")
            continue

        figures = parse_figures_json(result["content"])

        # Write figures JSON
        fig_path.write_text(json.dumps({"figures": figures}, indent=2, ensure_ascii=False))

        # Track costs
        pt = result["prompt_tokens"]
        ct = result["completion_tokens"]
        cost = pt * PROMPT_PRICE + ct * COMPLETION_PRICE
        total_cost += cost
        total_prompt_tokens += pt
        total_completion_tokens += ct
        ok_count += 1

        print(f"OK ({result['wall_seconds']:.1f}s, {len(figures)} figs, "
              f"${cost:.4f}, {pt}+{ct} tok)")

        with open(TIMINGS_FILE, "a") as f:
            f.write(json.dumps({
                "file": rel, "status": "ok",
                "figures_count": len(figures),
                "wall_seconds": result["wall_seconds"],
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "cost_usd": round(cost, 6),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }) + "\n")

        if (i + 1) % 50 == 0:
            PROGRESS_FILE.write_text(json.dumps({
                "index": i + 1, "total": len(to_process),
                "ok": ok_count, "errors": error_count,
                "total_cost_usd": round(total_cost, 4),
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }))

    PROGRESS_FILE.write_text(json.dumps({
        "index": len(to_process), "total": len(to_process),
        "ok": ok_count, "errors": error_count,
        "total_cost_usd": round(total_cost, 4),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "already": already,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))

    print()
    print("=" * 70)
    print(f"Done. Processed {len(to_process)} files.")
    print(f"  OK:     {ok_count}")
    print(f"  Errors: {error_count}")
    print(f"  Already: {already}")
    print(f"  Total cost: ${total_cost:.4f}")
    print(f"  Total tokens: {total_prompt_tokens} prompt + {total_completion_tokens} completion")
    print("=" * 70)


if __name__ == "__main__":
    main()

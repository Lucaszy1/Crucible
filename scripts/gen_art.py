#!/usr/bin/env python3
"""Build-time art for the cinematic arena (Phase 0).

Generates original art with Muse Image on the existing Meta Model
API key and commits it under dashboard/assets/art/, so the
dashboard stays fully offline at runtime. Hard caps: at most 12
images and 250K total tokens per run. Every call logs a spend line
to runs/spend.jsonl with a conservative placeholder estimate,
because image pricing is unknown; tokens are the honest measure.

Never prints or logs the API key. Usage:
    python scripts/gen_art.py            # generate the full set
    python scripts/gen_art.py --list     # show what would generate
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART_DIR = ROOT / "dashboard" / "assets" / "art"
SPEND_FILE = ROOT / "runs" / "spend.jsonl"

MODEL = "muse-image-1.0"
MAX_IMAGES = 12
MAX_TOKENS = 250_000
# Placeholder image rates (USD per 1K tokens), deliberately high:
# five times the Spark text rates. The true price is unknown, so
# these feed the spend guard only, never an invoice.
PRICE_PER_1K = (0.010, 0.030)

# Original designs only: abstract light-and-code entities for a
# cyber defense exercise. No people, no weapons, no gore, no
# existing characters, logos, or brands.
PIECES = [
    {
        "name": "backdrop-hall",
        "file": "backdrop-hall.webp",
        "size": "1792x1024",
        "prompt": (
            "Wide establishing view of a dark futuristic network operations hall: "
            "a vast dim cavern of deep blue-black metal, rows of faint server monoliths "
            "receding into haze on both sides, thin cyan light strips tracing the floor, "
            "soft volumetric god-rays falling from high vents, tiny drifting dust motes, "
            "empty darker stage floor in the center foreground for gameplay, no people, "
            "no text, no logos, cinematic, moody, ultra clean digital matte painting"
        ),
    },
    {
        "name": "intrusion-splash",
        "file": "intrusion-splash.webp",
        "size": "1024x1024",
        "prompt": (
            "Abstract hostile digital entity of glitching crimson code fragments and "
            "jagged wireframe shards, hovering diamond silhouette with sharp angular "
            "edges, cracked glass polygons orbiting a dark core, red scanline glitches "
            "and drifting glyph shards, deep black background with faint red haze, "
            "centered composition with empty margin around it, no people, no weapons, "
            "no text, no logos, original design, dramatic game splash art"
        ),
    },
    {
        "name": "guardian-splash",
        "file": "guardian-splash.webp",
        "size": "1024x1024",
        "prompt": (
            "Luminous abstract cyber guardian sentinel of concentric cyan rings and "
            "smooth hexagonal shield plates, calm symmetrical mandala silhouette "
            "floating upright, soft blue glow halo, delicate circuit filigree between "
            "the rings, deep black background with faint blue haze, centered "
            "composition with empty margin around it, no people, no weapons, no text, "
            "no logos, original design, dramatic game splash art"
        ),
    },
    {
        "name": "fx-streak",
        "file": "fx-streak.webp",
        "size": "512x512",
        "prompt": (
            "Single horizontal white-cyan light streak with soft falloff on a pure "
            "black background, thin bright core fading to transparent glow at both "
            "ends, subtle chromatic fringe, centered, minimal game effect sprite, "
            "no text"
        ),
    },
    {
        "name": "fx-ring",
        "file": "fx-ring.webp",
        "size": "512x512",
        "prompt": (
            "Thin luminous shockwave ring, pale cyan-white with a soft outer glow, "
            "perfect circle centered on a pure black background, crisp inner edge, "
            "minimal game effect sprite, no text"
        ),
    },
    {
        "name": "fx-shard",
        "file": "fx-shard.webp",
        "size": "512x512",
        "prompt": (
            "One jagged translucent crystal data shard, pale violet glass with bright "
            "edge highlights and inner refractions, floating centered on a pure black "
            "background, angled dynamically, minimal game effect sprite, no text"
        ),
    },
    {
        "name": "fx-noise",
        "file": "fx-noise.webp",
        "size": "256x256",
        "prompt": (
            "Seamless tileable grayscale film grain noise texture, fine uniform "
            "monochrome static at medium density, flat even coverage edge to edge, "
            "no shapes, no text"
        ),
    },
    {
        "name": "fx-flare",
        "file": "fx-flare.webp",
        "size": "512x512",
        "prompt": (
            "Soft anamorphic lens flare, small bright white core with wide horizontal "
            "blue streak and one faint hexagonal ghost below it, centered on a pure "
            "black background, subtle and clean, minimal game effect sprite, no text"
        ),
    },
]


def log_spend(model: str, input_tokens: int, output_tokens: int) -> float:
    """Append one spend line; returns the placeholder estimate."""
    per_in, per_out = PRICE_PER_1K
    cost = (input_tokens / 1000.0) * per_in + (output_tokens / 1000.0) * per_out
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SPEND_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "day": date.today().isoformat(),
            "ts": time.time(),
            "model": model,
            "exercise_id": "phase0-art",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "cached": False,
            "host": "meta",
            "note": "placeholder image rates; true price unknown",
        }) + "\n")
    return cost


def generate(client, piece: dict) -> tuple[int, int]:
    """Generate one piece, write the file, log spend. Returns tokens."""
    for attempt in range(3):
        try:
            res = client.images.generate(
                model=MODEL, prompt=piece["prompt"],
                size=piece["size"], n=1,
            )
            break
        except Exception as exc:
            name = type(exc).__name__
            if attempt < 2 and ("ate" in name or "ime" in name or "onnection" in name):
                time.sleep(2 * (attempt + 1))
                continue
            raise
    data = res.data[0]
    payload = data.b64_json or ""
    if not payload and getattr(data, "url", None):
        import urllib.request

        with urllib.request.urlopen(data.url, timeout=120) as res:
            raw = res.read()
        ART_DIR.joinpath(piece["file"]).write_bytes(raw)
    else:
        ART_DIR.joinpath(piece["file"]).write_bytes(base64.b64decode(payload))
    usage = res.usage or {}
    in_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    out_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost = log_spend(MODEL, in_tokens, out_tokens)
    print(f"{piece['name']}: {piece['size']} in={in_tokens} out={out_tokens} "
          f"est=${cost:.4f} -> {piece['file']}")
    return in_tokens, out_tokens


def write_provenance(generated_on: str | None = None) -> None:
    stamp = generated_on or date.today().strftime("%b %d %Y")
    lines = [
        "# Art provenance",
        "",
        "Original art generated at build time for the cinematic arena.",
        f"Generated: {stamp}.",
        f"Model: {MODEL} on the Meta Model API ({os.environ.get('MODEL_API_BASE', 'https://api.meta.ai/v1')}).",
        "All prompts describe original abstract designs: no existing characters,",
        "logos, brands, people, weapons, or gore. Files are committed so the",
        "dashboard runs fully offline.",
        "",
    ]
    for piece in PIECES:
        lines += [f"## {piece['file']} ({piece['size']})", "", piece["prompt"], ""]
    ART_DIR.joinpath("PROVENANCE.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate dashboard art.")
    parser.add_argument("--list", action="store_true", help="list pieces only")
    args = parser.parse_args(argv)
    if args.list:
        for piece in PIECES:
            print(f"{piece['name']}: {piece['size']} -> {piece['file']}")
        print(f"{len(PIECES)} images, caps: {MAX_IMAGES} images, {MAX_TOKENS} tokens")
        return 0
    if len(PIECES) > MAX_IMAGES:
        print(f"refusing: {len(PIECES)} pieces exceed the {MAX_IMAGES}-image cap")
        return 1
    try:
        from openai import OpenAI
    except ImportError:
        print("the openai package is required (see .venv)")
        return 1
    api_key = os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        print("MODEL_API_KEY is not set")
        return 1
    client = OpenAI(api_key=api_key,
                    base_url=os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1"))
    ART_DIR.mkdir(parents=True, exist_ok=True)
    total_tokens = 0
    total_cost = 0.0
    for piece in PIECES:
        if ART_DIR.joinpath(piece["file"]).exists():
            print(f"{piece['name']}: already exists, skipping")
            continue
        in_tokens, out_tokens = generate(client, piece)
        total_tokens += in_tokens + out_tokens
        per_in, per_out = PRICE_PER_1K
        total_cost += (in_tokens / 1000.0) * per_in + (out_tokens / 1000.0) * per_out
        if total_tokens > MAX_TOKENS:
            print(f"stopping: token budget {MAX_TOKENS} exceeded")
            return 1
    write_provenance()
    print(f"done: {total_tokens} tokens, est=${total_cost:.4f} (placeholder rates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

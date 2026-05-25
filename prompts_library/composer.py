#!/usr/bin/env python3
"""
composer.py — assemble prompts from the Shine It prompt library.

Reads ``prompts_library/index.json`` and lets you:
  * list   — list all patterns (optionally filtered by tag)
  * show   — print a single pattern's metadata
  * apply  — assemble a final prompt from one or more patterns

Examples::

    # See everything
    python3 composer.py list

    # See only foam patterns
    python3 composer.py list --tag foam

    # See one pattern in full
    python3 composer.py show foam/realistic_flat_suds

    # Build a final prompt by applying foam + shape rules onto a base instruction,
    # substituting [OBJECT] = "shell housing"
    python3 composer.py apply \\
        --base "Take the stained shell housing in the reference image." \\
        --pattern foam/realistic_flat_suds \\
        --object "shell housing"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

LIB_ROOT = Path(__file__).resolve().parent
INDEX_PATH = LIB_ROOT / "index.json"


def load_index() -> dict[str, Any]:
    if not INDEX_PATH.exists():
        sys.exit(f"[ERR] {INDEX_PATH} not found")
    with INDEX_PATH.open() as f:
        return json.load(f)


def find_pattern(index: dict[str, Any], pattern_id: str) -> dict[str, Any]:
    for p in index["patterns"]:
        if p["id"] == pattern_id:
            return p
    sys.exit(f"[ERR] pattern not found: {pattern_id!r}")


def cmd_list(args: argparse.Namespace) -> None:
    idx = load_index()
    patterns = idx["patterns"]
    if args.tag:
        patterns = [p for p in patterns if args.tag in p.get("tags", [])]
    if not patterns:
        print("(no patterns)")
        return
    print(f"{'ID':<40} {'TAGS':<35} USE CASE")
    print("─" * 100)
    for p in patterns:
        tags = ",".join(p.get("tags", []))[:34]
        use = p.get("use_case", "")[:60]
        print(f"{p['id']:<40} {tags:<35} {use}")


def cmd_show(args: argparse.Namespace) -> None:
    idx = load_index()
    p = find_pattern(idx, args.pattern_id)
    print(json.dumps(p, indent=2, ensure_ascii=False))


def cmd_apply(args: argparse.Namespace) -> None:
    idx = load_index()
    parts: list[str] = []
    if args.base:
        parts.append(args.base.strip())

    all_negatives: list[str] = []
    for pid in args.pattern:
        p = find_pattern(idx, pid)
        snippet = p["snippet"]
        if args.object:
            snippet = snippet.replace("[OBJECT]", args.object)
        parts.append(snippet)
        all_negatives.extend(p.get("negatives", []))

    if all_negatives:
        # dedupe while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for n in all_negatives:
            if n not in seen:
                seen.add(n)
                deduped.append(n)
        parts.append("Negatives: " + "; ".join(deduped) + ".")

    final = "\n\n".join(parts)
    if args.out:
        Path(args.out).write_text(final)
        print(f"[OK] wrote {args.out} ({len(final)} chars)")
    else:
        print(final)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List patterns")
    p_list.add_argument("--tag", help="Filter by tag (e.g. foam, dust)")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one pattern's JSON")
    p_show.add_argument("pattern_id")
    p_show.set_defaults(func=cmd_show)

    p_apply = sub.add_parser("apply", help="Assemble a final prompt")
    p_apply.add_argument("--base", help="Base instruction to prepend")
    p_apply.add_argument(
        "--pattern",
        action="append",
        default=[],
        required=True,
        help="Pattern id (repeatable). e.g. --pattern foam/realistic_flat_suds",
    )
    p_apply.add_argument(
        "--object",
        help="Value to substitute for the [OBJECT] placeholder in snippets",
    )
    p_apply.add_argument("--out", help="Write to file instead of stdout")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

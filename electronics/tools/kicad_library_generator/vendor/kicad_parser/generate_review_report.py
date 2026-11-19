#!/usr/bin/env python3
"""Generate a human review checklist from pipeline payload/report JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def lines_for_items(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines.extend(["- None", ""])
        return lines
    for item in items:
        field = item.get("field", "unknown")
        reason = item.get("reason", "No reason recorded")
        tool = item.get("tool", "")
        prefix = f"{tool}: " if tool else ""
        lines.append(f"- [ ] `{prefix}{field}` - {reason}")
    lines.append("")
    return lines


def build_report(payload: dict[str, Any], generation: dict[str, Any]) -> str:
    payload_summary = payload.get("validation_summary", {})
    generation_summary = generation.get("validation_summary", {})
    footprint = payload.get("mcp_payloads", {}).get("footprint", {})
    selected_rule = payload.get("selected_package_rule", {})

    lines = [
        "# KiCad Symbol/Footprint Review Checklist",
        "",
        f"- Package rule: `{selected_rule.get('id', 'unknown')}`",
        f"- Confidence score: `{payload_summary.get('quality', {}).get('score_out_of_10', 'n/a')}/10`",
        f"- Production gate: `{generation_summary.get('production_gate', payload_summary.get('production_gate', {})).get('pass', False)}`",
        "",
    ]
    lines.extend(lines_for_items("Missing Required Fields", payload_summary.get("missing_required_fields", [])))
    lines.extend(lines_for_items("Needs Human Or Official-Source Validation", payload_summary.get("needs_validation", [])))

    review_status = footprint.get("review_status", {})
    lines.extend(
        [
            "## Footprint Review Status",
            "",
            f"- Review file loaded: `{review_status.get('review_file_loaded', False)}`",
            f"- Review confirmed: `{review_status.get('confirmed', False)}`",
            f"- Orientation confirmed: `{review_status.get('orientation_confirmed', False)}`",
            f"- Pad numbering confirmed: `{review_status.get('pad_numbering_confirmed', False)}`",
            "",
        ]
    )

    payload_data = footprint.get("payload", {})
    lines.extend(
        [
            "## Generated Footprint Payload",
            "",
            f"- Library: `{payload_data.get('libraryPath', '')}`",
            f"- Name: `{payload_data.get('name', '')}`",
            f"- Pad count: `{len(payload_data.get('pads', []))}`",
            "",
        ]
    )

    generation_fp = generation.get("footprint", {})
    validation = generation_fp.get("validation", {})
    lines.extend(lines_for_items("Geometry/File Validation Errors", [{"field": "footprint", "reason": item} for item in validation.get("errors", [])]))
    warnings = validation.get("warnings", [])
    lines.extend(
        [
            "## Geometry/File Validation Warnings",
            "",
            *([f"- {item}" for item in warnings] if warnings else ["- None"]),
            "",
        ]
    )

    lines.extend(
        [
            "## Production Approval",
            "",
            "- [ ] Pin names/numbers match datasheet terminal table.",
            "- [ ] Symbol electrical pin types are acceptable for ERC.",
            "- [ ] Footprint pad coordinates and pad numbers match datasheet or official footprint.",
            "- [ ] Pin-1 marker/orientation is visually confirmed.",
            "- [ ] Courtyard, fab, and silkscreen are acceptable for the target KiCad library policy.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a KiCad library review checklist.")
    parser.add_argument("--payload", required=True, help="Resolver payload JSON.")
    parser.add_argument("--generation-report", default="", help="Generation report JSON.")
    parser.add_argument("--output", required=True, help="Output markdown path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = load_json(Path(args.payload))
    generation = load_json(Path(args.generation_report)) if args.generation_report else {}
    report = build_report(payload, generation)
    write_text(Path(args.output), report)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

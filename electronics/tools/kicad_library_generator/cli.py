"""Command line interface for schema-driven KiCad library generation."""

from __future__ import annotations

import argparse
import json

from .library_generation import generate_kicad_libraries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kicad-library-generator")
    parser.add_argument("schema_json", help="normalized component schema JSON")
    parser.add_argument("--library-root", required=True)
    parser.add_argument("--manifest", help="optional datasheet manifest for identity/hash check")
    parser.add_argument("--component-id")
    parser.add_argument("--symbol-library-name")
    parser.add_argument("--footprint-library-name")
    parser.add_argument("--footprint-name")
    parser.add_argument("--orientation")
    parser.add_argument("--allow-draft-footprint", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = generate_kicad_libraries(
            schema_path=args.schema_json,
            library_root=args.library_root,
            manifest_path=args.manifest,
            component_id=args.component_id,
            symbol_library_name=args.symbol_library_name,
            footprint_library_name=args.footprint_library_name,
            footprint_name=args.footprint_name,
            orientation=args.orientation,
            allow_draft_footprint=args.allow_draft_footprint,
            overwrite=args.overwrite,
            timeout_seconds=args.timeout_seconds,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["success"] else 2
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

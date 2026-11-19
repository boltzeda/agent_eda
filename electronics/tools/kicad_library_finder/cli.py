"""Command line interface for the installed KiCad library finder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .library_finder import find_installed_libraries


def _schema_queries(path: str) -> tuple[str, str]:
    schema = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    component = schema.get("component") or {}
    package = schema.get("package") or {}
    return str(component.get("mpn") or ""), str(package.get("name") or package.get("package_code") or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kicad-library-finder")
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--schema-json", help="derive exact MPN and package from normalized schema")
    query.add_argument("--symbol-name", help="exact symbol name (usually the MPN)")
    parser.add_argument("--package-name", help="package name for footprint candidate search")
    parser.add_argument("--footprint-name", help="optional exact footprint name")
    parser.add_argument("--symbols-dir", help="override symbol library root")
    parser.add_argument("--footprints-dir", help="override footprint library root")
    parser.add_argument("--max-footprint-matches", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        symbol_name = args.symbol_name
        package_name = args.package_name
        if args.schema_json:
            schema_symbol, schema_package = _schema_queries(args.schema_json)
            symbol_name = symbol_name or schema_symbol
            package_name = package_name or schema_package
        if not symbol_name:
            raise ValueError("normalized schema does not contain component.mpn; pass --symbol-name")
        result = find_installed_libraries(
            symbol_name=symbol_name,
            package_name=package_name,
            footprint_name=args.footprint_name,
            symbols_dir=args.symbols_dir,
            footprints_dir=args.footprints_dir,
            max_footprint_matches=args.max_footprint_matches,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

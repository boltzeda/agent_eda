"""Command line interface for the component datasheet workflow."""

from __future__ import annotations

import argparse
import json
import sys

from .datasheet import prepare_datasheet
from .parser import parse_datasheet
from .schema_validation import validate_component_schema


def _emit(value: dict) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="component-datasheet", description="Acquire and parse a datasheet for a CAD-neutral component schema.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="download or copy and validate a datasheet PDF")
    prepare.add_argument("--manufacturer", required=True)
    prepare.add_argument("--mpn", required=True)
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf-path")
    source.add_argument("--pdf-url")
    prepare.add_argument("--source-type", choices=["manufacturer", "authorized_distributor_mirror", "user_provided"])
    prepare.add_argument("--output-dir", required=True)

    parse = subparsers.add_parser("parse", help="run this workflow's MinerU parser adapter")
    parse.add_argument("--manifest", required=True)
    parse.add_argument("--output-dir")
    parse.add_argument("--backend", default="pipeline")
    parse.add_argument("--timeout-seconds", type=int, default=1800)

    validate = subparsers.add_parser("validate", help="validate a normalized component JSON")
    validate.add_argument("schema_json")
    validate.add_argument("--manifest")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_datasheet(
                manufacturer=args.manufacturer,
                mpn=args.mpn,
                pdf_path=args.pdf_path,
                pdf_url=args.pdf_url,
                source_type=args.source_type,
                output_dir=args.output_dir,
            )
            _emit(result)
            return 0
        if args.command == "parse":
            _emit(parse_datasheet(
                manifest_path=args.manifest,
                output_dir=args.output_dir,
                backend=args.backend,
                timeout_seconds=args.timeout_seconds,
            ))
            return 0
        result = validate_component_schema(args.schema_json, args.manifest)
        _emit(result)
        return 0 if result["valid"] else 2
    except Exception as exc:
        _emit({"success": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

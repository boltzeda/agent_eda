"""Command-line entry point for the standalone ADI model acquirer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import ADIModelAcquirer


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire a public ADI simulation model.")
    parser.add_argument("part_number", help="Exact ADI manufacturer part number, e.g. AD8605")
    parser.add_argument("--asset-type", default="spice", help="spice, pspice, ltspice, simplis, or ibis")
    parser.add_argument("--output", type=Path, default=Path("generated/adi_model_acquirer"))
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--import-file", type=Path)
    parser.add_argument("--source-url", help="Official model URL for --browser or --import-file")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--accept-license", action="store_true")
    parser.add_argument("--browser-executable")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.browser and args.import_file:
        parser.error("--browser and --import-file are separate modes")
    if args.browser and (not args.source_url or args.asset_type not in {"spice", "pspice"}):
        parser.error("--browser requires an exact official --source-url and SPICE format")
    if not args.browser and bool(args.import_file) != bool(args.source_url):
        parser.error("--import-file and --source-url must be supplied together")
    if not args.browser and (args.accept_license or args.browser_executable or args.headed):
        parser.error("browser options require --browser")
    try:
        acquirer = ADIModelAcquirer(args.output, timeout_seconds=args.timeout)
        if args.browser:
            from .browser_download import download_model
            result = download_model(args.part_number, args.source_url, args.output,
                                    accept_license=args.accept_license, timeout_seconds=args.timeout,
                                    browser_executable=args.browser_executable, headed=args.headed)
            args.output.mkdir(parents=True, exist_ok=True)
            manifest = args.output / f"{ADIModelAcquirer._safe_name(args.part_number.upper())}_browser_acquisition.json"
            manifest.write_text(json.dumps(result.to_dict(), indent=2))
        elif args.import_file:
            result = acquirer.import_model(args.part_number, args.import_file, args.source_url, args.asset_type)
        else:
            result = acquirer.acquire(args.part_number, args.asset_type)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result.to_dict(), indent=2))
    if args.browser:
        return 0 if result.status.value == "downloaded" else 1
    return 0 if result.status.value in {"downloaded", "imported", "unavailable", "access_required", "paid"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

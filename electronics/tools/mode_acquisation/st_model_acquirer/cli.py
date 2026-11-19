"""Command-line entry point for the standalone ST model acquirer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import STModelAcquirer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acquire a public STMicroelectronics simulation model."
    )
    parser.add_argument(
        "part_number",
        help="Exact ST manufacturer part number, e.g. A6986F or TSC101",
    )
    parser.add_argument(
        "--asset-type",
        default="spice",
        help="spice, pspice, ltspice, simplis, simetrix, or eldo",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated/st_model_acquirer"),
    )
    parser.add_argument("--product-url", help="Official ST product page for an exact-part fallback lookup")
    args = parser.parse_args()
    try:
        result = STModelAcquirer(args.output).acquire(args.part_number, args.asset_type, product_url=args.product_url)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status.value in {
        "downloaded", "unavailable", "access_required", "paid",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

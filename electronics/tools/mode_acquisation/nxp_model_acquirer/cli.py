"""Command-line entry point for the standalone NXP model acquirer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import NXPModelAcquirer


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire a public NXP simulation model.")
    parser.add_argument("part_number", help="Exact NXP part or family name, e.g. BF862")
    parser.add_argument("--asset-type", default="spice", help="spice, pspice, or simplis")
    parser.add_argument("--output", type=Path, default=Path("generated/nxp_model_acquirer"))
    args = parser.parse_args()
    result = NXPModelAcquirer(args.output).acquire(args.part_number, args.asset_type)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status.value in {
        "downloaded", "unavailable", "access_required", "paid"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for the standalone TI model acquirer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import TIModelAcquirer


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire a public TI simulation model.")
    parser.add_argument("part_number", help="Exact TI manufacturer part number, e.g. LM741")
    parser.add_argument("--asset-type", default="pspice", help="pspice, spice, tina-ti, or simplis")
    parser.add_argument("--output", type=Path, default=Path("generated/ti_model_acquirer"))
    args = parser.parse_args()
    result = TIModelAcquirer(args.output).acquire(args.part_number, args.asset_type)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status.value in {"downloaded", "unavailable", "access_required", "paid"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

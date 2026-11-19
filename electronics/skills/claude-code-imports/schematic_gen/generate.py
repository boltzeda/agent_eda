"""CLI and Python API entry point for schematic generation."""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine.circuit_parser import validate_circuit, circuit_summary
from engine.schematic_engine import SchematicEngine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def generate_schematic(
    circuit: dict,
    output_path: str,
    template_path: str | None = None,
    project_path: str | None = None,
    validate: bool = True,
    paper_size: str = "A4",
) -> Path:
    """Generate a .kicad_sch from a circuit definition dict."""
    circuit = validate_circuit(circuit, paper_size=paper_size)
    print(circuit_summary(circuit))

    engine = SchematicEngine(
        template_path=Path(template_path) if template_path else None,
        project_path=Path(project_path) if project_path else None,
    )
    return engine.generate(
        circuit, output_path, validate=validate, paper_size=paper_size
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate KiCAD schematics from circuit JSON"
    )
    parser.add_argument("circuit", help="Path to circuit JSON file, or --json for inline")
    parser.add_argument("output", help="Output .kicad_sch path")
    parser.add_argument("--json", action="store_true", help="Read circuit from stdin as JSON string")
    parser.add_argument("--template", help="Path to seed template .kicad_sch")
    parser.add_argument("--no-validate", action="store_true", help="Skip post-generation validation")
    parser.add_argument(
        "--paper",
        default="A4",
        choices=["A4", "A3", "A", "B"],
        help="Paper size (default: A4)",
    )
    args = parser.parse_args()

    if args.json:
        circuit = json.loads(args.circuit)
    else:
        with open(args.circuit) as f:
            circuit = json.load(f)

    generate_schematic(
        circuit,
        args.output,
        template_path=args.template,
        validate=not args.no_validate,
        paper_size=args.paper,
    )


if __name__ == "__main__":
    main()

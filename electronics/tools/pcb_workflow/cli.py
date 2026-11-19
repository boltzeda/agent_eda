"""Stage dispatcher for the standalone PCB workflow.

Each command runs exactly one stage. In particular, placement never triggers
routing; Agent-EDA can present the placed board and wait for approval before it
starts the router stage.
"""

from __future__ import annotations

import argparse
import json
import sys

from .kicad_environment import doctor as kicad_doctor


_STAGES = {
    "preprocess": "Parse a KiCad netlist and emit prepared CSV/JSON inputs.",
    "place": "Place components and emit an unrouted KiCad board.",
    "route": "Route an approved placed board with LineExplore.",
    "doctor": "Check Python, KiCad bindings, CLI, footprints, and placement dependencies.",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pcb_workflow",
        description=(
            "Run one PCB workflow stage at a time. Placement and routing are "
            "separate commands so a user can review and approve placement "
            "before routing starts."
        ),
        epilog=(
            "Stages:\n"
            + "\n".join(f"  {name}: {description}" for name, description in _STAGES.items())
            + "\nExamples: python -m pcb_workflow preprocess --help; "
            "python -m pcb_workflow place --help; "
            "python -m pcb_workflow route --help"
        ),
    )
    parser.add_argument("stage", choices=_STAGES, help="stage to run")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one stage without automatically starting a later stage."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()

    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0 if arguments else 2

    stage = arguments.pop(0)
    if stage not in _STAGES:
        parser.error(f"unknown stage {stage!r}; choose one of: {', '.join(_STAGES)}")

    if stage == "doctor":
        doctor_parser = argparse.ArgumentParser(prog="python -m pcb_workflow doctor")
        doctor_parser.add_argument(
            "--stage", choices=("placement", "routing", "all"), default="all",
            help="dependencies to check (default: all)",
        )
        doctor_args = doctor_parser.parse_args(arguments)
        report = kicad_doctor(stage=doctor_args.stage)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    # Delegate the stage's remaining arguments to its own parser. This keeps
    # each tool's existing CLI as the source of truth for its input contract.
    if stage == "preprocess":
        from .placement_preprocessor.cli import main as stage_main
    elif stage == "place":
        from .placement_tool.cli import main as stage_main
    else:
        from .router_tool.cli import main as stage_main
    return stage_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

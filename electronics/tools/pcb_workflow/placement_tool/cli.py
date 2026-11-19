"""Standalone command line for the copied PCB placement implementation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pcb_workflow.kicad_environment import footprint_roots, preflight as kicad_preflight

TOOL_DIR = Path(__file__).resolve().parent
ENGINE_DIR = TOOL_DIR / "engine"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="placement-tool",
        description="Place components from prepared board inputs and emit placement JSON only.",
    )
    parser.add_argument("input_dir", type=Path, help="Directory containing prepared CSV and JSON inputs")
    parser.add_argument("prefix", help="Board/project prefix used in the input filenames")
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory (default: input directory)",
    )
    parser.add_argument("--generations", type=int, default=100, help="Genetic algorithm generations")
    parser.add_argument("--population", type=int, default=50, help="Genetic algorithm population size")
    parser.add_argument(
        "--placement-config",
        type=Path,
        help="Optional placement policy JSON; defaults to <prefix>_placement_constraints.json in input_dir",
    )
    parser.add_argument("--allow-bottom", action="store_true", default=None)
    parser.add_argument("--bottom-bias", type=float)
    parser.add_argument(
        "--footprints-dir",
        type=Path,
        help="KiCad footprint libraries root (default: auto-detected or KICAD_FOOTPRINTS)",
    )
    return parser


def _stage_inputs(input_dir: Path, work_dir: Path, prefix: str) -> None:
    names = [
        "components.csv",
        "nets.csv",
        "net_nodes.csv",
        f"{prefix}_footprints.json",
        f"{prefix}_board.json",
        f"{prefix}_drc.json",
        f"{prefix}_layers.json",
        f"{prefix}_mechanical.json",
        f"{prefix}_placement_constraints.json",
    ]
    required = {
        "components.csv",
        f"{prefix}_footprints.json",
        f"{prefix}_board.json",
        f"{prefix}_drc.json",
    }
    missing = [name for name in sorted(required) if not (input_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "missing required placement input(s): " + ", ".join(missing)
        )
    for name in names:
        source = input_dir / name
        if source.is_file():
            shutil.copy2(source, work_dir / name)


def run_placement(
    input_dir: Path,
    prefix: str,
    out_dir: Path,
    *,
    generations: int = 100,
    population: int = 50,
    placement_config_path: Path | None = None,
    allow_bottom: bool | None = None,
    bottom_bias: float | None = None,
    footprints_dir: Path | None = None,
) -> dict[str, Any]:
    """Run placement in a temporary input copy and preserve the source inputs."""
    input_dir = input_dir.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
        raise ValueError("prefix must be a simple filename prefix without directory separators")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"input directory does not exist: {input_dir}")
    if generations < 0 or population < 1:
        raise ValueError("generations must be >= 0 and population must be >= 1")

    kicad_preflight(
        require_footprints=True,
        require_torch=True,
        require_packages=("numpy", "networkx", "deap", "shapely"),
        footprints_override=footprints_dir,
    )

    default_policy = input_dir / f"{prefix}_placement_constraints.json"
    policy_path = placement_config_path or (default_policy if default_policy.is_file() else None)
    policy = _read_json(policy_path) if policy_path else {}

    # Keep argument parsing and --help usable without loading the placement
    # stack. The heavy dependencies are needed only when placement is run.
    engine_path = str(ENGINE_DIR)
    if engine_path not in sys.path:
        sys.path.insert(0, engine_path)
    from main_pipeline import run_pipeline

    effective_allow_bottom = (
        allow_bottom if allow_bottom is not None else bool(policy.get("allow_bottom", False))
    )
    effective_bottom_bias = (
        bottom_bias if bottom_bias is not None else float(policy.get("bottom_bias", 20.0))
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="placement_tool_input_") as temporary:
        work_dir = Path(temporary)
        _stage_inputs(input_dir, work_dir, prefix)
        result_path = work_dir / f"{prefix}_placement.json"
        error: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            result = run_pipeline(
                str(work_dir),
                prefix,
                ga_generations=generations,
                ga_population=population,
                allow_bottom=effective_allow_bottom,
                bottom_bias=effective_bottom_bias,
                side_hints=policy.get("side_hints") or None,
                cluster_hints=policy.get("cluster_hints") or None,
                relative_constraints=policy.get("relative_constraints") or None,
            )
        except Exception as exc:
            error = exc

        placement_output = out_dir / f"{prefix}_placement.json"
        if result_path.is_file():
            shutil.copy2(result_path, placement_output)
        for kind in ("board", "mechanical"):
            generated = work_dir / f"{prefix}_{kind}.json"
            if generated.is_file():
                shutil.copy2(generated, out_dir / f"{prefix}_placement_{kind}.json")

        if error is not None:
            status = "placement_constraint_failure" if result_path.is_file() else "error"
            manifest = {
                "status": status,
                "placement_file": str(placement_output) if result_path.is_file() else None,
                "error": str(error),
            }
            (out_dir / f"{prefix}_placement_result.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            return manifest

        board_output = out_dir / f"{prefix}_placed.kicad_pcb"
        worker = TOOL_DIR / "board_export_worker.py"
        library_root = (
            footprints_dir.expanduser().resolve()
            if footprints_dir is not None
            else footprint_roots()[0]
        )
        command = [
            sys.executable,
            str(worker),
            "--input-dir", str(work_dir),
            "--prefix", prefix,
            "--output", str(board_output),
            "--footprints-dir", str(library_root),
        ]
        try:
            generated = subprocess.run(
                command,
                cwd=TOOL_DIR.parent,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            manifest = {
                "status": "board_generation_error",
                "placement_file": str(placement_output),
                "error": f"could not start KiCad board generation: {exc}",
            }
            (out_dir / f"{prefix}_placement_result.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            return manifest
        if generated.returncode != 0 or not board_output.is_file():
            error_text = (generated.stderr or generated.stdout or "board generator returned no details").strip()
            manifest = {
                "status": "board_generation_error",
                "placement_file": str(placement_output),
                "board_file": None,
                "error": error_text[-4000:],
            }
            (out_dir / f"{prefix}_placement_result.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            return manifest

        placed = (result or {}).get("placement") or {}
        board_config_path = out_dir / f"{prefix}_placement_board.json"
        board_config = _read_json(board_config_path) if board_config_path.is_file() else {}
        manifest = {
            "status": "placement_complete",
            "placement_file": str(placement_output),
            "placed_components": len(placed),
            "board_width_mm": board_config.get("width"),
            "board_height_mm": board_config.get("height"),
            "board_file": str(board_output),
            "board_generation": "complete",
            "routing_performed": False,
        }
        (out_dir / f"{prefix}_placement_result.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_dir = args.input_dir.expanduser().resolve()
    out_dir = args.out_dir or input_dir
    try:
        manifest = run_placement(
            input_dir,
            args.prefix,
            out_dir,
            generations=args.generations,
            population=args.population,
            placement_config_path=args.placement_config,
            allow_bottom=args.allow_bottom,
            bottom_bias=args.bottom_bias,
            footprints_dir=args.footprints_dir,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(manifest))
    return 0 if manifest["status"] == "placement_complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

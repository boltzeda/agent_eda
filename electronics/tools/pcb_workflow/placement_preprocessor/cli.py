"""Command line for preparing placement inputs from a KiCad netlist."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from ..kicad_environment import footprint_roots
from .board_reader import extract_board
from .netlist_reader import extract_netlist
from .rules_reader import extract_board_rules, extract_custom_rules, extract_project_rules


COMPONENT_FIELDS = [
    "ref", "value", "footprint", "datasheet", "description",
    "symbol_library", "symbol_part", "sheetpath",
]
NET_FIELDS = ["code", "name", "fanout", "class"]
NODE_FIELDS = ["net", "code", "ref", "pin", "pinfunction", "pintype"]
FOOTPRINT_FIELDS = [
    "ref", "value", "footprint", "footprint_library", "footprint_name",
    "description", "footprint_filters", "pin_count",
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="placement-preprocessor",
        description=(
            "Parse a KiCad .netlist and optionally enrich it with physical "
            "footprint and board data from a .kicad_pcb."
        ),
    )
    parser.add_argument("netlist", type=Path, help="KiCad exported .net file")
    parser.add_argument(
        "--board", "--pcb", dest="board", type=Path,
        help="optional .kicad_pcb used only to enrich footprint and board geometry",
    )
    parser.add_argument(
        "--project", type=Path,
        help="optional matching .kicad_pro for board constraints and net classes",
    )
    parser.add_argument(
        "--rules", "--custom-rules", dest="rules", type=Path,
        help="optional .kicad_dru file; rules are preserved as metadata, not translated to router behavior",
    )
    parser.add_argument(
        "--footprints-dir", type=Path,
        help=(
            "optional KiCad footprint root containing <library>.pretty directories; "
            "otherwise KICAD_FOOTPRINTS/KISYSMOD and standard locations are searched"
        ),
    )
    parser.add_argument("--out-dir", type=Path, help="output directory")
    parser.add_argument("--prefix", help="output filename prefix (default: netlist filename stem)")
    return parser


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _footprint_roots(footprints_dir: Path | None) -> list[Path]:
    roots: list[Path] = []
    if footprints_dir is not None:
        roots.append(footprints_dir.expanduser().resolve())
    else:
        roots.extend(footprint_roots())

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(Path(key))
    return unique


def _load_library_footprint(footprint_id: str, roots: list[Path]) -> dict[str, Any] | None:
    """Read physical dimensions and pad geometry for an assigned netlist footprint."""
    if ":" not in footprint_id:
        return None
    library, name = footprint_id.split(":", 1)
    for root in roots:
        path = root / f"{library}.pretty" / f"{name}.kicad_mod"
        if not path.is_file():
            continue
        try:
            from .board_reader import _footprint_metadata
            from .sexpr import parse

            footprint = parse(path.read_text(encoding="utf-8"))
            if not footprint or footprint[0] not in {"footprint", "module"}:
                continue
            record = _footprint_metadata(footprint)
        except (OSError, UnicodeDecodeError, ValueError):
            continue

        metadata = record.get("metadata") or {}
        if metadata.get("dimension_source") != "board_embedded_geometry":
            # Do not promote the geometry reader's 3 x 3 mm fallback as exact.
            continue
        metadata = dict(metadata)
        metadata["dimension_source"] = "kicad_library_geometry"
        metadata["geometry_source"] = "kicad_library"
        metadata["kicad_footprint"] = footprint_id
        geometry = dict(metadata.get("geometry") or {})
        geometry["geometry_source"] = "kicad_library"
        metadata["geometry"] = geometry
        metadata["resolved_kicad_mod"] = str(path.resolve())
        return metadata
    return None


def _same_footprint_id(board_metadata: dict[str, Any], footprint_id: str) -> bool:
    board_id = str(board_metadata.get("kicad_footprint") or board_metadata.get("footprint") or "")
    if not board_id or not footprint_id:
        return False
    if ":" in board_id:
        return board_id == footprint_id
    return board_id == footprint_id.rsplit(":", 1)[-1]


def _footprints_with_board(
    netlist_data: dict[str, Any],
    board_data: dict[str, Any] | None,
    footprint_roots: list[Path],
) -> tuple[dict[str, Any], dict[str, int]]:
    board_footprints = (board_data or {}).get("footprints", {})
    result: dict[str, Any] = {}
    counts = {"library_geometry": 0, "board_geometry": 0, "unresolved": 0, "no_footprint": 0}
    for component in netlist_data["components"]:
        ref = component["ref"]
        footprint_id = component["footprint"].strip()
        footprint_name = footprint_id.split(":", 1)[-1] if footprint_id else "Unknown"
        board_metadata = board_footprints.get(ref) or {}
        library_metadata = _load_library_footprint(footprint_id, footprint_roots) if footprint_id else None
        matching_board_metadata = (
            board_metadata
            if (_same_footprint_id(board_metadata, footprint_id)
                and board_metadata.get("dimension_source") == "board_embedded_geometry")
            else None
        )
        metadata = dict(library_metadata or matching_board_metadata or {})
        metadata.update({
            "class": footprint_id or "NoFootprint",
            "fallback_class": footprint_name,
            "kicad_footprint": footprint_id,
            "symbol_pin_count": netlist_data["footprints"].get(ref, {}).get("pin_count", 0),
        })
        if library_metadata:
            counts["library_geometry"] += 1
        elif matching_board_metadata:
            counts["board_geometry"] += 1
            metadata["geometry_source"] = "board_embedded_geometry"
        elif not footprint_id:
            counts["no_footprint"] += 1
            metadata.update({
                "dimension_source": "no_footprint_assigned",
                "geometry_source": "not_available",
            })
        else:
            counts["unresolved"] += 1
            metadata.update({
                "dimension_source": "footprint_id_only",
                "geometry_source": "not_resolved",
            })
            if board_metadata and not matching_board_metadata:
                metadata["board_footprint_id_mismatch"] = board_metadata.get("kicad_footprint")
        result[ref] = metadata
    return result, counts


def prepare(netlist_path: Path, output_dir: Path, prefix: str,
            board_path: Path | None = None,
            project_path: Path | None = None,
            rules_path: Path | None = None,
            footprints_dir: Path | None = None) -> dict[str, Any]:
    netlist_path = netlist_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not netlist_path.is_file():
        raise FileNotFoundError(f"netlist does not exist: {netlist_path}")
    if netlist_path.suffix.lower() != ".net":
        raise ValueError("netlist input must have a .net extension")
    if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
        raise ValueError("prefix must be a simple filename prefix without directory separators")

    netlist_data = extract_netlist(netlist_path)
    board_data = None
    if board_path is not None:
        board_path = board_path.expanduser().resolve()
        if not board_path.is_file():
            raise FileNotFoundError(f"board does not exist: {board_path}")
        if board_path.suffix.lower() != ".kicad_pcb":
            raise ValueError("board input must have a .kicad_pcb extension")
        board_data = extract_board(board_path)
    if project_path is not None:
        project_path = project_path.expanduser().resolve()
        if not project_path.is_file():
            raise FileNotFoundError(f"project settings file does not exist: {project_path}")
    if rules_path is not None:
        rules_path = rules_path.expanduser().resolve()
        if not rules_path.is_file():
            raise FileNotFoundError(f"custom rules file does not exist: {rules_path}")
    if footprints_dir is not None:
        footprints_dir = footprints_dir.expanduser().resolve()
        if not footprints_dir.is_dir():
            raise NotADirectoryError(f"footprints directory does not exist: {footprints_dir}")

    embedded_board_rules = (board_data or {}).get("embedded_rules") or {}
    if project_path is not None:
        drc_data = extract_project_rules(
            project_path, netlist_data["nets"], embedded_board_rules
        )
    elif board_path is not None:
        drc_data = extract_board_rules(embedded_board_rules, board_path)
    else:
        drc_data = None
    custom_rules_data = extract_custom_rules(rules_path) if rules_path is not None else None
    if custom_rules_data is not None:
        if drc_data is None:
            drc_data = {"sources": {}}
        drc_data.setdefault("sources", {})["custom_rules"] = str(rules_path)
        drc_data["custom_rules_file"] = f"{prefix}_custom_rules.json"
        drc_data["custom_rules_count"] = custom_rules_data["rule_count"]

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "components.csv", COMPONENT_FIELDS, netlist_data["components"])
    _write_csv(output_dir / "nets.csv", NET_FIELDS, netlist_data["nets"])
    _write_csv(output_dir / "net_nodes.csv", NODE_FIELDS, netlist_data["net_nodes"])
    _write_csv(output_dir / "footprints_list.csv", FOOTPRINT_FIELDS, netlist_data["footprint_rows"])

    footprint_metadata, footprint_geometry_counts = _footprints_with_board(
        netlist_data, board_data, _footprint_roots(footprints_dir)
    )
    footprint_file = f"{prefix}_footprints.json"
    (output_dir / footprint_file).write_text(
        json.dumps(footprint_metadata, indent=2), encoding="utf-8"
    )

    files_written = [
        "components.csv", "nets.csv", "net_nodes.csv", "footprints_list.csv", footprint_file,
    ]
    if board_data is not None:
        for filename, content in (
            (f"{prefix}_board.json", board_data["board"]),
            (f"{prefix}_layers.json", board_data["layers"]),
            (f"{prefix}_mechanical.json", board_data["mechanical"]),
        ):
            (output_dir / filename).write_text(json.dumps(content, indent=2), encoding="utf-8")
            files_written.append(filename)
    if drc_data is not None:
        drc_file = f"{prefix}_drc.json"
        (output_dir / drc_file).write_text(json.dumps(drc_data, indent=2), encoding="utf-8")
        files_written.append(drc_file)
    if custom_rules_data is not None:
        custom_rules_file = f"{prefix}_custom_rules.json"
        (output_dir / custom_rules_file).write_text(
            json.dumps(custom_rules_data, indent=2), encoding="utf-8"
        )
        files_written.append(custom_rules_file)

    board_refs = set((board_data or {}).get("footprints", {}))
    netlist_refs = {row["ref"] for row in netlist_data["components"]}
    netlist_footprint_refs = {
        row["ref"] for row in netlist_data["components"] if row["footprint"].strip()
    }
    missing_board_footprints = sorted(netlist_footprint_refs - board_refs) if board_data else []
    board_only_refs = sorted(board_refs - netlist_refs) if board_data else []
    missing_placement_inputs = []
    if board_data is None:
        missing_placement_inputs.append(f"{prefix}_board.json")
    missing_rule_fields = [
        field for field in ("clearance", "outline_clearance")
        if drc_data is None or drc_data.get(field) is None
    ]
    if drc_data is None:
        missing_placement_inputs.append(f"{prefix}_drc.json")
    elif missing_rule_fields:
        missing_placement_inputs.extend(f"{prefix}_drc.json:{field}" for field in missing_rule_fields)

    notes = [
        "The board, project settings, and custom-rule files are optional inputs.",
        "Physical footprint dimensions are resolved from the netlist footprint ID using KiCad .pretty libraries when available; otherwise a matching supplied board can provide geometry.",
    ]
    if custom_rules_data is not None:
        notes.append(
            "Custom rules were preserved in a sidecar for reference; the placement and routing tools do not automatically enforce arbitrary KiCad custom-rule expressions."
        )
    if missing_rule_fields:
        notes.append(
            "Some placement-required rule values were not available. The parser did not invent them; supply the missing values before running placement."
        )
    if footprint_geometry_counts["unresolved"]:
        notes.append(
            "Some assigned footprint IDs could not be resolved to KiCad library or matching board geometry; placement may use its approximate size fallback for those components."
        )

    manifest = {
        "status": "prepared_netlist_and_board" if board_data else "prepared_netlist_only",
        "netlist": str(netlist_path),
        "board": str(board_path) if board_path else None,
        "project": str(project_path) if project_path else None,
        "custom_rules": str(rules_path) if rules_path else None,
        "output_directory": str(output_dir),
        "prefix": prefix,
        "files_written": files_written,
        "component_count": len(netlist_data["components"]),
        "footprint_count": len(netlist_data["footprint_rows"]),
        "net_count": len(netlist_data["nets"]),
        "net_node_count": len(netlist_data["net_nodes"]),
        "footprint_geometry_source": "kicad_library_or_board" if any(
            footprint_geometry_counts[key] for key in ("library_geometry", "board_geometry")
        ) else "unresolved_from_footprint_ids",
        "footprint_geometry_counts": footprint_geometry_counts,
        "footprint_library_root": str(footprints_dir) if footprints_dir else None,
        "missing_board_footprint_refs": missing_board_footprints,
        "board_refs_not_in_netlist": board_only_refs,
        "missing_placement_inputs": missing_placement_inputs,
        "missing_placement_rule_fields": missing_rule_fields,
        "custom_rule_count": custom_rules_data["rule_count"] if custom_rules_data else 0,
        "netlist_metadata": netlist_data["netlist_metadata"],
        "notes": notes,
    }
    manifest_path = output_dir / f"{prefix}_placement_preprocessor_result.json"
    manifest["manifest_file"] = manifest_path.name
    files_written.append(manifest_path.name)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    netlist_path = args.netlist.expanduser().resolve()
    prefix = args.prefix or netlist_path.stem
    output_dir = args.out_dir or (Path.cwd() / f"{prefix}_placement_input")
    try:
        result = prepare(
            netlist_path, output_dir, prefix, args.board, args.project, args.rules,
            args.footprints_dir,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

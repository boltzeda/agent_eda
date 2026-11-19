"""Create a placed, unrouted KiCad board in an isolated pcbnew process."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# This worker is launched as a file, so add the project root before importing
# the shared KiCad discovery module.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pcb_workflow.kicad_environment import footprint_roots, import_pcbnew

try:
    pcbnew = import_pcbnew()
except RuntimeError as exc:  # pragma: no cover - depends on the host KiCad install
    raise SystemExit(str(exc)) from exc


NM_PER_MM = 1_000_000


def _load_json(path: Path, *, required: bool = True) -> dict:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"required board-generation input is missing: {path.name}")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object in {path.name}")
    return data


def _csv(path: Path, *, required: bool = True) -> list[dict[str, str]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"required board-generation input is missing: {path.name}")
        return []
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _point(x_mm: float, y_mm: float):
    return pcbnew.VECTOR2I(round(x_mm * NM_PER_MM), round(y_mm * NM_PER_MM))


def _set_orientation(footprint, rotation_degrees: float) -> None:
    try:
        footprint.SetOrientation(pcbnew.EDA_ANGLE(rotation_degrees, pcbnew.DEGREES_T))
    except AttributeError:
        footprint.SetOrientation(round(rotation_degrees * 10))


def _visual_center_offset(footprint, kicad_rotation: float) -> tuple[float, float]:
    footprint.SetPosition(_point(0.0, 0.0))
    _set_orientation(footprint, kicad_rotation)
    box = footprint.GetBoundingBox()
    cx = (box.GetLeft() + box.GetRight()) / 2.0 / NM_PER_MM
    cy = (box.GetTop() + box.GetBottom()) / 2.0 / NM_PER_MM
    return cx, cy


SIDE_LAYER_SWAPS = (
    ("F_Cu", "B_Cu"), ("F_Mask", "B_Mask"), ("F_Paste", "B_Paste"),
    ("F_SilkS", "B_SilkS"), ("F_CrtYd", "B_CrtYd"), ("F_Fab", "B_Fab"),
)


def _swap_side_layer(layer_id: int) -> int:
    for front, back in SIDE_LAYER_SWAPS:
        front_id = getattr(pcbnew, front, None)
        back_id = getattr(pcbnew, back, None)
        if front_id is None or back_id is None:
            continue
        if layer_id == front_id:
            return back_id
        if layer_id == back_id:
            return front_id
    return layer_id


def _mirror_item(item) -> None:
    relative = item.GetFPRelativePosition()
    item.SetFPRelativePosition(pcbnew.VECTOR2I(-relative.x, relative.y))
    try:
        degrees = item.GetFPRelativeOrientation().AsDegrees()
        item.SetFPRelativeOrientation(pcbnew.EDA_ANGLE(-degrees, pcbnew.DEGREES_T))
    except (AttributeError, TypeError):
        pass
    try:
        item.SetLayer(_swap_side_layer(item.GetLayer()))
    except (AttributeError, TypeError):
        pass
    try:
        item.SetMirrored(True)
    except (AttributeError, TypeError):
        pass


def _place_bottom(footprint) -> None:
    footprint.SetLayer(pcbnew.B_Cu)
    rear_layers = pcbnew.LSET()
    for name in ("B_Cu", "B_Mask", "B_Paste"):
        layer = getattr(pcbnew, name, None)
        if layer is not None:
            rear_layers.AddLayer(layer)
    for pad in footprint.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
            relative = pad.GetFPRelativePosition()
            pad.SetFPRelativePosition(pcbnew.VECTOR2I(-relative.x, relative.y))
            try:
                degrees = pad.GetFPRelativeOrientation().AsDegrees()
                pad.SetFPRelativeOrientation(pcbnew.EDA_ANGLE(-degrees, pcbnew.DEGREES_T))
            except AttributeError:
                pass
            pad.SetLayerSet(rear_layers)
    for item in footprint.GraphicalItems():
        _mirror_item(item)
    _mirror_item(footprint.Reference())
    _mirror_item(footprint.Value())


def _load_footprint(library_root: Path, footprint_id: str, ref: str):
    if ":" not in footprint_id:
        raise ValueError(f"{ref} has no KiCad library-qualified footprint ID: {footprint_id!r}")
    library, name = footprint_id.split(":", 1)
    library_dir = library_root / f"{library}.pretty"
    footprint_file = library_dir / f"{name}.kicad_mod"
    if not footprint_file.is_file():
        raise FileNotFoundError(f"footprint for {ref} was not found: {footprint_file}")
    footprint = pcbnew.FootprintLoad(str(library_dir), name)
    if footprint is None:
        raise ValueError(f"KiCad could not load footprint {footprint_id!r} for {ref}")
    footprint.SetFPID(pcbnew.LIB_ID(library, name))
    return footprint


def _set_board_rules(board, drc: dict) -> None:
    settings = board.GetDesignSettings()
    net_settings = settings.m_NetSettings
    default_class = net_settings.GetDefaultNetclass()
    rules = {
        "trace_width": "SetTrackWidth",
        "clearance": "SetClearance",
        "via_track_width": "SetViaDiameter",
        "via_drill": "SetViaDrill",
    }
    annular = float(drc.get("via_annular_ring", 0.0) or 0.0)
    drill = float(drc.get("via_drill", 0.0) or 0.0)
    via_diameter = max(float(drc.get("via_track_width", 0.0) or 0.0), drill + 2 * annular)
    values = dict(drc)
    if via_diameter:
        values["via_track_width"] = via_diameter
    for key, setter_name in rules.items():
        if key not in values:
            continue
        setter = getattr(default_class, setter_name, None)
        if setter:
            setter(round(float(values[key]) * NM_PER_MM))
    minima = {
        "m_MinClearance": "clearance",
        "m_TrackMinWidth": "trace_width",
        "m_ViasMinSize": "via_track_width",
        "m_ViasMinDrill": "via_drill",
        "m_HoleClearance": "hole_clearance",
        "m_EdgeClearance": "outline_clearance",
    }
    for attr, key in minima.items():
        if key in values and hasattr(settings, attr):
            setattr(settings, attr, round(float(values[key]) * NM_PER_MM))


def _layer_id(name: str):
    normalized = name.strip()
    aliases = {"GTL": "F.Cu", "GBL": "B.Cu"}
    normalized = aliases.get(normalized.upper(), normalized)
    if normalized == "F.Cu":
        return pcbnew.F_Cu
    if normalized == "B.Cu":
        return pcbnew.B_Cu
    if normalized.startswith("In") and normalized.endswith(".Cu"):
        index = int(normalized[2:-3])
        return pcbnew.In1_Cu + index - 1
    if normalized.upper().startswith("GP") and normalized[2:].isdigit():
        return pcbnew.In1_Cu + int(normalized[2:]) - 2
    return None


def _add_outline(board, width: float, height: float) -> None:
    corners = ((0, 0), (width, 0), (width, height), (0, height), (0, 0))
    for start, end in zip(corners, corners[1:]):
        edge = pcbnew.PCB_SHAPE(board)
        edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
        edge.SetLayer(pcbnew.Edge_Cuts)
        edge.SetStart(_point(*start))
        edge.SetEnd(_point(*end))
        edge.SetWidth(round(0.1 * NM_PER_MM))
        board.Add(edge)


def _add_zones(board, fills: dict, drc: dict) -> int:
    added = 0
    clearance = round(float(drc.get("clearance", 0.2)) * NM_PER_MM)
    inset = round(float(drc.get("outline_clearance", 0.5)) * NM_PER_MM)
    x1, y1, x2, y2 = inset, inset, board.GetBoardEdgesBoundingBox().GetWidth() - inset, board.GetBoardEdgesBoundingBox().GetHeight() - inset
    if x2 <= x1 or y2 <= y1:
        return 0
    for layer_name, net_name in (fills or {}).items():
        layer = _layer_id(str(layer_name))
        net = board.FindNet(str(net_name))
        if layer is None or net is None:
            continue
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNet(net)
        zone.SetLocalClearance(clearance)
        zone.SetMinThickness(max(clearance, round(0.1 * NM_PER_MM)))
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
            outline.Append(x, y)
        board.Add(zone)
        added += 1
    if added:
        try:
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        except (AttributeError, TypeError):
            pass
    return added


def _build(args: argparse.Namespace) -> dict:
    input_dir = args.input_dir
    prefix = args.prefix
    components = _csv(input_dir / "components.csv")
    nets = _csv(input_dir / "nets.csv", required=False)
    nodes = _csv(input_dir / "net_nodes.csv", required=False)
    placement_doc = _load_json(input_dir / f"{prefix}_placement.json")
    board_doc = _load_json(input_dir / f"{prefix}_board.json")
    drc_doc = _load_json(input_dir / f"{prefix}_drc.json")
    layers_doc = _load_json(input_dir / f"{prefix}_layers.json", required=False)
    mechanical_doc = _load_json(input_dir / f"{prefix}_mechanical.json", required=False)

    placement = placement_doc.get("placement")
    if not isinstance(placement, dict):
        raise ValueError("placement JSON must contain a placement object")
    width = float(board_doc["width"])
    height = float(board_doc["height"])
    if width <= 0 or height <= 0:
        raise ValueError("board width and height must be positive")
    total_layers = int(layers_doc.get("total_layers", 2) or 2)
    if total_layers < 2 or total_layers > 32 or total_layers % 2:
        raise ValueError("board copper layer count must be an even number from 2 to 32")

    board = pcbnew.BOARD()
    if total_layers > 2:
        board.SetCopperLayerCount(total_layers)
    _set_board_rules(board, drc_doc)

    library_root = args.footprints_dir
    if not library_root.is_dir():
        raise FileNotFoundError(f"KiCad footprint library directory does not exist: {library_root}")

    placed: dict[str, object] = {}
    missing = []
    for row in components:
        ref = (row.get("ref") or "").strip()
        footprint_id = (row.get("footprint") or "").strip()
        if not ref or not footprint_id:
            continue  # schematic-only/no-footprint symbols are not PCB footprints
        if ref not in placement:
            missing.append(ref)
            continue
        pose = placement[ref]
        fp = _load_footprint(library_root, footprint_id, ref)
        fp.SetReference(ref)
        fp.SetValue(row.get("value") or "")
        rotation = -float(pose.get("rotation", 0.0))
        side = str(pose.get("side", "top")).lower()
        if side == "bottom":
            _place_bottom(fp)
        cx, cy = _visual_center_offset(fp, rotation)
        x = float(pose["x"]) - cx
        y = height - float(pose["y"]) - cy
        fp.SetPosition(_point(x, y))
        _set_orientation(fp, rotation)
        board.Add(fp)
        placed[ref] = fp
    if missing:
        raise ValueError("placement result is missing physical component(s): " + ", ".join(missing))
    if not placed:
        raise ValueError("no physical footprints were available to generate a board")

    net_objects = {}
    net_aliases = {}
    for row in nets:
        name = (row.get("name") or "").strip()
        code = (row.get("code") or "").strip()
        if not name or name in net_objects:
            continue
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        net_objects[name] = net
        net_aliases[name] = name
        if code:
            net_aliases[code] = name
    for row in nodes:
        net_name = (row.get("net") or "").strip()
        ref = (row.get("ref") or "").strip()
        pin_number = (row.get("pin") or "").strip()
        fp = placed.get(ref)
        net = net_objects.get(net_aliases.get(net_name, net_name))
        if fp is None or net is None:
            continue
        matching = [pad for pad in fp.Pads() if str(pad.GetNumber()).strip() == pin_number]
        for pad in matching:
            pad.SetNet(net)

    _add_outline(board, width, height)
    _add_zones(board, layers_doc.get("fills", {}), drc_doc)

    hole_warnings = []
    for index, hole in enumerate(mechanical_doc.get("holes", []), start=1):
        if str(hole.get("ref") or "") in placed:
            # The hole belongs to a component footprint already loaded above.
            continue
        diameter = float(hole.get("diameter", 0.0))
        if diameter <= 0:
            hole_warnings.append(f"hole {index} has no positive diameter")
            continue
        name = f"MountingHole_{diameter:g}mm"
        footprint = _load_footprint(library_root, f"MountingHole:{name}", f"MH{index}")
        footprint.SetReference(str(hole.get("ref") or f"MH{index}"))
        footprint.SetValue("MountingHole")
        footprint.SetPosition(_point(float(hole["x"]), height - float(hole["y"])))
        _set_orientation(footprint, 0.0)
        board.Add(footprint)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(args.output), board)
    return {
        "status": "board_generated",
        "board_file": str(args.output.resolve()),
        "placed_components": len(placed),
        "net_count": len(net_objects),
        "copper_layers": total_layers,
        "tracks_created": 0,
        "vias_created": 0,
        "warnings": hole_warnings,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    known_roots = footprint_roots()
    parser.add_argument(
        "--footprints-dir", type=Path,
        default=known_roots[0] if known_roots else None,
        required=not bool(known_roots),
    )
    return parser


if __name__ == "__main__":
    try:
        result = _build(_parser().parse_args())
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result))

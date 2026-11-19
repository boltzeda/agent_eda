#!/usr/bin/env python3
"""
pcb_auto_placement/size_estimator.py

Maps component footprints to physical (width, height) dimensions in mm.
When a real KiCad footprint is available, prefer its measured bounding box.
Only approximate when the KiCad footprint cannot be resolved or loaded.
"""

import glob
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from pcb_workflow.kicad_environment import footprint_roots, import_pcbnew


# Footprint physical dimensions in mm: (width, height)
FOOTPRINT_SIZES = {
    # Resistors
    "R_0402": (1.0, 0.5),
    "R_0603": (1.6, 0.8),
    "R_0805": (2.0, 1.25),
    "R_1206": (3.2, 1.6),
    "R_1210": (3.2, 2.5),
    "R_2010": (5.0, 2.5),
    "R_2512": (6.3, 3.2),
    # Capacitors
    "C_0402": (1.0, 0.5),
    "C_0603": (1.6, 0.8),
    "C_0805": (2.0, 1.25),
    "C_1206": (3.2, 1.6),
    "C_1210": (3.2, 2.5),
    # Diodes
    "D_SOD-123": (2.7, 1.6),
    "D_SOD-323": (1.8, 1.3),
    "D_SMA": (4.6, 2.9),
    "D_SMB": (5.3, 3.6),
    # IC packages
    "SOIC-8": (5.0, 4.0),
    "SOIC-14": (8.75, 4.0),
    "SOIC-16": (10.3, 4.0),
    "SSOP-28": (10.2, 5.3),
    "TSSOP-8": (3.0, 4.4),
    "TSSOP-14": (5.0, 4.4),
    "TSSOP-16": (5.0, 4.4),
    "TSSOP-20": (6.5, 4.4),
    "QFN-16": (3.0, 3.0),
    "QFN-20": (4.0, 4.0),
    "QFN-32": (5.0, 5.0),
    "QFN-48": (7.0, 7.0),
    "QFP-32": (7.0, 7.0),
    "QFP-44": (10.0, 10.0),
    "QFP-64": (12.0, 12.0),
    "QFP-100": (14.0, 14.0),
    "BGA-256": (17.0, 17.0),
    # Transistor packages
    "SOT-23": (2.9, 1.3),
    "SOT-223": (6.5, 3.5),
    "SOT-89": (4.5, 2.5),
    "TO-252": (6.5, 6.1),
    "TO-263": (8.4, 10.3),
    # Connectors
    "PinHeader_1x02": (2.54, 5.08),
    "PinHeader_1x03": (2.54, 7.62),
    "PinHeader_1x04": (2.54, 10.16),
    "PinHeader_1x06": (2.54, 15.24),
    "PinHeader_1x08": (2.54, 20.32),
    "PinHeader_1x10": (2.54, 25.40),
    "PinHeader_2x03": (5.08, 7.62),
    "PinHeader_2x04": (5.08, 10.16),
    "PinHeader_2x05": (5.08, 12.70),
    "PinHeader_2x10": (5.08, 25.40),
    "PinHeader_2x13": (5.08, 33.02),
    "PinHeader_2x20": (5.08, 50.80),
    "USB_Micro_B": (7.5, 5.0),
    "USB_C": (9.0, 7.5),
    "USB_A": (12.0, 5.7),
    "BarrelJack": (14.0, 9.0),
    "JST_XH_2": (7.5, 5.75),
    "JST_XH_3": (10.0, 5.75),
    # Crystals / Oscillators
    "Crystal_3225": (3.2, 2.5),
    "Crystal_5032": (5.0, 3.2),
    "Crystal_HC49": (11.0, 4.7),
}

DEFAULT_SIZE = (3.0, 3.0)  # conservative fallback


def estimate_size(footprint_name) -> tuple:
    """
    Return (width, height) in mm for a given footprint name.
    Uses exact match first, then prefix matching.
    """
    if not isinstance(footprint_name, str):
        return DEFAULT_SIZE

    if footprint_name in FOOTPRINT_SIZES:
        return FOOTPRINT_SIZES[footprint_name]

    # Try prefix matching
    for key, size in FOOTPRINT_SIZES.items():
        if footprint_name.startswith(key) or key.startswith(footprint_name):
            return size

    return DEFAULT_SIZE


try:
    pcbnew = import_pcbnew()
except Exception:
    pcbnew = None


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _score_mod_candidate(target: str, candidate_path: str) -> float:
    stem = os.path.splitext(os.path.basename(candidate_path))[0]
    target_norm = _normalize_name(target)
    stem_norm = _normalize_name(stem)
    score = SequenceMatcher(None, target_norm, stem_norm).ratio()

    target_head = target.split("_", 1)[0].lower()
    if stem.lower().startswith(target_head):
        score += 0.1
    if target_head in stem.lower():
        score += 0.05
    return score


def _candidate_kicad_footprint_roots():
    roots = [str(path) for path in footprint_roots()]
    for key, value in os.environ.items():
        if not value:
            continue
        if key.startswith("KICAD") and "FOOTPRINT" in key and value not in roots:
            roots.append(value)

    if sys.platform.startswith("linux"):
        roots.extend(("/usr/share/kicad-nightly/footprints", "/usr/local/share/kicad-nightly/footprints"))

    project_root = Path(__file__).resolve().parents[1]
    roots.extend(
        [
            str(project_root / "kicad-footprints"),
            str(project_root / "vendor" / "kicad-footprints"),
            str(project_root / "third_party" / "kicad-footprints"),
            str(project_root / "data" / "kicad-footprints"),
            str(project_root.parent / "kicad-footprints"),
        ]
    )

    normalized = []
    seen = set()
    for root in roots:
        root_path = str(Path(root).expanduser())
        if root_path and root_path not in seen:
            normalized.append(root_path)
            seen.add(root_path)
    return tuple(normalized)


def _candidate_pretty_dirs(lib_name: str):
    pretty_dirs = []
    seen = set()
    suffix = f"{lib_name}.pretty"

    for root in _candidate_kicad_footprint_roots():
        root_path = Path(root)
        for candidate in (
            root_path / suffix,
            root_path / "footprints" / suffix,
            root_path / "kicad-footprints" / suffix,
        ):
            candidate_str = str(candidate)
            if candidate_str not in seen:
                pretty_dirs.append(candidate_str)
                seen.add(candidate_str)

    return pretty_dirs


def _resolve_kicad_mod_path(footprint_name: str):
    if ":" not in str(footprint_name):
        return None

    lib_name, fp_name = footprint_name.split(":", 1)
    best_match = None
    best_score = -1.0

    for pretty_dir in _candidate_pretty_dirs(lib_name):
        mod_file = os.path.join(pretty_dir, f"{fp_name}.kicad_mod")
        if os.path.exists(mod_file):
            return mod_file
        if not os.path.isdir(pretty_dir):
            continue

        parts = fp_name.split("_")
        for width in range(len(parts), 0, -1):
            prefix = "_".join(parts[:width])
            matches = sorted(glob.glob(os.path.join(pretty_dir, f"{prefix}*.kicad_mod")))
            if matches:
                candidate = max(matches, key=lambda path: _score_mod_candidate(fp_name, path))
                score = _score_mod_candidate(fp_name, candidate)
                if score > best_score:
                    best_match = candidate
                    best_score = score
                break

        matches = sorted(glob.glob(os.path.join(pretty_dir, "*.kicad_mod")))
        if not matches:
            continue

        candidate = max(matches, key=lambda path: _score_mod_candidate(fp_name, path))
        score = _score_mod_candidate(fp_name, candidate)
        if score > best_score:
            best_match = candidate
            best_score = score

    if best_match and best_score >= 0.55:
        return best_match
    return None


def _measure_kicad_mod(mod_file: str | None):
    if not pcbnew or not mod_file or not os.path.exists(mod_file):
        return None
    try:
        mod_dir = os.path.dirname(mod_file)
        mod_name = os.path.splitext(os.path.basename(mod_file))[0]
        fprint = pcbnew.FootprintLoad(mod_dir, mod_name)
        if fprint is None:
            return None
        fprint.Reference().SetVisible(False)
        fprint.Value().SetVisible(False)
        bbox = fprint.GetBoundingBox()
        return float(bbox.GetWidth() / 1e6), float(bbox.GetHeight() / 1e6)
    except Exception:
        return None


def _explicit_dimensions(meta: dict):
    w = meta.get("width")
    h = meta.get("height")
    if w is None or h is None:
        return None
    return float(w), float(h)


def _exact_kicad_dimensions(meta: dict):
    if not isinstance(meta, dict):
        return None

    dims = _explicit_dimensions(meta)
    if meta.get("dimension_source") == "kicad_mod_bbox" and dims is not None:
        return dims

    mod_file = meta.get("resolved_kicad_mod")
    exact_dims = _measure_kicad_mod(mod_file)
    if exact_dims is not None:
        return exact_dims

    footprint_name = meta.get("kicad_footprint") or meta.get("footprint")
    resolved_mod = _resolve_kicad_mod_path(footprint_name) if footprint_name else None
    exact_dims = _measure_kicad_mod(resolved_mod)
    if exact_dims is not None:
        return exact_dims

    return None


def estimate_all_sizes(footprint_mapping: dict) -> dict:
    """
    Given a dict {ref: footprint_name} OR {ref: {metadata}}, return {ref: (w, h)}.

    Prioritization:
    1. Exact KiCad footprint bounding box, when a real .kicad_mod is available
    2. Explicit width/height metadata already stored for the footprint
    3. Approximate dictionary fallback
    """
    results = {}
    for ref, fp in footprint_mapping.items():
        if isinstance(fp, dict):
            exact_dims = _exact_kicad_dimensions(fp)
            if exact_dims is not None:
                results[ref] = exact_dims
                continue

            explicit_dims = _explicit_dimensions(fp)
            if explicit_dims is not None:
                results[ref] = explicit_dims
                continue

            fallback_name = (
                fp.get("fallback_class")
                or fp.get("kicad_footprint")
                or fp.get("footprint")
                or fp.get("class")
                or ""
            )
            results[ref] = estimate_size(str(fallback_name).split(":", 1)[-1])
            continue

        fp_name = str(fp or "")
        resolved_mod = _resolve_kicad_mod_path(fp_name)
        exact_dims = _measure_kicad_mod(resolved_mod)
        if exact_dims is not None:
            results[ref] = exact_dims
            continue

        results[ref] = estimate_size(fp_name.split(":", 1)[-1])

    return results

"""resolve_components — deterministic KiCad symbol resolver.

Call ONCE with a list of parts; it searches the standard KiCad libraries in
either the legacy ``/tmp/kicad-symbols`` split layout or the installed
monolithic ``/usr/share/kicad/symbols`` layout. It returns, per part, the exact
symbol lib_id, the symbol's own footprint, and its parsed pins — so the agent
never has to browse the library iteratively.

CLI:
    python -m circuit_synth.kicad.resolve_components ESP32-WROOM-32 AMS1117-3.3 \\
           Regulator_Linear:AMS1117-3.3 resistor USB_B_Micro

Each part arg may be:
  * a full lib_id      ``Regulator_Linear:AMS1117-3.3``  (resolved directly)
  * a bare symbol name ``AMS1117-3.3`` / ``R`` / ``LED``  (located by scanning)
  * a role alias       ``resistor`` / ``cap`` / ``led``    (mapped, then located)

Prints one JSON object: ``{<part>: {found, symbol, footprint, reference,
pin_count, pins:[{name,number,type}]}}``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

DEFAULT_KICAD_SYM_DIR = "/tmp/kicad-symbols"
SYSTEM_KICAD_SYM_DIR = "/usr/share/kicad/symbols"

# role -> KiCad symbol name (bare). Lets the agent say "resistor" not "R".
_ALIASES = {
    "resistor": "R", "res": "R", "r": "R",
    "capacitor": "C", "cap": "C", "c": "C",
    "cap_polarized": "CP", "electrolytic": "CP", "cp": "CP",
    "led": "LED",
    "diode": "D", "schottky": "D_Schottky", "zener": "D_Zener",
    "inductor": "L", "ferrite": "FerriteBead", "fuse": "Fuse",
    "crystal": "Crystal",
    "npn": "Q_NPN_BCE", "pnp": "Q_PNP_BCE", "nmos": "Q_NMOS_GDS", "pmos": "Q_PMOS_GDS",
    "switch": "SW_Push", "button": "SW_Push",
}


def _find_direct(kicad_dir: Path, lib: str, name: str) -> Optional[Path]:
    split_file = kicad_dir / f"{lib}.kicad_symdir" / f"{name}.kicad_sym"
    if split_file.exists():
        return split_file
    monolithic_file = kicad_dir / f"{lib}.kicad_sym"
    return monolithic_file if monolithic_file.exists() else None


_MONOLITHIC_INDEX: Dict[Path, Dict[str, Tuple[str, Path]]] = {}


def _monolithic_index(kicad_dir: Path) -> Mapping[str, Tuple[str, Path]]:
    """Return symbol-name -> (library, file) for stock monolithic libraries."""
    try:
        key = kicad_dir.resolve()
    except OSError:
        key = kicad_dir
    cached = _MONOLITHIC_INDEX.get(key)
    if cached is not None:
        return cached

    index: Dict[str, Tuple[str, Path]] = {}
    if key.is_dir():
        symbol_pattern = re.compile(r'\(symbol\s+"([^"]+)"')
        for symbol_file in sorted(key.glob("*.kicad_sym")):
            try:
                text = symbol_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            library = symbol_file.stem
            for symbol_name in symbol_pattern.findall(text):
                # Unit graphics such as R_0_1 are harmless; keeping the first
                # occurrence preserves the same deterministic behavior as the
                # split-library scan.
                index.setdefault(symbol_name, (library, symbol_file))
    _MONOLITHIC_INDEX[key] = index
    return index


def _find_by_name(kicad_dir: Path, name: str) -> Optional[Tuple[str, Path]]:
    """Find a symbol in either split or installed monolithic libraries."""
    if not kicad_dir.is_dir():
        return None
    for libdir in sorted(kicad_dir.glob("*.kicad_symdir")):
        f = libdir / f"{name}.kicad_sym"
        if f.exists():
            return libdir.name[: -len(".kicad_symdir")], f
    return _monolithic_index(kicad_dir).get(name)


def _symbol_roots(kicad_sym_dir: str) -> List[Path]:
    """Ordered, de-duplicated symbol roots for portable host/container use."""
    raw_roots = [kicad_sym_dir]
    raw_roots.extend(
        value for value in os.environ.get("KICAD_SYMBOL_DIR", "").split(os.pathsep)
        if value
    )
    raw_roots.append(SYSTEM_KICAD_SYM_DIR)

    roots: List[Path] = []
    seen = set()
    for raw in raw_roots:
        root = Path(raw).expanduser()
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        roots.append(root)
    return roots


def _parse_one(kicad_dirs: List[Path], part: str) -> Mapping:
    # 1. decide lib/name
    if ":" in part:
        lib, name = part.split(":", 1)
        name = name.strip()
        lib_resolved = lib.strip()
        f = next(
            (hit for root in kicad_dirs
             if (hit := _find_direct(root, lib_resolved, name)) is not None),
            None,
        )
    else:
        name = _ALIASES.get(part.strip().lower(), part.strip())
        hit = next(
            (found for root in kicad_dirs
             if (found := _find_by_name(root, name)) is not None),
            None,
        )
        if hit:
            lib_resolved, f = hit
        else:
            lib_resolved, f = None, None

    if f is None:
        return {"found": False, "query": part, "note": "no matching KiCad symbol"}

    # 2. parse (lazy import — pulls the circuit_synth parser chain)
    try:
        from circuit_synth.kicad.kicad_symbol_parser import parse_kicad_sym_file

        data = parse_kicad_sym_file(str(f))
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "query": part, "error": f"{type(exc).__name__}: {exc}"}

    symbols = data.get("symbols", {}) if isinstance(data, dict) else {}
    sym_data = symbols.get(name) or (next(iter(symbols.values()), {}) if symbols else {})
    props = sym_data.get("properties", {}) if isinstance(sym_data, dict) else {}
    raw_pins = sym_data.get("pins", []) if isinstance(sym_data, dict) else []
    pins = [
        {"name": p.get("name"), "number": p.get("number"),
         "type": p.get("function") or p.get("type")}
        for p in raw_pins
    ]
    sym_name = sym_data.get("name", name) if isinstance(sym_data, dict) else name
    return {
        "found": True,
        "query": part,
        "symbol": f"{lib_resolved}:{sym_name}",
        "footprint": (props.get("Footprint") or None),
        "reference": props.get("Reference"),
        "pin_count": len(pins),
        "pins": pins,
    }


def resolve(parts: List[str], kicad_sym_dir: str = DEFAULT_KICAD_SYM_DIR) -> Mapping:
    """Resolve a list of part identifiers to KiCad symbol lib_ids + pins."""
    kicad_dirs = _symbol_roots(kicad_sym_dir)
    return {p: _parse_one(kicad_dirs, p) for p in parts}


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write(
            "Usage: python -m circuit_synth.kicad.resolve_components "
            "<part|lib_id|alias> [<part> ...]\n"
        )
        return 2
    print(json.dumps(resolve(argv), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Find exact KiCad symbols and package-compatible footprints on common OS installs."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any


def _unique(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            resolved = path.expanduser()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _split_path_setting(value: str) -> list[Path]:
    return [Path(item.strip().strip('"')) for item in value.split(os.pathsep) if item.strip()]


def _install_roots() -> list[Path]:
    roots: list[Path] = []
    if os.environ.get("KICAD_ROOT"):
        roots.append(Path(os.environ["KICAD_ROOT"]).expanduser())

    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(variable)
            if value:
                parent = Path(value) / ("Programs/KiCad" if variable == "LOCALAPPDATA" else "KiCad")
                roots.append(parent)
        roots.extend((Path("C:/KiCad"), Path("C:/Program Files/KiCad")))
    elif sys.platform == "darwin":
        roots.extend((
            Path("/Applications/KiCad/KiCad.app/Contents"),
            Path("/Applications/kicad/KiCad.app/Contents"),
            Path("/Applications/kicad/Kicad.app/Contents"),
            Path("/Applications/KiCad.app/Contents"),
            Path.home() / "Applications/KiCad/KiCad.app/Contents",
            Path("/opt/homebrew/opt/kicad"),
            Path("/usr/local/opt/kicad"),
        ))
    else:
        roots.extend((
            Path("/usr"), Path("/usr/local"), Path("/opt/kicad"),
            Path("/usr/lib/kicad"), Path("/usr/share/kicad-nightly"),
            Path.home() / ".local/share/kicad",
        ))

    expanded = list(roots)
    for root in roots:
        if root.is_dir() and (sys.platform == "win32" or root == Path(os.environ.get("KICAD_ROOT", "!"))):
            try:
                expanded.extend(child for child in root.iterdir() if child.is_dir())
            except OSError:
                pass
        if root.suffix.casefold() == ".app":
            expanded.extend((root / "Contents", root / "Contents" / "SharedSupport"))
        elif root.name.casefold() == "contents":
            expanded.append(root / "SharedSupport")
    return _unique(expanded)


def _derive_library_roots(kind: str, explicit: list[Path] | None = None) -> list[Path]:
    direct_roots: list[Path] = list(explicit or [])
    if kind == "symbols":
        setting_names = ("KICAD_SYMBOL_DIR", "KICAD_SYMBOLS")
    else:
        setting_names = (
            "KICAD_FOOTPRINTS", "KISYSMOD", "KICAD10_FOOTPRINT_DIR",
            "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR",
            "KICAD7_FOOTPRINT_DIR", "KICAD6_FOOTPRINT_DIR",
        )
    for name in setting_names:
        direct_roots.extend(_split_path_setting(os.environ.get(name, "")))

    candidates: list[Path] = []
    for root in _install_roots():
        candidates.extend((
            root / "share" / "kicad" / kind,
            root / kind,
            root / "SharedSupport" / kind,
            root / "Contents" / "SharedSupport" / kind,
        ))

    if kind == "symbols":
        if sys.platform == "darwin":
            candidates.extend((
                Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
                Path("/Applications/kicad/KiCad.app/Contents/SharedSupport/symbols"),
                Path("/opt/homebrew/share/kicad/symbols"),
                Path("/usr/local/share/kicad/symbols"),
            ))
        else:
            candidates.extend((Path("/usr/share/kicad/symbols"), Path("/usr/local/share/kicad/symbols")))
    else:
        if sys.platform == "darwin":
            candidates.extend((
                Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
                Path("/Applications/kicad/KiCad.app/Contents/SharedSupport/footprints"),
                Path("/opt/homebrew/share/kicad/footprints"),
                Path("/usr/local/share/kicad/footprints"),
            ))
        else:
            candidates.extend((Path("/usr/share/kicad/footprints"), Path("/usr/local/share/kicad/footprints")))

    expected_name = kind.casefold()
    direct = [path for path in direct_roots if path.is_dir()]
    discovered = [path for path in candidates if path.is_dir() and path.name.casefold() == expected_name]
    return _unique(direct + discovered)


def _walk_files(root: Path, suffix: str):
    """Yield matching files while skipping inaccessible installation directories."""
    for current, dirs, files in os.walk(root, onerror=lambda _error: None):
        if suffix == ".kicad_mod" and Path(current).suffix != ".pretty":
            dirs[:] = [name for name in dirs if name.endswith(".pretty")]
            continue
        for filename in files:
            if filename.casefold().endswith(suffix):
                yield Path(current) / filename


def _symbol_matches(root: Path, query: str) -> list[dict[str, Any]]:
    pattern = re.compile(r'\(symbol\s+"((?:\\.|[^"\\])*)"')
    found: list[dict[str, Any]] = []
    for library in _walk_files(root, ".kicad_sym"):
        try:
            text = library.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(match.group(1).casefold() == query.casefold() for match in pattern.finditer(text)):
            found.append({
                "symbol_name": query,
                "library_file": str(library.resolve()),
                "library_nickname": library.stem,
                "root": str(root),
            })
    return found


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _footprint_matches(root: Path, footprint_name: str | None, package_name: str | None) -> list[dict[str, Any]]:
    query = (footprint_name or package_name or "").strip()
    if not query:
        return []
    normalized_query = _normalized(query)
    found: list[dict[str, Any]] = []
    for path in _walk_files(root, ".kicad_mod"):
        stem = path.stem
        normalized_stem = _normalized(stem)
        if footprint_name:
            if stem.casefold() != footprint_name.casefold():
                continue
            match_type = "exact_name"
        elif normalized_stem == normalized_query:
            match_type = "exact_package_name"
        elif normalized_stem.startswith(normalized_query):
            match_type = "package_prefix"
        elif normalized_query in normalized_stem:
            match_type = "package_name_contains"
        else:
            continue
        library_dir = path.parent
        found.append({
            "footprint_name": stem,
            "match_type": match_type,
            "library_dir": str(library_dir.resolve()),
            "library_nickname": library_dir.name.removesuffix(".pretty"),
            "footprint_file": str(path.resolve()),
            "root": str(root),
        })
    rank = {"exact_name": 0, "exact_package_name": 0, "package_prefix": 1, "package_name_contains": 2}
    return sorted(found, key=lambda item: (rank[item["match_type"]], item["footprint_name"].casefold()))


def find_installed_libraries(
    *,
    symbol_name: str,
    package_name: str | None = None,
    footprint_name: str | None = None,
    symbols_dir: str | Path | None = None,
    footprints_dir: str | Path | None = None,
    max_footprint_matches: int = 50,
) -> dict[str, Any]:
    """Search discovered or explicitly supplied KiCad library roots."""
    if not symbol_name.strip():
        raise ValueError("symbol_name or exact MPN is required")
    explicit_symbols = [Path(symbols_dir).expanduser()] if symbols_dir else None
    explicit_footprints = [Path(footprints_dir).expanduser()] if footprints_dir else None
    symbol_roots = _derive_library_roots("symbols", explicit_symbols)
    footprint_roots = _derive_library_roots("footprints", explicit_footprints)

    symbols = [match for root in symbol_roots for match in _symbol_matches(root, symbol_name.strip())]
    footprints = [
        match for root in footprint_roots
        for match in _footprint_matches(root, footprint_name, package_name)
    ][:max_footprint_matches]
    return {
        "success": True,
        "platform": sys.platform,
        "symbol_query": symbol_name.strip(),
        "package_query": package_name,
        "footprint_query": footprint_name,
        "symbol_roots": [str(root) for root in symbol_roots],
        "footprint_roots": [str(root) for root in footprint_roots],
        "symbol_found": bool(symbols),
        "symbol_matches": symbols,
        "footprint_found": bool(footprints),
        "footprint_matches": footprints,
        "footprint_match_limit": max_footprint_matches,
        "note": "Package-name matches are candidates. Confirm the exact package and pad mapping against the datasheet before reuse.",
    }

"""Discover and validate the host KiCad installation used by pcb_workflow.

KiCad is supplied by the host or Agent-EDA. This module never installs it.
Environment overrides take precedence over platform-specific default locations.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any


_DLL_DIRECTORY_HANDLES: list[Any] = []


def _path_value(name: str) -> Path | None:
    value = os.environ.get(name, "").strip().strip('"')
    return Path(value).expanduser() if value else None


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


def _default_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                root = Path(base) / ("Programs/KiCad" if variable == "LOCALAPPDATA" else "KiCad")
                roots.append(root)
        roots.append(Path("C:/KiCad"))
    elif sys.platform == "darwin":
        roots.extend((
            Path("/Applications/KiCad/KiCad.app/Contents"),
            Path("/Applications/kicad/KiCad.app/Contents"),
            Path("/Applications/kicad/Kicad.app/Contents"),
            Path("/Applications/KiCad.app/Contents"),
            Path("/opt/homebrew/opt/kicad"),
            Path("/usr/local/opt/kicad"),
        ))
    else:
        roots.extend((
            Path("/usr"), Path("/usr/local"), Path("/opt/kicad"),
            Path("/usr/lib/kicad"), Path("/usr/share/kicad-nightly"),
        ))
    return roots


def _root_candidates() -> list[Path]:
    roots: list[Path] = []
    override = _path_value("KICAD_ROOT")
    if override is not None:
        roots.append(override)
    roots.extend(_default_roots())

    # Windows and custom installs commonly put a version directory below the
    # KiCad parent (for example, Program Files/KiCad/10.0).
    expanded = list(roots)
    if sys.platform == "win32":
        for root in roots:
            if root.is_dir():
                try:
                    expanded.extend(child for child in root.iterdir() if child.is_dir())
                except OSError:
                    pass
    for root in roots:
        if root.suffix.lower() == ".app":
            expanded.extend((root / "Contents", root / "Contents" / "SharedSupport"))
        elif root.name.lower() == "contents":
            expanded.append(root / "SharedSupport")
    return _unique(expanded)


def _binding_candidates() -> list[Path]:
    candidates: list[Path] = []
    override = _path_value("KICAD_PYTHON_PATH")
    if override is not None:
        candidates.append(override)

    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}"
    for root in _root_candidates():
        candidates.extend((
            root / "bin",
            root / "lib" / "python3" / "dist-packages",
            root / "lib" / "python3" / "site-packages",
            root / "lib" / f"python{version}" / "site-packages",
            root / "lib" / f"python{version}" / "dist-packages",
            root / "Frameworks" / "Python.framework" / "Versions" / "Current" / "lib" / f"python{version}" / "site-packages",
            root / "Contents" / "Frameworks" / "Python.framework" / "Versions" / "Current" / "lib" / f"python{version}" / "site-packages",
        ))

    if sys.platform.startswith("linux"):
        candidates.extend((
            Path("/usr/lib/python3/dist-packages"),
            Path("/usr/local/lib/python3/dist-packages"),
            Path(f"/usr/lib/python{version}/site-packages"),
            Path(f"/usr/local/lib/python{version}/site-packages"),
            Path(f"/usr/lib/python{version}/dist-packages"),
        ))

    return _unique([path for path in candidates if path.is_dir()])


def _looks_like_binding_directory(path: Path) -> bool:
    try:
        return any(path.glob("pcbnew.py")) or any(path.glob("_pcbnew*"))
    except OSError:
        return False


def _dll_directories() -> list[Path]:
    directories: list[Path] = []
    override = _path_value("KICAD_BIN")
    if override is not None:
        directories.append(override)
    directories.extend(root / "bin" for root in _root_candidates())
    return _unique([path for path in directories if path.is_dir()])


def configure_kicad_import_paths() -> list[Path]:
    """Add discovered pcbnew and native-library locations to this process."""
    binding_dirs = [path for path in _binding_candidates() if _looks_like_binding_directory(path)]
    for path in reversed(binding_dirs):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        for path in _dll_directories():
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(path)))
            except (OSError, AttributeError):
                continue

    return binding_dirs


def import_pcbnew() -> Any:
    """Load pcbnew after configuring the host KiCad module and DLL paths."""
    configure_kicad_import_paths()
    try:
        return importlib.import_module("pcbnew")
    except (ImportError, OSError) as exc:
        raise RuntimeError(_pcbnew_error(exc)) from exc


def _pcbnew_error(exc: BaseException) -> str:
    binding_override = os.environ.get("KICAD_PYTHON_PATH", "<unset>")
    bin_override = os.environ.get("KICAD_BIN", "<unset>")
    root = os.environ.get("KICAD_ROOT", "<auto-detect>")
    candidates = _binding_candidates()
    found = [str(path) for path in candidates if _looks_like_binding_directory(path)]
    found_text = ", ".join(found) if found else "no pcbnew module directory found"
    return (
        "Could not load KiCad's pcbnew Python binding. "
        f"Workflow Python: {sys.executable} ({sys.version.split()[0]}). "
        f"KiCad root: {root}; binding override: {binding_override}; "
        f"binary/DLL override: {bin_override}; detected binding locations: {found_text}. "
        "Set KICAD_ROOT to the KiCad installation root, or set KICAD_PYTHON_PATH "
        "to the directory containing pcbnew.py/_pcbnew and KICAD_BIN to the "
        "directory containing KiCad's native libraries. The binding must be "
        "compatible with this Python version and KiCad installation. "
        f"Import error: {type(exc).__name__}: {exc}"
    )


def footprint_roots() -> list[Path]:
    """Return discovered footprint-library roots, with explicit settings first."""
    roots: list[Path] = []
    for variable in (
        "KICAD_FOOTPRINTS", "KISYSMOD", "KICAD10_FOOTPRINT_DIR",
        "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR",
        "KICAD7_FOOTPRINT_DIR", "KICAD6_FOOTPRINT_DIR",
    ):
        value = _path_value(variable)
        if value is not None:
            roots.append(value)

    for root in _root_candidates():
        roots.extend((
            root / "share" / "kicad" / "footprints",
            root / "footprints",
            root / "SharedSupport" / "footprints",
        ))

    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                roots.append(Path(base) / "KiCad" / "share" / "kicad" / "footprints")
    elif sys.platform == "darwin":
        roots.extend((
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
            Path("/Applications/kicad/KiCad.app/Contents/SharedSupport/footprints"),
            Path("/Applications/kicad/Kicad.app/Contents/SharedSupport/footprints"),
        ))
    else:
        roots.extend((
            Path("/usr/share/kicad/footprints"),
            Path("/usr/local/share/kicad/footprints"),
            Path.home() / ".local/share/kicad/footprints",
        ))

    return _unique([path for path in roots if path.is_dir()])


def find_kicad_cli(*, required: bool = True) -> Path | None:
    """Find kicad-cli from an explicit override, KiCad roots, or PATH."""
    configured = _path_value("KICAD_CLI")
    candidates: list[Path] = []
    if configured is not None:
        candidates.append(configured / ("kicad-cli.exe" if sys.platform == "win32" else "kicad-cli") if configured.is_dir() else configured)

    executable_names = ("kicad-cli.exe", "kicad-cli") if sys.platform == "win32" else ("kicad-cli",)
    binary_override = _path_value("KICAD_BIN")
    if binary_override is not None:
        candidates.extend(binary_override / name for name in executable_names)
    for root in _root_candidates():
        candidates.extend(root / "bin" / name for name in executable_names)
        candidates.extend(root / "MacOS" / name for name in executable_names)
        candidates.extend(root / name for name in executable_names)
    on_path = shutil.which("kicad-cli") or shutil.which("kicad-cli.exe")
    if on_path:
        candidates.append(Path(on_path))

    for candidate in _unique(candidates):
        if candidate.is_file():
            return candidate
    if required:
        raise FileNotFoundError(
            "Could not find kicad-cli. Agent-EDA must install/provide KiCad, or set "
            "KICAD_CLI to the executable (or its directory), KICAD_BIN to its "
            "directory, or KICAD_ROOT to the KiCad installation root."
        )
    return None


def preflight(*, require_cli: bool = False, require_footprints: bool = False,
              require_torch: bool = False, require_packages: tuple[str, ...] = (),
              footprints_override: Path | None = None) -> dict[str, Any]:
    """Validate requested KiCad and Python dependencies before a workflow stage."""
    pcbnew = import_pcbnew()
    result: dict[str, Any] = {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "pcbnew_version": getattr(pcbnew, "GetBuildVersion", lambda: "unknown")(),
        "binding_paths": [str(path) for path in _binding_candidates() if _looks_like_binding_directory(path)],
    }
    if require_cli:
        result["kicad_cli"] = str(find_kicad_cli(required=True))
    if require_footprints:
        roots = ([footprints_override.expanduser().resolve()]
                 if footprints_override is not None else footprint_roots())
        roots = [path for path in roots if path.is_dir()]
        if not roots:
            raise FileNotFoundError(
                "Could not find KiCad footprint libraries. Agent-EDA must provide KiCad, "
                "or set KICAD_FOOTPRINTS to the directory containing <Library>.pretty folders."
            )
        result["footprint_roots"] = [str(path) for path in roots]
    if require_torch:
        require_packages = tuple(dict.fromkeys(("torch", *require_packages)))
    for package in require_packages:
        try:
            module = importlib.import_module(package)
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                f"Workflow dependency '{package}' is unavailable in {sys.executable}: {exc}"
            ) from exc
        result[f"{package}_version"] = getattr(module, "__version__", "available")
    return result


def subprocess_environment() -> dict[str, str]:
    """Forward only platform/runtime variables needed by isolated workers."""
    keep = {
        "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "PATHEXT",
        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
        "TEMP", "TMP", "LANG", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
    }
    return {
        key: value for key, value in os.environ.items()
        if key in keep or key.startswith("KICAD_") or key == "KISYSMOD"
    }


def doctor(*, stage: str = "all") -> dict[str, Any]:
    """Report dependencies for placement, routing, or both stages."""
    if stage not in {"placement", "routing", "all"}:
        raise ValueError("stage must be 'placement', 'routing', or 'all'")
    report: dict[str, Any] = {
        "platform": sys.platform,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "check_scope": stage,
    }
    try:
        report["pcbnew"] = preflight(require_cli=False, require_footprints=False)["pcbnew_version"]
    except Exception as exc:
        report["pcbnew_error"] = str(exc)

    packages: set[str] = set()
    if stage in {"placement", "all"}:
        roots = footprint_roots()
        report["footprint_roots"] = [str(path) for path in roots]
        if not roots:
            report["footprints_error"] = (
                "KiCad footprint libraries not found; set KICAD_FOOTPRINTS or KICAD_ROOT."
            )
        packages.update(("torch", "numpy", "networkx", "deap", "shapely"))
    else:
        report["footprint_roots"] = "not required for routing"

    if stage in {"routing", "all"}:
        cli = find_kicad_cli(required=False)
        report["kicad_cli"] = str(cli or "not found")
        if cli is None:
            report["kicad_cli_error"] = "Set KICAD_CLI or KICAD_ROOT to the KiCad CLI location."
        packages.update(("numpy", "shapely"))
    else:
        report["kicad_cli"] = "not required for placement"

    for package in sorted(packages):
        try:
            module = importlib.import_module(package)
            report[package] = getattr(module, "__version__", "available")
        except (ImportError, OSError) as exc:
            report[f"{package}_error"] = str(exc)
    report["ok"] = not any(key.endswith("_error") for key in report)
    report["overrides"] = {
        name: os.environ.get(name)
        for name in ("KICAD_ROOT", "KICAD_PYTHON_PATH", "KICAD_BIN", "KICAD_CLI", "KICAD_FOOTPRINTS")
        if os.environ.get(name)
    }
    return report

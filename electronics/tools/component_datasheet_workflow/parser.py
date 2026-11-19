"""MinerU subprocess adapter owned by this standalone workflow."""

from __future__ import annotations

import json
import os
import shutil
import sys
import subprocess
from pathlib import Path
from typing import Any


def resolve_mineru_command() -> list[str]:
    """Resolve this tool's MinerU executable or its separately configured Python."""
    configured_bin = os.environ.get("COMPONENT_DATASHEET_MINERU_BIN")
    if configured_bin:
        candidate = Path(configured_bin).expanduser()
        if candidate.is_file():
            return [str(candidate)]
        raise FileNotFoundError(f"COMPONENT_DATASHEET_MINERU_BIN does not exist: {candidate}")

    configured_python = os.environ.get("COMPONENT_DATASHEET_MINERU_PYTHON")
    if configured_python:
        python = Path(configured_python).expanduser()
        if not python.is_file():
            raise FileNotFoundError(f"COMPONENT_DATASHEET_MINERU_PYTHON does not exist: {python}")
        candidates = [python.parent / "mineru", python.parent / "mineru.exe", python.parent.parent / "Scripts" / "mineru.exe"]
        binary = next((p for p in candidates if p.is_file()), None)
        if binary is None:
            raise FileNotFoundError(f"MinerU CLI not found beside configured Python: {python}")
        return [str(binary)]

    workflow_dir = Path(__file__).resolve().parent
    local_candidates = [
        workflow_dir / ".venv" / "bin" / "mineru",
        workflow_dir / ".venv" / "Scripts" / "mineru.exe",
    ]
    interpreter_binary = Path(sys.executable).parent / ("mineru.exe" if os.name == "nt" else "mineru")
    local_binary = interpreter_binary if interpreter_binary.is_file() else next((candidate for candidate in local_candidates if candidate.is_file()), None)
    if local_binary is not None:
        return [str(local_binary)]

    binary = shutil.which("mineru")
    if binary:
        return [binary]
    raise FileNotFoundError(
        "MinerU is not configured for component_datasheet_workflow. Install it in this tool's dedicated parser environment, "
        "then set COMPONENT_DATASHEET_MINERU_BIN or COMPONENT_DATASHEET_MINERU_PYTHON."
    )


def parse_datasheet(
    *,
    manifest_path: str | Path,
    output_dir: str | Path | None = None,
    backend: str = "pipeline",
    timeout_seconds: int = 1800,
    command: list[str] | None = None,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    if not manifest_file.is_file():
        raise FileNotFoundError(f"datasheet manifest not found: {manifest_file}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    pdf_path = Path(manifest["datasheet"]["path"])
    if not pdf_path.is_file():
        raise FileNotFoundError(f"datasheet PDF from manifest not found: {pdf_path}")
    if backend != "pipeline":
        raise ValueError("this workflow currently supports the MinerU pipeline backend only")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")

    parse_dir = Path(output_dir).expanduser().resolve() if output_dir else manifest_file.parent / "parser_output"
    parse_dir.mkdir(parents=True, exist_ok=True)
    invocation = command or resolve_mineru_command()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env.setdefault("OMP_NUM_THREADS", "4")
    args = [*invocation, "-p", str(pdf_path), "-o", str(parse_dir), "-b", backend]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"MinerU parsing timed out after {timeout_seconds} seconds") from exc
    if result.returncode:
        tail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-30:])
        raise RuntimeError(f"MinerU exited with status {result.returncode}:\n{tail}")

    markdown_files = sorted(parse_dir.rglob("*.md"))
    content_list_files = sorted(parse_dir.rglob("*content_list*.json"))
    if not markdown_files:
        raise RuntimeError("MinerU returned success but created no Markdown files")
    artifacts = {
        "markdown_files": [str(p) for p in markdown_files],
        "content_list_files": [str(p) for p in content_list_files]
    }
    parse_result = {
        "success": True,
        "backend": backend,
        "component": manifest["component"],
        "datasheet_sha256": manifest["datasheet"]["sha256"],
        "output_dir": str(parse_dir),
        "artifacts": artifacts,
        "agent_action": "Interpret the extracted tables and page evidence; write normalized_component.json, then run the schema validator."
    }
    result_path = parse_dir / "parse_result.json"
    parse_result["result_path"] = str(result_path)
    result_path.write_text(json.dumps(parse_result, indent=2) + "\n", encoding="utf-8")
    return parse_result

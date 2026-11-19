"""Build or reuse a workspace-level store of independent EDA tool environments."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def stage(build: Path, store: Path, *, verify_only: bool = False) -> int:
    build, store = build.resolve(), store.resolve()
    if not (build / '.agent-eda-assembly.json').is_file():
        raise ValueError('build is not an assembled Agent-EDA source tree')
    if store == build or store.is_relative_to(build):
        raise ValueError('tool environment store must be independent of the build')
    source = build / 'tools/tool_environments'
    names = sorted(path.name for path in source.iterdir() if (path / 'requirements.txt').is_file())
    if len(names) != 12:
        raise ValueError(f'expected twelve independent tool requirements; found {len(names)}')
    for name in names:
        requirement = (source / name / 'requirements.txt').read_bytes()
        target = store / name / 'requirements.txt'
        if target.is_file() and target.read_bytes() != requirement:
            raise ValueError(f'{name} requirements differ from the selected environment store')
        if not target.is_file():
            if verify_only:
                raise ValueError(f'{name} is absent from the environment store')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(requirement)
    if not verify_only:
        build_python = build / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run([str(build_python),
                        str(build / 'tools/setup_tool_environments.py'),
                        '--tools-root', str(store)], check=True)
    for name in names:
        environment = store / name / '.venv'
        executable = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not executable.is_file():
            raise ValueError(f'{name} interpreter is missing in the environment store')
        link = source / name / '.venv'
        if link.is_symlink() and link.resolve() == environment:
            continue
        if link.exists() and not link.is_symlink():
            raise ValueError(f'{name} has a local environment; preserve or migrate it explicitly')
        replacement = source / name / '.venv.eda-new-link'
        try:
            replacement.symlink_to(environment, target_is_directory=True)
            os.replace(replacement, link)
        except OSError:
            replacement.unlink(missing_ok=True)
            if os.name != 'nt':
                raise
            # Windows without symlink privilege can still run isolated copies.
            if link.is_symlink():
                link.unlink()
            shutil.copytree(environment, link)
    return len(names)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true',
                        help='use an existing store without installing packages')
    args = parser.parse_args()
    print(f'Ready: {stage(args.build, args.store, verify_only=args.verify_only)} tool environments')

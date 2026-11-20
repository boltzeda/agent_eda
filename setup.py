#!/usr/bin/env python3
"""Install a private, pinned Agent-EDA runtime beside a fresh product clone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PRODUCT = Path(__file__).resolve().parent
WORKSPACE = PRODUCT.parent
sys.path.insert(0, str(PRODUCT / 'distribution' / 'scripts'))
from assemble import assemble, checked_source  # noqa: E402
from install_profile import install  # noqa: E402
from stage_tool_envs import stage  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(list(args), cwd=cwd, check=True)


def uv_executable() -> str:
    executable = shutil.which('uv')
    if not executable:
        candidate = Path.home() / '.local' / 'bin' / ('uv.exe' if os.name == 'nt' else 'uv')
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        raise RuntimeError('uv is required. Install uv, then rerun this setup command.')
    return executable


def source_digest() -> str:
    digest = hashlib.sha256()
    sources = (PRODUCT / 'electronics', PRODUCT / 'distribution' / 'branding',
               PRODUCT / 'distribution' / 'overlays', PRODUCT / 'distribution' / 'patches')
    files = []
    for root in sources:
        for directory, subdirs, names in os.walk(root):
            subdirs[:] = [name for name in subdirs if name not in ('__pycache__', '.venv', 'node_modules')]
            files.extend(Path(directory) / name for name in names)
    files += [PRODUCT / 'distribution' / 'product.json', PRODUCT / 'distribution' / 'upstream.lock.json']
    for path in sorted(files):
        if ({'__pycache__', '.venv', 'node_modules'} & set(path.parts)
                or path.suffix in ('.pyc', '.pyo')):
            continue
        digest.update(path.relative_to(PRODUCT).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:12]


def reference_checkout(reference: Path, lock: dict) -> None:
    if not reference.exists():
        reference.parent.mkdir(parents=True, exist_ok=True)
        print(f'Fetching pinned Hermes source into {reference}', flush=True)
        run('git', 'clone', '--filter=blob:none', '--no-checkout', lock['repository'], str(reference))
        run('git', '-C', str(reference), 'remote', 'set-url', '--push', 'origin', 'DISABLED')
        run('git', '-C', str(reference), 'checkout', '--detach', lock['baseline']['commit'])
    checked_source(reference, lock, 'baseline')


def write_local_record(build: Path, home: Path) -> None:
    data = WORKSPACE / 'data'
    data.mkdir(parents=True, exist_ok=True)
    manifest = build / '.agent-eda-assembly.json'
    record = {'build': build.name, 'home': home.name,
              'assembly_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    if (PRODUCT / '.git').is_dir():
        record['product_revision'] = subprocess.check_output(
            ['git', '-C', str(PRODUCT), 'rev-parse', 'HEAD'], text=True).strip()
    target = data / 'local_install.json'
    temporary = data / 'local_install.json.tmp'
    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, target)


def setup(reference: Path, store: Path | None, provider: str | None,
          model: str | None, pcb_python: str | None) -> Path:
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is needed to run setup.py')
    if bool(provider) != bool(model):
        raise ValueError('--provider and --model must be given together')
    uv = uv_executable()
    lock = json.loads((PRODUCT / 'distribution' / 'upstream.lock.json').read_text(encoding='utf-8'))
    reference_checkout(reference, lock)
    name = f"{lock['baseline']['tag']}-eda-local-{source_digest()}"
    build = WORKSPACE / 'builds' / name
    prior_record = WORKSPACE / 'data' / 'local_install.json'
    if prior_record.is_file() and json.loads(prior_record.read_text(encoding='utf-8')).get('build') != name:
        raise RuntimeError('This clone has a different installed build. Use the qualified upgrade process; '
                           'setup.py will not replace an active profile with a changed build.')
    if not build.exists():
        print('Assembling Agent-EDA from the pinned Hermes source...', flush=True)
        assemble(PRODUCT, reference, build)
    manifest = json.loads((build / '.agent-eda-assembly.json').read_text(encoding='utf-8'))
    if manifest['upstream']['commit'] != lock['baseline']['commit']:
        raise RuntimeError('Existing local build does not match the pinned Hermes baseline')
    print('Installing the pinned Agent-EDA runtime...', flush=True)
    run(uv, 'sync', '--locked', '--no-dev', '--python', '3.14', cwd=build)
    if store:
        print('Checking and linking the existing private tool environments...', flush=True)
        stage(build, store, verify_only=True)
    else:
        print('Creating twelve private tool environments (first run may take time)...', flush=True)
        python = build / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        command = [str(python), str(build / 'tools/setup_tool_environments.py')]
        if pcb_python:
            command += ['--pcb-python', pcb_python]
        run(*command, cwd=build)
    home = WORKSPACE / 'data' / 'agent_eda-local'
    if not (home / 'config.yaml').is_file():
        print('Creating a private Agent-EDA profile...', flush=True)
        install(build, home, provider, model)
    elif provider or model:
        raise RuntimeError('Existing profile already has settings; use eda model to change provider')
    write_local_record(build, home)
    print(f'Agent-EDA ready: {PRODUCT / "bin" / "eda"}', flush=True)
    if not provider:
        print('Choose your provider with eda model before chatting.', flush=True)
    return build


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=WORKSPACE / 'upstream' / 'hermes',
                        help='existing clean Hermes reference; default fetches it beside this clone')
    parser.add_argument('--reuse-tool-store', type=Path,
                        help='reuse an existing matching tool environment store (for maintainers)')
    parser.add_argument('--provider', help='optional provider name; default selects later with eda model')
    parser.add_argument('--model', help='model name to pair with --provider')
    parser.add_argument('--pcb-python', help='host Python executable with KiCad pcbnew bindings')
    args = parser.parse_args()
    try:
        setup(args.reference.resolve(), args.reuse_tool_store.resolve() if args.reuse_tool_store else None,
              args.provider, args.model, args.pcb_python)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, KeyError) as exc:
        parser.exit(1, f'Agent-EDA setup stopped: {exc}\n')

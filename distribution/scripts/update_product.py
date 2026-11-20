"""Install fast-forward Agent-EDA commits without modifying the pinned Hermes release."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from assemble import assemble


def run(*command: str, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ''


def git(product: Path, *args: str) -> str:
    return run('git', '-C', str(product), *args, capture=True)


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    if root.is_dir():
        for path in sorted(root.rglob('*')):
            if path.is_file():
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def update(product: Path) -> int:
    product = product.resolve()
    workspace = product.parent
    data = workspace / 'data'
    record_path = data / 'local_install.json'
    if (data / 'active_build.json').exists():
        raise RuntimeError('This installation uses a qualified Hermes build; use the qualification upgrade process.')
    if not record_path.is_file():
        raise RuntimeError('Agent-EDA is not installed; run python3 setup.py first.')
    if git(product, 'remote', 'get-url', 'origin') not in (
            'https://github.com/boltzeda/agent_eda.git',
            'git@github.com:boltzeda/agent_eda.git',
            'ssh://git@github.com/boltzeda/agent_eda.git'):
        raise RuntimeError('Origin is not the Agent-EDA release repository.')
    if git(product, 'symbolic-ref', '--short', 'HEAD') != 'main':
        raise RuntimeError('Switch to the main branch before updating Agent-EDA.')
    if git(product, 'status', '--porcelain'):
        raise RuntimeError('The Agent-EDA source clone has local changes; preserve them before updating.')
    old_record = json.loads(record_path.read_text(encoding='utf-8'))
    old_build = workspace / 'builds' / old_record['build']
    old_home = data / old_record['home']
    if (not old_build.is_dir() or old_build.is_symlink() or
            not old_home.is_dir() or old_home.is_symlink() or
            old_build.parent != workspace / 'builds' or old_home.parent != data):
        raise RuntimeError('Installed build or profile is missing or unsafe.')
    old_manifest = old_build / '.agent-eda-assembly.json'
    if not old_manifest.is_file() or hashlib.sha256(old_manifest.read_bytes()).hexdigest() != old_record['assembly_manifest_sha256']:
        raise RuntimeError('Installed build manifest has changed.')
    print('Checking Agent-EDA releases on GitHub...', flush=True)
    run('git', '-C', str(product), 'fetch', '--no-tags', 'origin', 'main')
    head = git(product, 'rev-parse', 'HEAD')
    remote = git(product, 'rev-parse', 'FETCH_HEAD')
    subprocess.run(['git', '-C', str(product), 'merge-base', '--is-ancestor', head, remote], check=True)
    if head != remote:
        run('git', '-C', str(product), 'merge', '--ff-only', remote)
    revision = git(product, 'rev-parse', 'HEAD')
    if old_record.get('product_revision') == revision:
        print(f'Agent-EDA is current at {revision[:12]}.', flush=True)
        return 0
    # A failed installation leaves the old launcher record and profile intact.
    sys.path.insert(0, str(product))
    from setup import reference_checkout, source_digest, uv_executable
    lock = json.loads((product / 'distribution/upstream.lock.json').read_text(encoding='utf-8'))
    reference = workspace / 'upstream' / 'hermes'
    reference_checkout(reference, lock)
    name = f"{lock['baseline']['tag']}-eda-local-{source_digest()}-{revision[:12]}"
    candidate = workspace / 'builds' / name
    if not candidate.exists():
        print(f'Assembling Agent-EDA {revision[:12]}...', flush=True)
        assemble(product, reference, candidate, progress=lambda message: print(message, flush=True))
    manifest = candidate / '.agent-eda-assembly.json'
    if not manifest.is_file():
        raise RuntimeError('Candidate build is incomplete.')
    uv = uv_executable()
    run(uv, 'sync', '--locked', '--no-dev', '--python', '3.14', cwd=candidate)
    python = candidate / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    old_envs = old_build / 'tools' / 'tool_environments'
    new_envs = candidate / 'tools' / 'tool_environments'
    vendor = Path('tools/kicad_generation/vendor/circuit-synth')
    vendor_changed = digest_tree(old_build / vendor) != digest_tree(candidate / vendor)
    for directory in sorted(new_envs.iterdir()):
        requirements = directory / 'requirements.txt'
        if not requirements.is_file():
            continue
        name = directory.name
        environment = directory / '.venv'
        previous = old_envs / name / '.venv'
        reusable = (os.name != 'nt' and previous.is_dir() and
                    (old_envs / name / 'requirements.txt').is_file() and
                    (old_envs / name / 'requirements.txt').read_bytes() == requirements.read_bytes() and
                    not (name == 'kicad_schematic_generate' and vendor_changed))
        if not environment.exists() and not environment.is_symlink():
            if reusable:
                environment.symlink_to(previous.resolve(), target_is_directory=True)
            else:
                print(f'Installing private environment: {name}', flush=True)
                run(str(python), str(candidate / 'tools/setup_tool_environments.py'), '--tool', name,
                    cwd=candidate)
    print('Checking all private tool environments...', flush=True)
    run(str(python), str(product / 'distribution/scripts/verify_tool_envs.py'), '--build', str(candidate))
    profile_name = f'agent_eda-local-{revision[:12]}'
    profile = data / profile_name
    if not profile.exists():
        print('Migrating private profile and settings...', flush=True)
        staged_profile = data / f'.{profile_name}.staging'
        if staged_profile.exists():
            raise RuntimeError(f'Previous profile staging directory exists: {staged_profile}')
        run(str(python), str(product / 'distribution/scripts/migrate_profile.py'),
            '--previous', str(old_home), '--build', str(candidate), '--target', str(staged_profile))
        os.replace(staged_profile, profile)
    print('Checking Agent-EDA startup and tool routing...', flush=True)
    run(str(python), str(product / 'distribution/scripts/verify_runtime.py'),
        '--build', str(candidate), '--home', str(profile), '--product', str(product))
    record = {'build': candidate.name, 'home': profile.name,
              'assembly_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
              'product_revision': revision}
    temporary = data / 'local_install.json.update-tmp'
    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, record_path)
    print(f'Agent-EDA updated to {revision[:12]}. Start `eda` to use the new build.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(update(Path(__file__).resolve().parents[2]))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Agent-EDA update stopped: {error}')

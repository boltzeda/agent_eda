"""Audit an assembled Hermes pin without changing the active Agent-EDA build.

The report is evidence, not an approval. Unrun release gates stay pending and
the command exits nonzero until every required gate has evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from assemble import assemble
from install_profile import install


REQUIRED_GATES = (
    'source_integrity', 'core_overlap_review', 'migration_parity', 'tool_environments',
    'runtime_smoke', 'native_regressions', 'eda_artifacts',
    'profile_isolation', 'gateway_sessions', 'linux_package_rollback',
    'macos_package', 'windows_package', 'reassembly',
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(repository: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(repository), *args], text=True).strip()


def source_integrity(product: Path, reference: Path, build: Path, pin: str) -> dict:
    lock = json.loads((product / 'distribution/upstream.lock.json').read_text())
    manifest_path = build / '.agent-eda-assembly.json'
    manifest = json.loads(manifest_path.read_text())
    expected = lock[pin]
    errors = []
    if manifest['upstream']['commit'] != expected['commit'] or manifest['upstream']['tag'] != expected['tag']:
        errors.append('assembled source does not match the selected locked pin')
    if git(reference, 'rev-parse', f"{expected['tag']}^{{commit}}") != expected['commit']:
        errors.append('reference tag moved from the lock')
    if git(reference, 'remote', 'get-url', 'origin') != lock['repository']:
        errors.append('reference origin differs from the lock')
    if git(reference, 'remote', 'get-url', '--push', 'origin') != 'DISABLED':
        errors.append('reference push URL is enabled')
    if git(reference, 'status', '--porcelain'):
        errors.append('reference checkout is dirty')
    changed = []
    files = manifest.get('source_files_sha256', {})
    for relative, expected_digest in files.items():
        target = build / relative
        if not target.is_file() or target.is_symlink() or sha256(target) != expected_digest:
            changed.append(relative)
    if changed:
        errors.append(f'{len(changed)} assembled source files are missing or changed')
    return {'status': 'failed' if errors else 'passed', 'errors': errors,
            'source_files_checked': len(files), 'changed_examples': changed[:20],
            'assembly_manifest_sha256': sha256(manifest_path), 'upstream': expected}


def overlap_review(product: Path, reference: Path, build: Path, lock: dict, pin: str) -> dict:
    if pin == 'baseline':
        return {'status': 'passed', 'reason': 'no upstream transition for the baseline pin', 'overlaps': []}
    adapters = json.loads((product / 'distribution/overlays/manifest.json').read_text())['core_adapters']
    patches = json.loads((product / 'distribution/patches/manifest.json').read_text())['patches']
    declared = {entry['path'] for entry in (*adapters, *patches)}
    old, new = lock['baseline']['commit'], lock['candidate']['commit']
    changed = set(git(reference, 'diff', '--name-only', old, new).splitlines())
    review_path = product / 'distribution/upgrade_reviews' / (lock['candidate']['tag'] + '.json')
    reviews = json.loads(review_path.read_text()).get('paths', {}) if review_path.is_file() else {}
    overlap = []
    for path in sorted(declared & changed):
        diff = subprocess.check_output(['git', '-C', str(reference), 'diff', old, new, '--', path])
        digest = hashlib.sha256(diff).hexdigest()
        review = reviews.get(path, {})
        source = (build / path).read_text() if (build / path).is_file() else ''
        markers = review.get('required_candidate_markers', [])
        missing_markers = [marker for marker in markers if marker not in source]
        forbidden = [marker for marker in review.get('forbidden_candidate_markers', []) if marker in source]
        accepted = (review.get('upstream_diff_sha256') == digest
                    and bool(review.get('resolution')) and bool(review.get('contract_check'))
                    and bool(markers) and not missing_markers and not forbidden)
        overlap.append({'path': path, 'upstream_diff_sha256': digest,
                        'reviewed': accepted, 'resolution': review.get('resolution', ''),
                        'missing_candidate_markers': missing_markers,
                        'forbidden_candidate_markers_found': forbidden})
    pending = [row['path'] for row in overlap if not row['reviewed']]
    return {'status': 'pending' if pending else 'passed',
            'reason': 'changed native paths require reviewed behavior and contract evidence' if pending else '',
            'pending_paths': pending,
            'declared_native_paths': len(declared), 'upstream_changed_paths': len(changed),
            'overlaps': overlap}


def run_json(command: list[str], *, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(command, text=True, capture_output=True, timeout=180, env=env)
    if result.returncode:
        return {'status': 'failed', 'exit_code': result.returncode,
                'error': (result.stderr or result.stdout)[-2000:]}
    try:
        return {'status': 'passed', 'result': json.loads(result.stdout)}
    except json.JSONDecodeError:
        return {'status': 'failed', 'error': 'check did not return JSON',
                'output_tail': result.stdout[-500:]}


def reassembly(product: Path, reference: Path, build: Path, pin: str) -> dict:
    target = product.parent / 'builds' / ('reassembly-' + pin + '-' + uuid4().hex[:12])
    try:
        assemble(product, reference, target, pin)
        original = json.loads((build / '.agent-eda-assembly.json').read_text())
        rebuilt = json.loads((target / '.agent-eda-assembly.json').read_text())
        keys = ('source_files_sha256', 'core_adapters', 'upstream', 'product')
        differing = [key for key in keys if original[key] != rebuilt[key]]
        source_diff = [path for path in set(original['source_files_sha256']) | set(rebuilt['source_files_sha256'])
                       if original['source_files_sha256'].get(path) != rebuilt['source_files_sha256'].get(path)]
        return {'status': 'failed' if differing else 'passed', 'differing_sections': differing,
                'source_files': len(rebuilt['source_files_sha256']), 'changed_source_examples': sorted(source_diff)[:20]}
    except Exception as exc:
        return {'status': 'failed', 'error': str(exc)}
    finally:
        # This output was created only by this call and was never launched.
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)


def profile_routing(python: Path, home_a: Path, home_b: Path) -> dict:
    code = ('import json; from hermes_constants import get_hermes_home; '
            'from hermes_cli.config import load_config; '
            'print(json.dumps({"home":str(get_hermes_home()),'
            '"provider":load_config()["model"]["provider"]}))')
    rows = []
    for home in (home_a, home_b, home_a):
        result = run_json([str(python), '-c', code], env={**os.environ, 'HERMES_HOME': str(home)})
        if result['status'] != 'passed':
            return result
        rows.append(result['result'])
    expected = [(str(home_a), 'openai-codex'), (str(home_b), 'anthropic'),
                (str(home_a), 'openai-codex')]
    actual = [(row.get('home'), row.get('provider')) for row in rows]
    return {'status': 'passed' if actual == expected else 'failed',
            'actual': actual, 'scope': 'configuration routing only; request and secret isolation remain open'}


def migration_parity(workspace: Path) -> dict:
    ledger_path = workspace / 'evidence/migration-ledger.json'
    if not ledger_path.is_file():
        return {'status': 'pending', 'reason': 'source-to-owner migration ledger is missing'}
    ledger = json.loads(ledger_path.read_text())
    counts = ledger.get('counts', {})
    outstanding = {name: count for name, count in counts.items()
                   if 'needs_' in name and count}
    return {'status': 'pending' if outstanding else 'passed',
            'total_paths': ledger.get('total_paths'), 'counts': counts,
            'outstanding': outstanding, 'ledger_sha256': sha256(ledger_path),
            'reason': 'old Agent-EDA behavior still needs ownership and parity review' if outstanding else ''}


def qualify(product: Path, reference: Path, build: Path, pin: str) -> dict:
    product, reference, build = product.resolve(), reference.resolve(), build.resolve()
    workspace = product.parent
    lock = json.loads((product / 'distribution/upstream.lock.json').read_text())
    revision = git(product, 'rev-parse', 'HEAD')
    dirty = git(product, 'status', '--porcelain').splitlines()
    gates: dict[str, dict] = {name: {'status': 'pending', 'reason': 'release evidence not recorded'}
                              for name in REQUIRED_GATES}
    gates['source_integrity'] = source_integrity(product, reference, build, pin)
    gates['core_overlap_review'] = overlap_review(product, reference, build, lock, pin)
    gates['migration_parity'] = migration_parity(workspace)
    python = build / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if python.is_file():
        gates['tool_environments'] = run_json([
            sys.executable, str(product / 'distribution/scripts/verify_tool_envs.py'),
            '--build', str(build)])
        with TemporaryDirectory(prefix='agent-eda-qualification-', dir=workspace / 'data') as temp:
            profile = Path(temp) / 'profile'
            install(build, profile, 'openai-codex', 'gpt-6-luna')
            profile_b = Path(temp) / 'profile-b'
            install(build, profile_b, 'anthropic', 'test-model')
            gates['runtime_smoke'] = run_json([
                str(python), str(product / 'distribution/scripts/verify_runtime.py'),
                '--build', str(build), '--home', str(profile), '--product', str(product)],
                env={**os.environ, 'HERMES_HOME': str(profile)})
            routing = profile_routing(python, profile, profile_b)
            gates['profile_isolation'] = {'status': 'pending' if routing['status'] == 'passed' else 'failed',
                                          'configuration_a_b_a': routing,
                                          'reason': 'real request, secret and session A/B/A checks remain'}
            fixture = workspace / 'evidence' / ('schematic-' + pin + '-'
                        + gates['source_integrity']['assembly_manifest_sha256'][:12])
            fixture.mkdir(parents=True, exist_ok=True)
            script = fixture / 'generate.py'
            shutil.copy2(product / 'distribution/fixtures/minimal_schematic.py', script)
            schematic = run_json([
                str(python), str(product / 'distribution/scripts/verify_schematic.py'),
                '--script', str(script)], env={**os.environ, 'HERMES_HOME': str(profile)})
            component = run_json([
                str(python), str(product / 'distribution/scripts/verify_component.py')],
                env={**os.environ, 'HERMES_HOME': str(profile)})
            preliminary = schematic['status'] == component['status'] == 'passed'
            gates['eda_artifacts'] = {'status': 'pending' if preliminary else 'failed',
                                      'minimal_native_schematic': schematic,
                                      'installed_component_lookup': component,
                                      'reason': 'full schematic, simulation, placement and PCB checks remain'}
    else:
        for name in ('tool_environments', 'runtime_smoke'):
            gates[name] = {'status': 'failed', 'error': 'build Python environment is missing'}
    gates['reassembly'] = reassembly(product, reference, build, pin)
    native_log = workspace / 'evidence' / ('native-focused-' + lock[pin]['tag'] + '.log')
    if native_log.is_file():
        gates['native_regressions'] = {
            'status': 'pending', 'focused_log': str(native_log),
            'focused_log_sha256': sha256(native_log),
            'reason': 'focused tests are not the complete native suite; one Hermes-literal branding expectation needs a product-specific resolution'}
    report = {'schema': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'product_revision': revision, 'product_dirty_paths': dirty,
              'pin': pin, 'build': str(build), 'platform': sys.platform,
              'gates': gates, 'qualified': all(gate['status'] == 'passed' for gate in gates.values())}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--pin', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = qualify(args.product, args.reference, args.build, args.pin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'qualified': report['qualified'], 'report': str(args.output),
                      'gates': {name: gate['status'] for name, gate in report['gates'].items()}}, indent=2))
    raise SystemExit(0 if report['qualified'] else 2)

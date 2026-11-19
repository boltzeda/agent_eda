"""Promote only a fully qualified, pinned Agent-EDA candidate build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile

from qualify import REQUIRED_GATES


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(product: Path, report_path: Path) -> dict:
    product, report_path = product.resolve(), report_path.resolve()
    workspace = product.parent
    if not report_path.is_file() or not report_path.is_relative_to(workspace / 'evidence'):
        raise ValueError('qualification report must be a file in this workspace/evidence directory')
    report = json.loads(report_path.read_text())
    build = Path(report['build']).resolve()
    if build.parent != workspace / 'builds' or build.is_symlink():
        raise ValueError('qualification report names a build outside this workspace')
    if report.get('pin') != 'candidate':
        raise ValueError('only the reviewed candidate pin may be promoted')
    lock = json.loads((product / 'distribution/upstream.lock.json').read_text())
    manifest = json.loads((build / '.agent-eda-assembly.json').read_text())
    if manifest['upstream'] != {'repository': lock['repository'], **lock['candidate']}:
        raise ValueError('candidate build no longer matches the locked upstream source')
    if digest(build / '.agent-eda-assembly.json') != report['gates']['source_integrity'].get('assembly_manifest_sha256'):
        raise ValueError('candidate assembly manifest differs from the qualified artifact')
    revision = subprocess.check_output(['git', '-C', str(product), 'rev-parse', 'HEAD'], text=True).strip()
    if report.get('product_revision') != revision or report.get('product_dirty_paths'):
        raise ValueError('qualification was not run against the current clean product revision')
    if subprocess.check_output(['git', '-C', str(product), 'status', '--porcelain'], text=True).strip():
        raise ValueError('product source is dirty; record and rerun qualification first')
    gates = report.get('gates', {})
    missing = [name for name in REQUIRED_GATES if gates.get(name, {}).get('status') != 'passed']
    if missing or report.get('qualified') is not True:
        raise ValueError('candidate remains unqualified: ' + ', '.join(missing or ['report verdict']))
    return {'build': build.name, 'report': str(report_path), 'report_sha256': digest(report_path),
            'assembly_manifest_sha256': digest(build / '.agent-eda-assembly.json'),
            'upstream_commit': lock['candidate']['commit'], 'product_revision': revision}


def promote(product: Path, report_path: Path) -> Path:
    product = product.resolve()
    record = validate(product, report_path)
    active = product.parent / 'data/active_build.json'
    previous = json.loads(active.read_text()) if active.is_file() else None
    record['previous'] = ({key: previous[key] for key in
                           ('build', 'report', 'report_sha256', 'assembly_manifest_sha256')}
                          if previous else None)
    active.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', dir=active.parent, prefix='.active-build-', delete=False) as stream:
        temp = Path(stream.name)
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, active)
    return active


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='validate only; do not switch the active build')
    args = parser.parse_args()
    try:
        record = validate(args.product, args.report)
        print(json.dumps(record, indent=2, sort_keys=True))
        if not args.check:
            print('Active build pointer:', promote(args.product, args.report))
    except (OSError, KeyError, ValueError) as exc:
        parser.exit(2, f'Promotion refused: {exc}\n')

"""Create a public, history-free Agent-EDA Git repository from committed source."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tarfile
import tempfile

EXCLUDED = (
    'distribution/preservation',
    'electronics/skills/.hub',
    'electronics/skills/.curator_ledger.jsonl',
)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def export(product: Path, output: Path) -> dict:
    product, output = product.resolve(), output.absolute()
    if output.exists():
        raise ValueError(f'public export destination already exists: {output}')
    if git(product, 'status', '--porcelain'):
        raise ValueError('commit product source before exporting a public snapshot')
    revision = git(product, 'rev-parse', 'HEAD')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as stream:
        subprocess.run(['git', '-C', str(product), 'archive', '--format=tar', 'HEAD'],
                       stdout=stream, check=True)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            archive.extractall(output, filter='data')
    for relative in EXCLUDED:
        if (output / relative).exists():
            raise ValueError(f'private path entered the public snapshot: {relative}')
    if not (output / 'LICENSE').is_file() or not (output / 'setup.py').is_file():
        raise ValueError('public snapshot lacks license or setup entrypoint')
    subprocess.run(['git', 'init', '-b', 'main', str(output)], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(['git', '-C', str(output), 'add', '.'], check=True)
    author_name = git(product, 'log', '-1', '--format=%an')
    author_email = git(product, 'log', '-1', '--format=%ae')
    subprocess.run(['git', '-C', str(output), '-c', f'user.name={author_name}',
                    '-c', f'user.email={author_email}', 'commit', '-m',
                    f'Publish Agent-EDA source snapshot from {revision[:12]}'],
                   check=True, stdout=subprocess.DEVNULL)
    return {'source_revision': revision, 'public_revision': git(output, 'rev-parse', 'HEAD'),
            'output': str(output), 'files': len(git(output, 'ls-files').splitlines())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(export(args.product, args.output))

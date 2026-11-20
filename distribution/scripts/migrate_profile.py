"""Stage a new private profile while retaining the user's local settings and history."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import stat

from ruamel.yaml import YAML


def migrate(previous: Path, build: Path, target: Path) -> None:
    if previous.is_symlink() or not (previous / 'config.yaml').is_file():
        raise ValueError('installed profile is missing or unsafe')
    if target.exists() or target.is_symlink():
        raise ValueError('candidate profile already exists')
    def ignore_special(directory: str, names: list[str]) -> list[str]:
        return [name for name in names if not (
            stat.S_ISREG((Path(directory) / name).lstat().st_mode) or
            stat.S_ISDIR((Path(directory) / name).lstat().st_mode) or
            stat.S_ISLNK((Path(directory) / name).lstat().st_mode))]
    shutil.copytree(previous, target, symlinks=True, ignore=ignore_special)
    payload = build / 'agent_eda'
    if not (build / '.agent-eda-assembly.json').is_file() or not payload.is_dir():
        raise ValueError('candidate build has no Agent-EDA payload')
    for skill in (payload / 'skills').iterdir():
        destination = target / 'skills' / skill.name
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        if skill.is_dir():
            shutil.copytree(skill, destination)
        else:
            shutil.copy2(skill, destination)
    plugins = payload / 'tools' / 'agent_eda_plugins'
    names = sorted(path.name for path in plugins.iterdir() if (path / 'plugin.yaml').is_file())
    for name in names:
        destination = target / 'plugins' / name
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        shutil.copytree(plugins / name, destination)
    shutil.copy2(build / 'agent_eda_branding' / 'SOUL.md', target / 'SOUL.md')
    yaml = YAML()
    config = yaml.load((target / 'config.yaml').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('profile config is not a YAML mapping')
    def add(section: dict, key: str, values: list[str]) -> None:
        existing = section.setdefault(key, [])
        if not isinstance(existing, list):
            raise ValueError(f'profile config {key} must be a list')
        for value in values:
            if value not in existing:
                existing.append(value)
    add(config.setdefault('plugins', {}), 'enabled', names)
    toolsets = config.setdefault('platform_toolsets', {}).setdefault('cli', [])
    if not isinstance(toolsets, list):
        raise ValueError('profile config platform_toolsets.cli must be a list')
    for name in ['hermes-cli', *[name.replace('-', '_') for name in names]]:
        if name not in toolsets:
            toolsets.append(name)
    add(config.setdefault('skills', {}), 'auto_load', ['eda-design-workflow'])
    with (target / 'config.yaml').open('w', encoding='utf-8') as stream:
        yaml.dump(config, stream)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    migrate(args.previous, args.build, args.target)

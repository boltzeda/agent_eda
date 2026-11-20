"""Install the assembled EDA plugin/skill payload into an unused isolated profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def install(build: Path, home: Path, provider: str | None = None, model: str | None = None) -> dict:
    build, home = build.resolve(), home.absolute()
    if home.is_symlink():
        raise ValueError('profile home cannot be a symlink')
    if home.exists() and any(home.iterdir()):
        raise ValueError('choose a new empty profile home; this installer never rewrites live state')
    source = build / 'agent_eda'
    if not (build / '.agent-eda-assembly.json').is_file() or not source.is_dir():
        raise ValueError('build is missing an assembly manifest or EDA payload')
    if bool(provider) != bool(model):
        raise ValueError('provide both provider and model, or select them later with eda model')
    home.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / 'skills', home / 'skills')
    plugins = source / 'tools' / 'agent_eda_plugins'
    names = sorted(p.name for p in plugins.iterdir() if (p / 'plugin.yaml').is_file())
    for name in names:
        shutil.copytree(plugins / name, home / 'plugins' / name)
    shutil.copy2(build / 'agent_eda_branding' / 'SOUL.md', home / 'SOUL.md')
    toolsets = [n.replace('-', '_') for n in names]
    model_config = (f'model:\n  provider: {json.dumps(provider)}\n  default: {json.dumps(model)}\n'
                    if provider else '')
    config = ('plugins:\n  enabled:\n' + ''.join(f'    - {n}\n' for n in names)
              + model_config
              + 'platform_toolsets:\n  cli:\n    - hermes-cli\n'
              + ''.join(f'    - {n}\n' for n in toolsets)
              + 'skills:\n  auto_load:\n    - eda-design-workflow\n')
    (home / 'config.yaml').write_text(config, encoding='utf-8')
    return {'profile_home': str(home), 'plugins': names, 'skills': len(list((home / 'skills').rglob('SKILL.md')))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--provider', help='optional provider; otherwise run eda model after setup')
    parser.add_argument('--model', help='model used with --provider')
    args = parser.parse_args()
    print(json.dumps(install(args.build, args.home, args.provider, args.model), indent=2))

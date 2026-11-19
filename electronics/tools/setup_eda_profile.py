"""Install BoltzPCB's persona and design guidance into the active profile."""
from pathlib import Path
import shutil

from hermes_constants import get_hermes_home
from hermes_cli.config import atomic_config_write, read_user_config_raw
from tools.install_tool_plugins import refresh_running_plugins

ROOT = Path(__file__).resolve().parents[1]
PREFERRED = ('intent-understanding', 'block-expansion', 'hardware-specialists',
             'component-validation', 'datasheet-to-component', 'kicad-library-finder',
             'kicad-library-generator', 'kicad-schematic-generation', 'circuit-simulation',
             'model-acquisition', 'pcb-workflow')
OLD_GUIDANCE = '''## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
protocol and record this stage's reviewed evidence through `eda_workflow`.
Use this BoltzPCB skill and its installed native tools before generic/imported
alternatives. Respect explicit user skips, record them as unverified, and do
not infer skips from tool failures. Do not bypass generation prerequisites with
terminal commands. Standalone library/model tasks do not require a full PCB flow.
The shared protocol defines scope and sequencing; this skill supplies the
stage-specific engineering procedure. Do not silently weaken the shared
protocol during automatic self-improvement.

'''
GUIDANCE = '''## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

'''
FALLBACKS = {
    'schematic_gen': 'Explicit alternative; prefer kicad-schematic-generation.',
    'kicad-gen': 'Explicit alternative; prefer kicad-library-generator.',
    'layout_generation': 'Explicit alternative; prefer pcb-workflow.',
    'freerouting-autoroute': 'Explicit alternative; prefer pcb-workflow routing.',
}


FALLBACK_GUIDANCE = """## BoltzPCB workflow integration

This is an alternative implementation. Prefer the task-specific BoltzPCB skills
listed in `eda-design-workflow`. Use this alternative only for an explicit user
choice or a demonstrated capability gap, explain the switch, and retain the
project evidence, checks and recorded user skips. Never use it to bypass a
failed prerequisite or silently replace the preferred pipeline.

"""


def integrate_skill(path, preferred=True):
    text = path.read_text()
    if '## BoltzPCB workflow integration' in text:
        text = text.replace(OLD_GUIDANCE, GUIDANCE if preferred else FALLBACK_GUIDANCE)
        if not preferred:
            text = text.replace(GUIDANCE, FALLBACK_GUIDANCE)
        path.write_text(text)
        return
    # Insert after the frontmatter, before any potentially conflicting old procedure.
    end = text.index('\n---', 3) + 4 if text.startswith('---\n') else 0
    path.write_text(text[:end] + '\n\n' + (GUIDANCE if preferred else FALLBACK_GUIDANCE) + text[end:].lstrip())


def install():
    home = get_hermes_home()
    backup = home / 'backups' / 'boltzpcb-protocol-v2'
    backup.mkdir(parents=True, exist_ok=True)
    for name in ('SOUL.md', 'config.yaml'):
        if (home / name).exists() and not (backup / name).exists():
            shutil.copy2(home / name, backup / name)
    branding = ROOT.parent / 'agent_eda_branding' / 'SOUL.md'
    if not branding.is_file():
        branding = ROOT.parent / 'distribution' / 'branding' / 'SOUL.md'
    shutil.copy2(branding, home / 'SOUL.md')
    shutil.copytree(ROOT / 'skills/eda-design-workflow', home / 'skills/eda-design-workflow', dirs_exist_ok=True)
    for name in PREFERRED:
        path = home / 'skills' / name / 'SKILL.md'
        source = ROOT / 'skills' / name / 'SKILL.md'
        if path.exists():
            saved = backup / 'skills' / name / 'SKILL.md'
            if not saved.exists():
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
        if source.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, path)
    for name, description in FALLBACKS.items():
        path = home / 'skills/claude-code-imports' / name / 'SKILL.md'
        if path.exists():
            saved = backup / 'fallbacks' / name / 'SKILL.md'
            if not saved.exists():
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
            lines = path.read_text().splitlines()
            index = next(i for i, line in enumerate(lines) if line.startswith('description:'))
            lines[index] = 'description: ' + description
            path.write_text('\n'.join(lines) + '\n')
            integrate_skill(path, preferred=False)
    cfg_path = home / 'config.yaml'
    cfg = read_user_config_raw(cfg_path)
    skills = cfg.setdefault('skills', {})
    skills['auto_load'] = list(dict.fromkeys([*skills.get('auto_load', []), 'eda-design-workflow']))
    skills['disabled'] = [n for n in skills.get('disabled', []) if n != 'eda-design-workflow']
    plugins = cfg.setdefault('plugins', {})
    plugins['enabled'] = [n for n in plugins.get('enabled', []) if n != 'eda_workflow']
    plugins['disabled'] = list(dict.fromkeys([*plugins.get('disabled', []), 'eda_workflow']))
    platforms = cfg.setdefault('platform_toolsets', {})
    enabled = platforms.setdefault('cli', ['hermes-cli'])
    platforms['cli'] = [n for n in enabled if n != 'eda_workflow']
    atomic_config_write(cfg_path, cfg)
    print(f'Installed BoltzPCB persona and auto-loaded EDA workflow in {home}')
    refresh_running_plugins()


if __name__ == '__main__':
    install()

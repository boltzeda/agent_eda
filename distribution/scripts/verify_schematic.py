"""Exercise the Agent-EDA schematic tool through its registered plugin and KiCad."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def decode(value):
    for _ in range(3):
        if not isinstance(value, str):
            break
        value = json.loads(value)
    return value


def verify(script: Path) -> dict:
    from hermes_cli.plugins import get_plugin_manager
    from tools.registry import registry

    get_plugin_manager().discover_and_load()
    response = decode(registry.dispatch('kicad_schematic_generate',
                        {'script_path': str(script.resolve())}, session_id='qualification-schematic'))
    if not isinstance(response, dict) or response.get('success') is not True:
        raise RuntimeError(f'schematic generation failed: {str(response)[-1200:]}')
    schematics = sorted(script.parent.glob('*.kicad_sch'))
    if len(schematics) != 1 or not list(script.parent.glob('*.kicad_pro')):
        raise RuntimeError('generator did not produce one native KiCad schematic and project')
    netlist = script.parent / 'verified.net'
    run = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', str(schematics[0]),
                          '-o', str(netlist)], capture_output=True, text=True, timeout=60)
    if run.returncode or not netlist.is_file():
        raise RuntimeError('KiCad could not parse/export the generated schematic: ' + run.stderr[-1000:])
    if '(ref "R1")' not in netlist.read_text():
        raise RuntimeError('KiCad netlist did not retain fixture component R1')
    return {'success': True, 'schematic': str(schematics[0]), 'netlist': str(netlist),
            'component': 'R1', 'generator_tool': 'kicad_schematic_generate'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.script), indent=2))
    except Exception as exc:
        print(json.dumps({'success': False, 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)

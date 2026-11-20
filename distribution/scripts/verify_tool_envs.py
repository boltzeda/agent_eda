"""Check all twelve private tool interpreters and their key imports."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

MODULES = {
    'component_datasheet_prepare': ('pypdf', 'jsonschema', 'rpds'),
    'component_datasheet_parse': ('pypdf', 'jsonschema', 'rpds', 'mineru'),
    'component_schema_validate': ('jsonschema', 'rpds'),
    'kicad_library_find': (),
    'kicad_library_generate': ('sexpdata',),
    'kicad_schematic_setup': (),
    'kicad_schematic_generate': ('circuit_synth',),
    'pcb_workflow': ('torch', 'numpy', 'networkx', 'deap', 'shapely'),
    'acquire_adi_model': (),
    'acquire_nxp_model': (),
    'acquire_st_model': (),
    'acquire_ti_model': (),
}


def verify(build: Path) -> dict:
    root = build.resolve() / 'tools/tool_environments'
    executable = Path('Scripts/python.exe') if os.name == 'nt' else Path('bin/python')
    results = {}
    for name, modules in MODULES.items():
        python = root / name / '.venv' / executable
        if not python.is_file():
            results[name] = {'passed': False, 'error': 'private interpreter missing'}
            continue
        imports = ['import sys', *[f'import {module}' for module in modules]]
        if name == 'pcb_workflow':
            imports.append('from pcb_workflow.kicad_environment import import_pcbnew')
            imports.append('import_pcbnew()')
        code = '; '.join([*imports, 'print(sys.version.split()[0])'])
        try:
            run = subprocess.run([str(python), '-c', code], text=True,
                                 capture_output=True, timeout=45, check=False,
                                 cwd=build.resolve() / 'tools' if name == 'pcb_workflow' else None)
            results[name] = {'passed': run.returncode == 0,
                             'python': run.stdout.strip(),
                             'error': run.stderr[-700:] if run.returncode else ''}
        except (OSError, subprocess.TimeoutExpired) as error:
            results[name] = {'passed': False, 'error': str(error)}
    return {'build': str(build.resolve()), 'passed': sum(x['passed'] for x in results.values()),
            'total': len(MODULES), 'tools': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    opts = parser.parse_args()
    outcome = verify(opts.build)
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome['passed'] == outcome['total'] else 1)

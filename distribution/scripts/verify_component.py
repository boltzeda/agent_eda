"""Verify the installed KiCad finder through Agent-EDA's registered tool."""

from __future__ import annotations

import json
import sys


def verify() -> dict:
    from hermes_cli.plugins import get_plugin_manager
    from tools.registry import registry

    get_plugin_manager().discover_and_load()
    result = registry.dispatch('kicad_library_find',
                               {'symbol_name': 'NE555P', 'package_name': 'DIP-8'},
                               session_id='qualification-component')
    for _ in range(3):
        if isinstance(result, str):
            result = json.loads(result)
    if not isinstance(result, dict) or not result.get('success'):
        raise RuntimeError('KiCad library finder did not complete')
    symbols = result.get('symbol_matches') or []
    footprints = result.get('footprint_matches') or []
    if not any(row.get('symbol_name') == 'NE555P' and row.get('library_nickname') == 'Timer'
               for row in symbols):
        raise RuntimeError('installed Timer:NE555P symbol was not found')
    if not any(row.get('footprint_name') == 'DIP-8-16_W7.62mm'
               for row in footprints):
        raise RuntimeError('installed DIP-8 footprint candidate was not found')
    return {'success': True, 'symbol': 'Timer:NE555P',
            'candidate_footprint': 'Package_DIP:DIP-8-16_W7.62mm'}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(), indent=2))
    except Exception as exc:
        print(json.dumps({'success': False, 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)

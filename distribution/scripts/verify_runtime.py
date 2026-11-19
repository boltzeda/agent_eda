"""Smoke-check the assembled baseline against its own Python and profile."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch


def verify(build: Path, home: Path, product: Path) -> dict:
    build, home, product = build.resolve(), home.resolve(), product.resolve()
    os.environ['HERMES_HOME'] = str(home)
    sys.path.insert(0, str(build))
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.config import read_user_config_raw
    from hermes_cli.tools_config import _get_platform_tools
    from tools.registry import registry
    from model_tools import get_tool_definitions
    from hermes_cli.auth_nous import _nous_device_code_login, _login_nous
    from hermes_cli.banner import _banner_left_lines
    from hermes_cli.cli_status_bar_mixin import CLIStatusBarMixin
    from hermes_cli.cli_commands_mixin import CLICommandsMixin
    from hermes_cli.main import cmd_update, _pick_provider
    from hermes_cli.models import _PROVIDER_LABELS
    from hermes_cli.inventory import load_picker_context
    from hermes_cli.cli_model_switch_mixin import _show_model_picker, CLIModelSwitchMixin
    from agent.turn_failure_copy import oauth_relogin_command
    from agent_eda.provider_setup import _configure, start_provider_setup

    manifest = json.loads((build / '.agent-eda-assembly.json').read_text())
    if manifest['upstream']['tag'] not in ('v0.23.1', 'v0.23.2'):
        raise AssertionError('smoke test expects the locked baseline or candidate')
    for function, args in ((_nous_device_code_login, ()), (_login_nous, (None, None))):
        try:
            function(*args)
        except SystemExit as error:
            if 'disabled in Agent-EDA' not in str(error):
                raise AssertionError('Nous login refused for an unexpected reason') from error
        else:
            raise AssertionError('Nous login entrypoint is still active')
    publisher_line = _banner_left_lines('test-model', str(product), None, None, 'openai-codex',
                                        accent='#E879F9', dim='#C084FC')[0]
    if 'BoltzPCB' not in publisher_line:
        raise AssertionError('CLI banner does not identify BoltzPCB')
    if oauth_relogin_command('openai-codex') != 'eda auth add openai-codex --type oauth':
        raise AssertionError('Codex sign-in guidance does not use the Agent-EDA launcher')
    class PickerProbe:
        model = 'gpt-6-luna'
        provider = 'openai-codex'
        rows = []
        closed = False

        def _open_model_picker(self, providers, *args, **kwargs):
            self.rows = providers

        def _close_model_picker(self):
            self.closed = True

        def _invalidate(self, **kwargs):
            raise AssertionError('unauthenticated provider opened an empty model list')

    picker = PickerProbe()
    _show_model_picker(picker, load_picker_context(), False)
    if len(picker.rows) < 10 or not any(row['slug'] == 'openai-codex' for row in picker.rows):
        raise AssertionError('/model does not show setup choices including OpenAI Codex')
    if any(row['slug'] == 'nous' for row in picker.rows):
        raise AssertionError('/model still offers Nous Portal')
    with patch('hermes_cli.main._prompt_provider_choice', return_value=None) as choose_provider:
        _pick_provider({}, None, dict(_PROVIDER_LABELS), {})
    if any('Nous Portal' in label for label in choose_provider.call_args.args[0]):
        raise AssertionError('eda model still offers Nous Portal')
    codex_index = next(i for i, row in enumerate(picker.rows) if row['slug'] == 'openai-codex')
    if picker.rows[codex_index].get('authenticated') is False:
        picker._model_picker_state = {'stage': 'provider', 'providers': picker.rows, 'selected': codex_index}
        with patch('agent_eda.provider_setup.start_provider_setup') as launch_setup:
            CLIModelSwitchMixin._handle_model_picker_selection(picker)
        launch_setup.assert_called_once_with(picker, 'openai-codex')
        class FakeApp:
            task = None

            def create_background_task(self, task):
                self.task = task

        picker._app = FakeApp()
        with patch('prompt_toolkit.application.run_in_terminal', return_value='setup-task'):
            start_provider_setup(picker, 'openai-codex')
        if not picker.closed or picker._app.task != 'setup-task':
            raise AssertionError('in-chat provider setup did not suspend and schedule the terminal wizard')
        configured = SimpleNamespace(requested_provider='anthropic', model='old-model',
                                     _active_agent_route_signature='old-route')
        saved = {'model': {'provider': 'openai-codex', 'default': 'new-model'}}
        with patch('hermes_cli.config.load_config', side_effect=[{'model': {}}, saved]), \
                patch('hermes_cli.main._PROVIDER_MODEL_FLOWS', {'openai-codex': lambda *args: None}), \
                patch('hermes_cli.cli_agent_setup_mixin._retire_agent') as retire, \
                redirect_stdout(StringIO()):
            _configure(configured, 'openai-codex')
        retire.assert_called_once_with(configured)
        if (configured.requested_provider, configured.model,
                configured._active_agent_route_signature) != ('openai-codex', 'new-model', None):
            raise AssertionError('completed provider setup did not refresh the chat session')
    unknown_bar = CLIStatusBarMixin._build_context_bar(None, None)
    half_bar = CLIStatusBarMixin._build_context_bar(None, 50)
    if '?' not in unknown_bar or '●' not in half_bar or '○' not in half_bar:
        raise AssertionError('context meter display logic did not load')
    update_probe = SimpleNamespace(_pending_relaunch=None)
    if CLICommandsMixin._handle_update_command(update_probe) is not True or update_probe._pending_relaunch != ['update']:
        raise AssertionError('/update did not request the Agent-EDA updater')
    from hermes_cli.relaunch import relaunch
    with patch.dict(os.environ, AGENT_EDA_PRODUCT=str(product)), patch('hermes_cli.relaunch.os.execv') as execv:
        relaunch(['update'], preserve_inherited=False)
    if execv.call_args.args[1][-2:] != [str(product / 'bin/eda'), 'update']:
        raise AssertionError('/update relaunch did not target the Agent-EDA launcher')
    try:
        cmd_update(None)
    except SystemExit as error:
        if 'qualified pinned build' not in str(error):
            raise AssertionError('native updater refused for an unexpected reason') from error
    else:
        raise AssertionError('native updater is still active')
    get_plugin_manager().discover_and_load()
    required = ('component_datasheet_prepare', 'component_datasheet_parse',
                'component_schema_validate', 'kicad_library_find', 'kicad_library_generate',
                'kicad_schematic_setup', 'kicad_schematic_generate', 'pcb_workflow',
                'acquire_adi_model', 'acquire_nxp_model', 'acquire_st_model', 'acquire_ti_model')
    missing = [name for name in required if registry.get_entry(name) is None]
    if missing:
        raise AssertionError(f'EDA plugin tools missing from registry: {missing}')
    enabled = sorted(_get_platform_tools(read_user_config_raw(), 'cli'))
    definitions = get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True,
                                       skip_tool_search_assembly=True)
    listed = {item.get('function', {}).get('name', item.get('name')) for item in definitions}
    missing = [name for name in required if name not in listed]
    if missing:
        raise AssertionError(f'EDA plugin tools missing from CLI: {missing}')
    if registry.get_entry('eda_workflow') is not None or 'eda_workflow' in listed:
        raise AssertionError('obsolete EDA workflow tool is still exposed')
    config = read_user_config_raw()
    if 'eda-design-workflow' not in config.get('skills', {}).get('auto_load', []):
        raise AssertionError('EDA design instruction is not auto-loaded')
    schematic = json.loads(registry.dispatch(
        'kicad_schematic_generate', {'script_path': str(product / 'missing-generate.py')},
        session_id='no-workflow-project'))
    if 'script_path must point to an existing' not in schematic.get('error', ''):
        raise AssertionError(f'schematic tool did not receive direct call: {schematic}')
    placement = json.loads(registry.dispatch(
        'pcb_workflow', {'stage': 'place', 'arguments': []},
        session_id='no-workflow-project'))
    if placement.get('stage') != 'place' or 'usage:' not in placement.get('stderr', ''):
        raise AssertionError(f'PCB placement tool did not receive direct call: {placement}')
    routing = json.loads(registry.dispatch(
        'pcb_workflow', {'stage': 'route', 'arguments': []},
        session_id='no-workflow-project'))
    if 'explicitly approved routing' not in routing.get('error', ''):
        raise AssertionError('PCB tool lost its own routing approval check')
    child_env = dict(os.environ, AGENT_EDA_BUILD=str(build), AGENT_EDA_HOME=str(home))
    result = subprocess.run([sys.executable, str(product / 'bin/eda'), '--help'], text=True,
                            capture_output=True, timeout=30, check=True, env=child_env)
    if 'Agent-EDA - AI assistant' not in result.stdout or 'usage: eda ' not in result.stdout:
        raise AssertionError('CLI branding or parser missing')
    return {'baseline': manifest['upstream'], 'registered_eda_tools': list(required),
            'cli_tool_count': len(definitions), 'workflow_instruction': 'auto-loaded',
            'direct_generation_dispatch': 'passed',
            'cli_branding': 'passed', 'context_meter': 'passed', 'model_picker': 'passed',
            'nous_login_disabled': True, 'native_update_disabled': True,
            'profile': str(home)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--product', type=Path, required=True)
    opts = parser.parse_args()
    print(json.dumps(verify(opts.build, opts.home, opts.product), indent=2))

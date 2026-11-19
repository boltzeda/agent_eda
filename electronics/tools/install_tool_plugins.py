"""Install this repository's Agent-EDA tool adapters into the active user home."""

from __future__ import annotations

import shutil
from pathlib import Path

from hermes_constants import get_hermes_home

SOURCE_ROOT = Path(__file__).resolve().parent / "agent_eda_plugins"
PLUGIN_NAMES = (
    "eda_component_workflows",
    "kicad_generation",
    "pcb-workflow",
    "adi_model_acquirer",
    "nxp_model_acquirer",
    "st_model_acquirer",
    "ti_model_acquirer",
)
PLUGIN_FILES = {
    "eda_component_workflows": ("__init__.py", "plugin.yaml", "tools.py"),
    "kicad_generation": ("__init__.py", "plugin.yaml", "tools.py", "runtime.py"),
    "pcb-workflow": ("__init__.py", "plugin.yaml", "agent_eda_bridge.py"),
    "adi_model_acquirer": ("__init__.py", "plugin.yaml", "tools.py"),
    "nxp_model_acquirer": ("__init__.py", "plugin.yaml", "tools.py"),
    "st_model_acquirer": ("__init__.py", "plugin.yaml", "tools.py"),
    "ti_model_acquirer": ("__init__.py", "plugin.yaml", "tools.py"),
}


def refresh_running_plugins(required=()):
    """Publish changed files to the owning gateway and check its load receipt.

    A local import proves nothing about a long-lived gateway's registry. Use the
    same activation path as plugin enable/update, without restarting busy chats.
    Existing conversations retain their prompt; new ones get the new surface.
    """
    from hermes_cli.plugins_activation import find_activation, reload_running_gateway

    answer = reload_running_gateway()
    if answer is None:
        print('Files saved; no gateway acknowledged activation. Start eda, or reload '
              'the serving gateway before opening a new design session.')
        return False
    if not answer.get('reloaded'):
        raise RuntimeError(f"Gateway plugin reload refused: {answer.get('error', 'no reason supplied')}")
    for name in required:
        activation = find_activation(answer.get('activations'), name)
        if activation is None or activation.get('load_error') or activation.get('missing_dependencies'):
            raise RuntimeError(f'Gateway did not activate {name}: {activation or "missing from load receipt"}')
    print('Running gateway reloaded plugins. Open a new design session for updated tools and instructions.')
    return True


def install() -> Path:
    destination_root = get_hermes_home() / "plugins"
    for plugin in PLUGIN_NAMES:
        source = SOURCE_ROOT / plugin
        destination = destination_root / plugin
        destination.mkdir(parents=True, exist_ok=True)
        for filename in PLUGIN_FILES[plugin]:
            shutil.copy2(source / filename, destination / filename)
        if plugin == "kicad_generation":
            vendor_source = source / "vendor"
            if vendor_source.is_dir():
                shutil.copytree(
                    vendor_source,
                    destination / "vendor",
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
        print(f"Installed {plugin} -> {destination}")
    refresh_running_plugins()
    return destination_root


if __name__ == "__main__":
    install()

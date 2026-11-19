"""Run the upstream provider wizard from Agent-EDA's in-chat model picker."""

from __future__ import annotations


def _configure(cli, slug: str) -> None:
    from hermes_cli import main
    from hermes_cli.config import load_config
    from hermes_cli.cli_agent_setup_mixin import _retire_agent
    from hermes_cli.model_setup_flows import (
        _is_profile_plugin_flow_provider,
        _model_flow_plugin_provider,
        _profile_model_setup_flow,
    )

    config = load_config()
    model_cfg = config.get('model') or {}
    current_model = model_cfg.get('default', '') if isinstance(model_cfg, dict) else str(model_cfg)
    flow = main._PROVIDER_MODEL_FLOWS.get(slug) or _profile_model_setup_flow(slug)
    try:
        if flow is not None:
            flow(config, current_model, None)
        elif _is_profile_plugin_flow_provider(slug):
            _model_flow_plugin_provider(config, slug, current_model)
        elif slug in main._GENERIC_API_KEY_PROVIDERS or main._is_profile_api_key_provider(slug):
            main._model_flow_api_key_provider(config, slug, current_model)
        else:
            print(f'Provider {slug} has no interactive setup flow.')
            return
    except (KeyboardInterrupt, EOFError, SystemExit):
        print('\nProvider setup cancelled.')
        return
    except Exception as exc:
        print(f'Provider setup failed: {exc}')
        return

    model_cfg = load_config().get('model') or {}
    if not isinstance(model_cfg, dict) or model_cfg.get('provider') != slug:
        print('Provider setup was not completed.')
        return
    model = model_cfg.get('default') or model_cfg.get('model') or ''
    cli.requested_provider = slug
    cli.model = model or cli.model
    _retire_agent(cli)
    cli._active_agent_route_signature = None
    print(f'Agent-EDA is ready with {slug}' + (f' · {model}' if model else '') + '.')


def start_provider_setup(cli, slug: str) -> None:
    """Suspend the active TUI so the selected provider can prompt and authenticate."""
    from prompt_toolkit.application import run_in_terminal

    app = getattr(cli, '_app', None)
    if app is None:
        raise RuntimeError('provider setup requires an active Agent-EDA terminal')
    cli._close_model_picker()
    task = run_in_terminal(lambda: _configure(cli, slug))
    app.create_background_task(task)

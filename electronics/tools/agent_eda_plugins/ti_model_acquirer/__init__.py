"""Hermes plugin for publicly downloadable TI simulation models."""

from tools.mode_acquisation.ti_model_acquirer.service import TIModelAcquirer

__all__ = ["TIModelAcquirer"]


def register(ctx) -> None:
    """Register the TI-only acquisition tool without altering the generic resolver."""
    # Keep the standalone package importable and testable without Hermes on
    # PYTHONPATH; only plugin discovery needs the Hermes tool registry.
    from .tools import ACQUIRE_TI_MODEL_SCHEMA, _handle_acquire_ti_model

    ctx.register_tool(
        name="acquire_ti_model",
        toolset="ti_model_acquirer",
        schema=ACQUIRE_TI_MODEL_SCHEMA,
        handler=_handle_acquire_ti_model,
        emoji="🔧",
    )

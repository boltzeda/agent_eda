"""Hermes plugin for publicly downloadable ADI simulation models."""

from .service import ADIModelAcquirer

__all__ = ["ADIModelAcquirer"]


def register(ctx) -> None:
    """Register the ADI-only acquisition tool without altering the generic resolver."""
    # Keep the standalone package importable and testable without Hermes on
    # PYTHONPATH; only plugin discovery needs the Hermes tool registry.
    from .tools import ACQUIRE_ADI_MODEL_SCHEMA, _handle_acquire_adi_model

    ctx.register_tool(
        name="acquire_adi_model",
        toolset="adi_model_acquirer",
        schema=ACQUIRE_ADI_MODEL_SCHEMA,
        handler=_handle_acquire_adi_model,
        emoji="⚡",
    )

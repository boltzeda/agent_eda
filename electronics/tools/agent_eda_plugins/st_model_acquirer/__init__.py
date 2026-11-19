"""Hermes plugin for publicly downloadable STMicroelectronics simulation models."""

from tools.mode_acquisation.st_model_acquirer.service import STModelAcquirer

__all__ = ["STModelAcquirer"]


def register(ctx) -> None:
    """Register the ST-only acquisition tool without altering the generic resolver."""
    from .tools import ACQUIRE_ST_MODEL_SCHEMA, _handle_acquire_st_model

    ctx.register_tool(
        name="acquire_st_model",
        toolset="st_model_acquirer",
        schema=ACQUIRE_ST_MODEL_SCHEMA,
        handler=_handle_acquire_st_model,
        emoji="⚡",
    )

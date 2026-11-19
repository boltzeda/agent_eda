"""Hermes plugin for publicly downloadable NXP simulation models."""

from .service import NXPModelAcquirer

__all__ = ["NXPModelAcquirer"]


def register(ctx) -> None:
    """Register the NXP-only acquisition tool without altering other resolvers."""
    from .tools import ACQUIRE_NXP_MODEL_SCHEMA, _handle_acquire_nxp_model

    ctx.register_tool(
        name="acquire_nxp_model",
        toolset="nxp_model_acquirer",
        schema=ACQUIRE_NXP_MODEL_SCHEMA,
        handler=_handle_acquire_nxp_model,
        emoji="⚡",
    )

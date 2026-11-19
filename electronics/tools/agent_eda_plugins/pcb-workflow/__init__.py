"""Standalone PCB workflow and its Agent EDA tool registration."""

from pathlib import Path

__version__ = "0.1.0"


def register(ctx) -> None:
    """Expose the existing stage dispatcher as a native Agent EDA tool."""
    from .agent_eda_bridge import register as register_tools

    register_tools(ctx)
    ctx.register_skill(
        "pcb-workflow",
        Path(__file__).parent / "skills" / "pcb-workflow" / "SKILL.md",
    )

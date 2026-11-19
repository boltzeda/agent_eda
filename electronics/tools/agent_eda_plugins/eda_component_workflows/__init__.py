"""Agent-EDA tools for component datasheets and KiCad libraries."""


def register(ctx) -> None:
    from .tools import TOOL_DEFINITIONS

    for definition in TOOL_DEFINITIONS:
        ctx.register_tool(
            name=definition["name"],
            toolset="eda_component_workflows",
            schema=definition["schema"],
            handler=definition["handler"],
            description=definition["description"],
            emoji=definition["emoji"],
        )

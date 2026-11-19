"""KiCad schematic tools backed by a plugin-owned Circuit Synth environment."""


def register(ctx) -> None:
    from .tools import (
        KICAD_SCHEMATIC_GENERATE_SCHEMA,
        KICAD_SCHEMATIC_SETUP_SCHEMA,
        handle_kicad_schematic_generate,
        handle_kicad_schematic_setup,
    )

    ctx.register_tool(
        name="kicad_schematic_setup",
        toolset="kicad_generation",
        schema=KICAD_SCHEMATIC_SETUP_SCHEMA,
        handler=handle_kicad_schematic_setup,
        emoji="🔧",
    )
    ctx.register_tool(
        name="kicad_schematic_generate",
        toolset="kicad_generation",
        schema=KICAD_SCHEMATIC_GENERATE_SCHEMA,
        handler=handle_kicad_schematic_generate,
        emoji="📐",
    )

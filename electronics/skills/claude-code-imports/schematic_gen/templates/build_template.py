"""Build seed template from KiCAD system libraries.

Creates a .kicad_sch with pre-placed seed symbols at off-screen
positions (x=-200) that serve as clone sources for SchematicEngine.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from skip import Schematic
from engine.backends.dynamic_symbol_loader import DynamicSymbolLoader

TEMPLATE = Path(__file__).parent / "seed_template.kicad_sch"

SEED_SYMBOLS = [
    ("Device", "R", "_SEED_R"),
    ("Device", "C", "_SEED_C"),
    ("Device", "L", "_SEED_L"),
    ("Device", "Crystal", "_SEED_Y"),
    ("Device", "D", "_SEED_D"),
    ("Device", "LED", "_SEED_LED"),
    ("Device", "Q_NPN", "_SEED_Q_NPN"),
    ("Device", "Q_NMOS", "_SEED_Q_NMOS"),
    ("Amplifier_Operational", "LM358", "_SEED_U_OPAMP"),
    ("Connector_Generic", "Conn_01x02", "_SEED_J2"),
    ("Connector_Generic", "Conn_01x04", "_SEED_J4"),
    ("Regulator_Linear", "AMS1117-3.3", "_SEED_U_REG"),
    ("Switch", "SW_Push", "_SEED_SW"),
    ("power", "PWR_FLAG", "_SEED_PWR"),
]

# Minimal valid .kicad_sch as starting point
MINIMAL_SCH = """(kicad_sch (version 20250114) (generator "schematic-gen-template") (uuid a0000000-0000-0000-0000-000000000001) (paper "A4") (lib_symbols) (sheet_instances (path "/" (page "1"))))"""


def build_template():
    TEMPLATE.write_text(MINIMAL_SCH)
    sch = Schematic(str(TEMPLATE))
    loader = DynamicSymbolLoader()

    y_start = -100
    y_step = -12

    for i, (lib, sym, ref) in enumerate(SEED_SYMBOLS):
        y = y_start + i * y_step
        print(f"Adding {lib}:{sym} as {ref} at (-200, {y})...")
        sch.write(str(TEMPLATE))
        loader.add_component(
            schematic_path=TEMPLATE,
            library_name=lib,
            symbol_name=sym,
            reference=ref,
            value=sym,
            x=-200.0,
            y=float(y),
        )
        sch = Schematic(str(TEMPLATE))

    sch.write(str(TEMPLATE))
    print(f"\nTemplate written to {TEMPLATE} with {len(SEED_SYMBOLS)} seed symbols.")


if __name__ == "__main__":
    build_template()

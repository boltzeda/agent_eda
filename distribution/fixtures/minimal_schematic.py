"""Small native schematic fixture for the Agent-EDA qualification check."""

from pathlib import Path

from circuit_synth import Component, Net, circuit
from circuit_synth.kicad.sch_gen.main_generator import SchematicGenerator


@circuit(name='resistor_fixture')
def build():
    resistor = Component(symbol='Device:R', ref='R1', value='1k',
                         footprint='Resistor_SMD:R_0603_1608Metric')
    resistor['1'] += Net('VIN')
    resistor['2'] += Net('GND')


def main():
    output = Path(__file__).resolve().parent
    design = build()
    netlist = output / 'resistor_fixture.json'
    design.generate_json_netlist(str(netlist))
    result = SchematicGenerator(str(output.parent), output.name).generate_project(
        json_file=str(netlist), force_regenerate=True, generate_pcb=False,
        placement_algorithm='hierarchical', schematic_placement='connection_aware')
    if not result.get('success', True):
        raise RuntimeError(result.get('error', 'generation failed'))


if __name__ == '__main__':
    main()

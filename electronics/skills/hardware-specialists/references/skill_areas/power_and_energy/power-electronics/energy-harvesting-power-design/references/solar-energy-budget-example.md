# Solar Embedded Supply Energy-Budget Example

Illustrative baseline from a small outdoor embedded node:

- Solar panel: 6 V nominal, 1 W
- Battery: 1S Li-ion, 3.7 V nominal, 2 Ah
- Regulated output: 3.3 V
- Maximum continuous load: 100 mA
- Transient load: 500 mA
- Intended average load: 20 mA
- Example equivalent sun duration: 4 h/day

Checks:

- Nominal battery energy = 3.7 V × 2 Ah = 7.4 Wh.
- At 90% usable conversion efficiency, approximate load-side energy = 6.66 Wh.
- At 3.3 V × 100 mA = 0.33 W, maximum-load autonomy is about 20.2 h.
- At 3.3 V × 20 mA continuously, daily load energy is 1.584 Wh/day.
- A 1 W panel with 4 equivalent sun-hours produces about 4 Wh/day before derating.

Interpretation: the panel can support the 20 mA average target with margin, but cannot sustain the 100 mA maximum continuously. The 100 mA value must be treated as an operating maximum, not the energy-neutral average.

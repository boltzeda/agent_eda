# LM741 non-inverting amplifier example

```text
                             +15 V
                               │
                        ┌──────┴──────┐
                        │    LM741    │
    Vin ───────────────►│ +        OUT├────── Vout
                        │             │
                 NINV ─►│ −           │
                   ▲    └─────────────┘
                   │          │
              Rg = 10 kΩ      │
                   │          │
                  AGND   Rf = 100 kΩ
                              │
                              └─────────── Vout
```

Connectivity:

- `Vin` connects to the non-inverting input.
- `NINV` connects to the inverting input, the top of `Rg`, and the left side
  of `Rf`.
- `Rg = 10 kΩ` connects `NINV` to `AGND`.
- `Rf = 100 kΩ` connects `NINV` to `Vout`.
- `Vout` connects to the output and the feedback resistor.
- The ideal closed-loop gain is `1 + Rf/Rg = 11`, subject to the LM741's
  datasheet limits, loading, slew rate, and supply rails.

Before a KiCad schematic is marked verified, add and validate the LM741
negative supply (`V-`) and define the input source, output load, and operating
rails. A single `+15 V` rail with `AGND` is not enough to establish a valid
LM741 operating point.

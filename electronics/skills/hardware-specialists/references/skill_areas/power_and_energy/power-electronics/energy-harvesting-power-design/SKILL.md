---
name: energy-harvesting-power-design
description: Class-level workflow for sizing and reviewing solar-powered battery chargers and regulated supplies for embedded systems, with explicit harvested-energy balance, autonomy, duty-cycle, and transient-load checks.
---

# Energy-Harvesting Power Design

Use this skill whenever a design includes solar or another intermittent source, rechargeable storage, and a regulated embedded-system load.

## Required design sequence

1. Freeze the application context: source type and nominal rating, battery chemistry/cell count/capacity, output rails, maximum load, expected average duty cycle, environment, and autonomy target.
2. Separate load specifications into:
   - maximum continuous load;
   - transient/peak load;
   - intended average load or duty-cycle profile;
   - sleep/standby current.
   Never treat a peak or maximum load as the sustainable average unless the source-energy calculation proves it.
3. Calculate and present:
   - nominal battery energy: `Vbattery_nominal × Ah`;
   - usable load energy after battery derating and converter efficiency;
   - autonomy at maximum continuous load;
   - daily load energy at the intended average duty cycle;
   - harvested energy using an explicit solar-runtime/equivalent-sun-hours assumption;
   - charging margin after charger, wiring, temperature, shading, and orientation losses.
4. Check energy balance before topology approval. If harvested energy is below daily load energy, either reduce average duty cycle, increase panel/storage, or state that the battery is only a finite-runtime buffer.
5. Choose topology only after the voltage ranges are explicit:
   - charger input range must include the real solar-panel operating range;
   - battery-to-output regulation must account for the full battery range relative to the output;
   - use buck-boost when battery voltage crosses the regulated output;
   - size the regulator for peak current and transient response, not average current alone.
6. Include protection for reverse current, solar-input overvoltage/transients, battery overcharge/over-discharge, overcurrent/short circuit, and output brownout behavior.
7. State thermal density constraints for the charger and regulator, especially in sealed or sun-exposed enclosures.

## User-delegated assumptions

When the user explicitly asks the agent to choose values or proceed autonomously, select conservative, internally consistent baseline values instead of repeatedly reopening requirements. Label the assumptions, show their energy and transient consequences, and retain the normal approval gate before advancing.

## Presentation rules

- Show an ASCII power-flow diagram for every solar/battery supply design.
- Present maximum load and sustainable average load as separate values.
- Label all solar assumptions, especially equivalent sun hours and derating.
- State plainly when a proposed panel cannot sustain the maximum load continuously.
- Gate the derived specification table for user approval before advancing to architecture.

## Periodic Wi-Fi sensor-node baseline

For unattended soil/environment nodes that transmit on a fixed interval, model
at least four separate load states: deep sleep, sensor acquisition, Wi-Fi
association/transmit burst, and firmware/storage overhead. Convert each state
using its duration and repetition interval into daily mAh/Wh; never size the
panel from the Wi-Fi peak current or size the battery from nominal watt-hours
without derating.

A practical autonomous baseline when the user delegates assumptions is a 1S
3 Ah lithium cell, a 3 W class panel, a 10-minute reporting interval, and a
conservative 2 equivalent-sun-hour/day assumption with 50% harvest derating.
These are starting assumptions only; state them explicitly and gate the derived
energy table for approval. Use a regulator topology that covers the full cell
range, and reserve local capacitance/regulator peak current for Wi-Fi bursts.

The associated worked pattern is documented in
`references/periodic-wifi-soil-node.md`.
- A battery’s nominal watt-hours are not fully available at the regulated output.
- A 500 mA Wi-Fi or processor burst requires local capacitance and regulator transient capability even when average power is low.
- A 1-cell Li-ion range of roughly 3.0–4.2 V cannot be regulated to 3.3 V with a buck-only converter across the whole discharge range.
- A solar charger selected only from the panel nominal voltage may fail at low-light voltage or overvoltage at open circuit.

## Reference material

See `references/solar-energy-budget-example.md` for a worked energy-balance example separating maximum load, average load, battery autonomy, and harvested daily energy.

## Verification checklist

Before finalizing Section 1b, verify the arithmetic with a calculation tool and confirm:

- battery energy and usable energy are dimensionally correct;
- maximum-load autonomy is not confused with solar-sustained operation;
- daily harvested energy exceeds daily load energy with margin for the stated deployment assumptions;
- charge current is physically compatible with panel power;
- output peak current and input/battery current are both checked.

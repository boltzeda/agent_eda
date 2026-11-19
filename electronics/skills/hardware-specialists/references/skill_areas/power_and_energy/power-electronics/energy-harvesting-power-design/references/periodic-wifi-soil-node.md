# Periodic Wi-Fi Soil-Node Energy Budget

Use this as a compact pattern, not as a guaranteed design.

Baseline assumptions:
- 1S Li-ion/LiPo battery: 3.7 V nominal, 3 Ah
- 3 W nominal solar panel, approximately 6 V operating point
- Report every 600 s
- Conservative harvest: 2 equivalent sun-hours/day and 50% overall derating
- 3.3 V regulated load rail

Separate load states:
- Deep sleep: 0.08 mA continuously
- Sensor acquisition: 20 mA for 2 s per report
- Wi-Fi burst: 250 mA for 4 s per report
- Firmware/storage overhead: 10 mA for 5 s per report

For each state, calculate:
`daily_mAh = current_mA * active_seconds_per_day / 3600`
`daily_Wh = rail_voltage * daily_mAh / 1000`

Then apply converter losses to obtain battery-side energy. Compare that result
with:
`harvested_Wh_per_day = panel_W * equivalent_sun_hours * total_derating`

Check both average energy and peak current. The panel sustains the average
energy budget; the battery and regulator handle nighttime operation and Wi-Fi
transients. Charging must be temperature-qualified for the selected cell.

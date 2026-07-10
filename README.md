# Tuya EV Charger Local (Home Assistant) — stripped-down

Local Home Assistant integration for Tuya EV chargers over LAN using `tinytuya`.

> **This is a stripped-down version of the original integration.**
> The built-in solar **surplus mode** (the controller, its sensors, switches,
> numbers, selects and services) has been removed. Charging control is left to
> [**evcc**](https://evcc.io/), which talks to this integration's plain
> entities. French translations have also been removed; the integration ships
> English strings only.

## Credits

Based on the original **Tuya EV Charger Local** integration by
**Valentin Lachand Pascal** (GitHub: [@lachand](https://github.com/lachand)).

- Original repository: https://github.com/lachand/EV_charger

All credit for the original work belongs to the author above. This version only
removes the surplus feature and the French localization.

Tested charger reference: `de-portable-ev-charger-3-5kw-v2`

## Quickstart

1. Add this repository in HACS (`Integrations` > `Custom repositories` > `Integration` category).
2. Install the integration, then restart Home Assistant.
3. Collect `host`, `device_id`, `local_key` (see section below).
4. Add the integration from `Settings` > `Devices & Services`.

## Get the local_key

Recommended method (TinyTuya + Tuya IoT Cloud):

1. Create a developer account on https://iot.tuya.com.
2. Create a Smart Home cloud project.
3. Link your Tuya/Smart Life app account to that project.
4. Run:

```bash
python -m tinytuya wizard
```

5. Enter API Key, API Secret and region.
6. Read `device_id` and `local_key` from output or generated `devices.json`.

Notes:

- If you re-pair/reset the device, `local_key` can change.
- `local_key` is a secret.

## Using it with evcc

This integration only exposes and controls the charger; the charging logic
(PV surplus, scheduling, phases, etc.) is handled by evcc. The relevant
entities for an evcc `custom` charger are:

- `sensor.<name>_evcc_status` — IEC 61851 status letter (`A` ready / `B` connected / `C` charging)
- `switch.charge_session` — enable/disable charging (evcc `enable`/`enabled`)
- `number.charge_current` — charge current setpoint in A
- `sensor.<name>_power_l1` — active power
- `sensor.<name>_current_l1` — current
- `sensor.<name>_charge_energy_total` — cumulative charged energy (charge meter)

## Options

- `scan_interval`
- `charger_profile`
- `charger_profile_json` (optional)

## Exposed entities

- `sensor`: voltage, current, power, temperature, cumulative charged energy,
  evcc status, human-friendly status, work state (+ debug), countdown,
  self-test, alarm, available currents, product variant
- `number`: charge current setpoint
- `switch`: charge session, NFC, scheduled charging
- `button`: reboot charger
- `time`: schedule start / end

## HACS compatibility

Repository side requirements:

- Add repository description
- Add valid topics
- Provide brand assets or submit brand to Home Assistant brands repo

Integration side requirements:

- `hacs.json` must use valid keys
- `manifest.json` must include `issue_tracker`
- `manifest.json` keys must be sorted (`domain`, `name`, then alphabetical)

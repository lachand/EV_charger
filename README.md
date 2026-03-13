# Tuya EV Charger Local (Home Assistant)

Integration Home Assistant locale pour borne Tuya en LAN via `tinytuya`.
Local Home Assistant integration for Tuya EV chargers over LAN using `tinytuya`.

Repository: https://github.com/lachand/EV_charger  
Author: Valentin Lachand Pascal (GitHub: [@lachand](https://github.com/lachand))

Tested charger reference: `de-portable-ev-charger-3-5kw-v2`

## Quickstart

### FR

1. Ajoute ce depot dans HACS (`Integrations` > `Custom repositories` > categorie `Integration`).
2. Installe `Tuya EV Charger Local` puis redemarre Home Assistant.
3. Recupere `host`, `device_id`, `local_key` (voir section plus bas).
4. Ajoute l'integration depuis `Parametres` > `Appareils et services`.

### EN

1. Add this repository in HACS (`Integrations` > `Custom repositories` > `Integration` category).
2. Install `Tuya EV Charger Local`, then restart Home Assistant.
3. Collect `host`, `device_id`, `local_key` (see section below).
4. Add the integration from `Settings` > `Devices & Services`.

## Get the local_key / Recuperer la local_key

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

## Surplus mode (simplified)

The surplus UX is intentionally short.

User entities:

- `switch.charge_session`
- `number.charge_current`
- `switch.surplus_mode`
- `binary_sensor.surplus_regulation_active`
- `sensor.surplus_last_decision_reason`
- `sensor.surplus_raw_w`
- `sensor.surplus_effective_w`
- `sensor.surplus_battery_discharge_over_limit_w`
- `sensor.surplus_target_current_a`
- `number.surplus_battery_soc_high_threshold_pct`
- `number.surplus_battery_soc_low_threshold_pct`
- `number.surplus_start_threshold_w`
- `number.surplus_stop_threshold_w`
- `number.surplus_max_battery_discharge_for_ev_w`
- `select.surplus_profile`

### Simplified behavior

- `classic` vs `zero_injection` is auto-detected:
- if curtailed power sensor is configured -> `zero_injection`
- else -> `classic`
- Battery hysteresis:
- above high threshold: curtailed/battery contribution allowed
- below low threshold: curtailed/battery contribution blocked
- Optional battery net-discharge guard:
- configurable max discharge budget (W) for EV charging
- discharge above budget is subtracted from available surplus (downshift if possible)
- Forecast is optional and used only as anti-drop guard (avoid stop on short cloud transient).
- Quick profiles:
- `eco`: conservative, avoids battery discharge
- `balanced`: default behavior
- `fast`: reacts faster and starts with lower surplus
- The rest is fixed internally (voltage, ramp, delays, cooldowns, protections).

## Options

- `scan_interval`
- `charger_profile`
- `charger_profile_json` (optional)
- `surplus_mode_enabled`
- `surplus_sensor_entity_id` + `surplus_sensor_inverted`
- `surplus_curtailment_sensor_entity_id` + `surplus_curtailment_sensor_inverted` (optional)
- `surplus_battery_soc_sensor_entity_id` (optional)
- `surplus_battery_soc_high_threshold_pct`
- `surplus_battery_soc_low_threshold_pct`
- `surplus_battery_net_discharge_sensor_entity_id` + `surplus_battery_net_discharge_sensor_inverted` (optional)
- `surplus_allow_battery_discharge_for_ev`
- `surplus_max_battery_discharge_for_ev_w`
- `surplus_start_threshold_w`
- `surplus_stop_threshold_w`
- `surplus_forecast_sensor_entity_id` (optional)

## Home Assistant services

- `tuya_ev_charger.force_charge_for`
- `tuya_ev_charger.pause_surplus`
- `tuya_ev_charger.profile_assistant`
- `tuya_ev_charger.set_surplus_profile`

## Exposed entities

- `sensor`: electrical values, charger state, diagnostics
- `number`: current setpoint + battery high/low thresholds
- `switch`: charge session, NFC, surplus mode
- `binary_sensor`: surplus regulation active
- `button`: reboot charger

## Lovelace

A simplified example card is provided:

- `lovelace/charge_intelligente.yaml`

## HACS compatibility

Repository side requirements:

- Add repository description
- Add valid topics
- Provide brand assets or submit brand to Home Assistant brands repo

Integration side requirements:

- `hacs.json` must use valid keys
- `manifest.json` must include `issue_tracker`
- `manifest.json` keys must be sorted (`domain`, `name`, then alphabetical)

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

### Setting it up

Surplus mode needs one thing: a sensor reporting your **grid** power, not your
solar production.

1. Open the integration's **Configure** dialog.
2. Set **Grid power sensor** to any `sensor.*` entity whose state is a number in
   watts. Every sensor entity is selectable, so if yours does not show up, check
   that its state is numeric and that it is not `unavailable`.
3. Sign convention: **positive when importing** from the grid, **negative when
   exporting**. If yours is the other way round, tick **Invert grid sensor sign**
   rather than creating a template sensor.
4. Set **start** and **stop** thresholds (W). Charging starts once the available
   surplus stays above the start threshold, and stops below the stop threshold.
   The stop threshold is clamped to never exceed the start threshold.
5. Turn on `switch.surplus_mode`.

To check it is working, watch `sensor.surplus_raw_w` (what the integration reads
from your sensor, after any inversion) and `sensor.surplus_last_decision_reason`,
which states in plain text why it started, stopped, or did nothing.

Everything else — battery thresholds, forecast, curtailment — is optional.

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
- `continuous_current` — adjust the current in 1 A steps (default **on**). DP 107
  only advertises the shortcuts the Tuya app shows, not a hardware limit, so any
  value between the charger's minimum and its own maximum (DP 152) is accepted.
  Turn it off if your charger really only accepts the advertised steps.
- `vehicles` (optional) — comma-separated car names, e.g. `Zoe, Kangoo`. Setting
  this adds an **Active vehicle** select plus one cumulative energy sensor per
  car; charged energy is attributed to whichever car is selected.
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

- `sensor`: electrical values (per phase: L1, plus L2/L3 on 3-phase models),
  total power, session energy and duration, last session record, charger state,
  diagnostics
- `number`: current setpoint + battery high/low thresholds
- `switch`: charge session, NFC, surplus mode
- `select`: surplus profile, active vehicle (when `vehicles` is configured)
- `binary_sensor`: surplus regulation active
- `button`: reboot charger

The **session energy** sensor is `total_increasing`, so it can be added straight
to the Energy Dashboard: the charger exposes no lifetime meter, only a counter
that resets each session, and Home Assistant accumulates those cycles correctly.

On single-phase chargers the L2/L3 sensors stay unavailable rather than
reporting a misleading 0 V.

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

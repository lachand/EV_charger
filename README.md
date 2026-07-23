# Tuya EV Charger Local (Home Assistant)

Local Home Assistant integration for Tuya EV chargers over LAN, using `tinytuya`.
No cloud is required to read or control the charger.

Integration Home Assistant locale pour borne Tuya en LAN via `tinytuya`.

Repository: https://github.com/lachand/EV_charger
Author: Valentin Lachand Pascal (GitHub: [@lachand](https://github.com/lachand))

Reference charger: `de-portable-ev-charger-3-5kw-v2`. Also reported working on
the dé Mobile Wallbox 11 kW 3-phase and other dé/Tuya models — see
[Compatibility](#compatibility).

---

## Install

1. Add this repository in HACS (`Integrations` → `Custom repositories` →
   category `Integration`).
2. Install **Tuya EV Charger Local**, then restart Home Assistant.
3. Add the integration from `Settings` → `Devices & Services` → `Add integration`.

## Setup

The setup flow offers three ways to get your charger's credentials. Pick one.

### 1. Scan the network (easiest, no account)

Listens for Tuya UDP broadcasts and lists the devices it finds, pre-filling the
IP, device ID and protocol version. You then paste the `local_key`.

Requires Home Assistant to be on the **same subnet** as the charger — UDP
broadcasts do not cross VLANs or most Wi-Fi repeaters in NAT mode.

### 2. Fetch credentials from Tuya Cloud (no manual copying)

Enter your Tuya IoT **Access ID** and **Access Secret**, pick your charger from
the list, and both `device_id` and `local_key` are filled in automatically. The
IP is still resolved locally.

To get those credentials:

1. Create a free account on https://iot.tuya.com.
2. Create a Cloud project (development method **Smart Home**, data centre =
   your region, e.g. *Central Europe*). Note the **Access ID** and
   **Access Secret**.
3. In **Devices → Link Tuya App Account**, add your app account by scanning the
   QR code from the Smart Life app (*Me* → scan icon).

If you keep these credentials configured, the integration can also re-download
the `local_key` on its own when it changes — see
[Automatic recovery](#automatic-recovery).

> Tuya IoT trial projects expire (1 month, extendable to ~6). After expiry the
> cloud lookup stops working; local control is unaffected.

### 3. Enter everything manually

Classic path if you already have `host`, `device_id` and `local_key`, for example
from `python -m tinytuya wizard`, which writes them to `devices.json`.

`local_key` is a secret, and it **changes if you re-pair or reset the charger**.

---

## Automatic recovery

Two things routinely break a local Tuya setup. The integration now heals from
both without any manual reconfiguration.

**The IP changes** (new DHCP lease after a power cycle). The charger is located
again by its `device_id`, which is stable across power cycles, from its UDP
broadcast. If the stored `device_id` is itself stale, candidates are probed with
your `local_key` and the one that answers a real status read is adopted.
Recovery happens in memory, so no reload; the new IP is persisted at the next
startup.

**The `local_key` changes** (after re-pairing). When the control port answers but
payloads no longer decrypt, the key is re-downloaded from the Tuya Cloud and
persisted — only if you configured cloud credentials.

Home Assistant DHCP discovery is also declared, so a new lease can update the IP
immediately when the charger's MAC is known.

> Still, the most robust fix is a **DHCP reservation** on your router. Automatic
> recovery is a safety net, not a substitute.

---

## Charging current

Chargers advertise a short list of currents in DP 107, e.g. `[6, 8, 10, 13, 16]`.
That is only the set of shortcuts the Tuya app displays — **not** a hardware
limit. Verified by writing 11 A to a charger that advertises the list above and
reading it back successfully.

So `number.charge_current` accepts **any value in 1 A steps**, from the charger's
minimum up to its own maximum (DP 152). Turn off the `continuous_current` option
if your charger really is restricted to the advertised steps.

---

## Surplus mode

Charge the car from solar surplus only.

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

### Behaviour

- `classic` vs `zero_injection` is auto-detected: a curtailed-power sensor
  selects `zero_injection`, otherwise `classic`.
- Battery hysteresis: above the high threshold the battery may contribute,
  below the low threshold it may not.
- Optional battery net-discharge guard: discharge above the configured budget is
  subtracted from the available surplus, downshifting rather than stopping when
  possible.
- Forecast is optional and used only as an anti-drop guard, to avoid stopping on
  a short cloud transient.
- Quick profiles via `select.surplus_profile`:
  - `eco` — conservative, avoids battery discharge
  - `balanced` — default
  - `fast` — reacts sooner, starts on less surplus
- The rest (line voltage, ramp, delays, cooldowns, protections) is fixed
  internally.

### Installation limit

A charger's rating is not its circuit's rating: a 32 A unit is often wired to a
smaller breaker, and many wiring rules require the circuit to be sized above the
charging current (Spain's ITC-BT-52 asks for 125 %, making 20 A the real ceiling
on a 25 A breaker).

Set **Maximum charging current** to what your *installation* can carry (`0` uses
the charger's own limit). It caps `number.charge_current` **and** everything
surplus regulation and load balancing may write — they all draw from the same
list, so nothing can widen it afterwards.

**Minimum charging current** raises the floor, for cars that refuse to start
below 8 A.

Unlike load balancing below, this needs no grid sensor: it is a fixed property
of your wiring, not a live measurement.

---

## Load balancing

Set **Maximum house power** to your subscribed power in watts (`0` disables it).
Using the same grid sensor as surplus mode, the integration keeps the whole
house — car included — under that limit: it caps the charging current, and stops
charging outright if even the minimum current would not fit.

This is a safety limit, not a surplus feature: it applies **whether or not
surplus mode is on**, and nothing raises the current above the cap afterwards.
With no grid sensor, or when its reading is unavailable, no cap is applied —
capping on a stale measurement would be worse than not capping at all.

Typical case: a 6 kVA subscription with the oven, the hob and the car all on.

---

## Off-peak hours and departure time

Two optional settings, both empty by default and both inert until filled in:

| Option | Example | Meaning |
|---|---|---|
| **Off-peak windows** | `22:00-06:00, 12:30-14:30` | Only charge during these hours |
| **Departure time** | `07:00` | Be ready by then… |
| **Energy needed by departure** | `20` | …with this many kWh delivered |

With windows configured, charging waits for off-peak. The deadline overrides
that: once waiting any longer would miss the departure — the remaining energy at
the current charging power, plus a 20-minute safety margin — the charge starts
immediately, off-peak or not. A departure time without an energy target does
nothing, since there is no way to know how long the charge takes.

Malformed windows are ignored rather than fatal, so a typo narrows the schedule
instead of breaking the integration.

**Current limitation:** the planner applies when surplus mode is **off**. With
surplus mode on, surplus regulation decides alone and the off-peak schedule is
not consulted — combining the two (solar first, off-peak next, full price last)
is planned but not implemented.

---

## Per-vehicle energy tracking

Set the `vehicles` option to a comma-separated list, e.g. `Zoe, Kangoo`. That
adds a `select.active_vehicle` entity plus one cumulative energy sensor per car.
Charged energy is attributed to whichever car is selected, and totals survive
restarts.

The charger cannot know which car is plugged in, so the select is manual — but
it is a normal entity, so an automation can flip it from a BLE/GPS tracker, an
NFC tag, or anything else that identifies the car.

---

## Energy Dashboard

Add `sensor.energy_session` as a device consumption source.

These chargers expose **no lifetime meter** — only a per-session counter that
resets. The sensor is therefore published as `total_increasing`, which is exactly
the contract for a resetting meter: Home Assistant treats each reset as a new
cycle and accumulates a correct running total.

---

## Exposed entities

**Sensors** — `status` (see below), `voltage_l1`, `current_l1`, `power_l1`, and on
3-phase models the matching `*_l2` / `*_l3`; `power_total`; `energy_session`,
`session_duration`; `last_session_energy`, `last_session_duration`;
`temperature`, `work_state`, `work_state_debug`, `downcounter`, `selftest`,
`alarm`, `adjust_current_options`, `product_variant`; surplus diagnostics
(`surplus_raw_w`, `surplus_effective_w`, `surplus_target_current_a`,
`surplus_battery_discharge_over_limit_w`, `surplus_last_decision_reason`).

**Number** — `charge_current`, surplus thresholds, battery thresholds.
**Switch** — `charge_session`, `nfc_enabled`, `surplus_mode`, `schedule_enabled`.
**Select** — `surplus_profile`, `plug_in_action`, `active_vehicle` (when
`vehicles` is set).
**Time** — `schedule_start`, `schedule_end`.
**Binary sensor** — `surplus_regulation_active`.
**Button** — `reboot_charger`, `ready_to_charge`.

`sensor.status` is an enum with stable, translated values — `sleep`, `idle`,
`plugged_in`, `charging`, `waiting`, `fault`, `paused`, `charged` — decoded from
the charger's raw state string. Prefer it over `work_state_debug` in
automations: the raw strings are firmware-specific, these values are not.

`select.plug_in_action` controls what the charger does when a cable is plugged
in: **Prompt**, **Start charging**, or **Do nothing**. Setting it to *Do nothing*
is the supported way to stop a car auto-starting a charge. It is unavailable on
firmwares that do not report it.

`button.ready_to_charge` returns the charger to its ready state after a session.
On some firmwares that is what clears a stale power reading.

`sensor.evcc_status` reports the IEC 61851 letter [evcc](https://evcc.io)
expects — `A` (no vehicle), `B` (connected), `C` (charging) — so the charger can
be driven as an evcc custom charger. `WORKING` can linger after a completed
charge, so `C` additionally requires power to actually be flowing.

Writes are skipped when the charger already holds the target value: every DP
write makes the charger beep, and controllers re-assert the same setpoint on a
timer.

## Connection health

`sensor.connection_health` reports the share of polls the charger answered, with
the details as attributes: consecutive failures, last success and last failure,
the cached fault verdict, how many times the charger was relocated after an IP
change, and how many times the `local_key` was re-fetched.

It stays **available while the charger is unreachable** — that is when it is
worth reading. A Tuya charger accepts a single local connection, so contention
with the Smart Life app shows up as intermittent failures rather than a clean
outage, which is invisible on every other entity.

The same information, plus the last discovery scan, is included in the
diagnostics download.

---

## Reconfiguring

Use **Configure → Reconfigure** to change the address, `device_id` or
`local_key` without deleting the integration — deleting it would discard the
entity history and energy statistics.

## Development

```bash
pip install -r requirements-test.txt
pytest
```

The suite stubs Home Assistant rather than installing it, so it runs anywhere in
under a second. It asserts DP decoding against payloads captured from real
hardware, and guards the regressions that have reached users (options-form
serialisation, secret redaction in diagnostics, every module importing).

On single-phase chargers the L2/L3 sensors stay **unavailable** rather than
reporting a misleading 0 V. Power reads 0 whenever the charger is not actively
charging, instead of holding the last value.

---

## Options

| Option | Purpose |
|---|---|
| `scan_interval` | Polling interval, seconds |
| `charger_profile` / `charger_profile_json` | DP mapping; custom JSON overrides, validated on save |
| `continuous_current` | 1 A steps (default **on**) |
| `max_charge_current_a` / `min_charge_current_a` | Your circuit's rating; `0` uses the charger's |
| `vehicles` | Comma-separated car names; enables per-vehicle tracking |
| `max_house_power_w` | Subscribed power for load balancing; `0` disables it |
| `off_peak_windows` | `22:00-06:00, 12:30-14:30`; empty charges at any hour |
| `departure_time` / `departure_energy_kwh` | Deadline that overrides the off-peak wait |
| `off_peak_price` / `peak_price` | Price per kWh; enables session cost estimation |
| `surplus_mode_enabled` | Master switch for surplus mode |
| `surplus_sensor_entity_id`, `surplus_sensor_inverted` | Grid power sensor and its sign |
| `surplus_start_threshold_w`, `surplus_stop_threshold_w` | Start/stop thresholds |
| `surplus_curtailment_sensor_entity_id`, `surplus_curtailment_sensor_inverted` | Optional, enables `zero_injection` |
| `surplus_battery_soc_sensor_entity_id` | Optional battery SOC |
| `surplus_battery_soc_high_threshold_pct`, `surplus_battery_soc_low_threshold_pct` | Battery hysteresis |
| `surplus_battery_net_discharge_sensor_entity_id`, `surplus_battery_net_discharge_sensor_inverted` | Optional discharge guard |
| `surplus_allow_battery_discharge_for_ev` / `surplus_max_battery_discharge_for_ev_w` | Battery budget for the EV |
| `surplus_forecast_sensor_entity_id` | Optional 1 h solar forecast, anti-drop guard |

## Services

All of them take an optional `entry_id`, which is only needed if you have more
than one charger.

### `force_charge_for`

Charge at full rate for a set time, ignoring surplus regulation — the "I need to
leave in an hour" button.

```yaml
action: tuya_ev_charger.force_charge_for
data:
  duration_minutes: 60
  current_a: 16 # optional; the maximum available current if omitted
```

### `pause_surplus`

Suspend surplus regulation for a while without turning the mode off, so it
resumes by itself. Useful before running the oven, or while testing.

```yaml
action: tuya_ev_charger.pause_surplus
data:
  duration_minutes: 30
```

### `set_surplus_profile`

Switch between `eco`, `balanced` and `fast` — the same thing
`select.surplus_profile` does, callable from an automation.

```yaml
action: tuya_ev_charger.set_surplus_profile
data:
  profile: eco
```

### `set_vehicle_energy`

Correct a per-vehicle total. Attribution depends on `select.active_vehicle`
being right *at the time of the charge*, so a forgotten switch credits the wrong
car; this is how you fix it after the fact.

```yaml
action: tuya_ev_charger.set_vehicle_energy
data:
  vehicle: Zoe # must match a name in the `vehicles` option
  energy_kwh: 412.5
```

### `profile_assistant`

Dumps what the charger actually reports and suggests a DP profile. Set
`apply: true` to apply the suggestion. The report is posted as a persistent
notification and fired on the event bus as `tuya_ev_charger_profile_assistant` —
**attach it when opening an issue about an unsupported model.**

```yaml
action: tuya_ev_charger.profile_assistant
data:
  apply: false
```

---

## Session history and cost

The charger remembers exactly **one** session: DP 105 is overwritten by the
next. "How much did I charge last month" is therefore unanswerable from the
device itself.

Each completed session is now logged as it is announced, with its duration,
energy, off-peak/peak split, the vehicle it was attributed to, and its estimated
cost. The last 60 are kept.

Two entities:

- `sensor.last_session_cost` — cost of the most recent session, with the
  breakdown as attributes. Unavailable until a price is set.
- `sensor.session_count` — how many sessions are on record, with the whole log,
  total energy and total cost as attributes. **Disabled by default**, since its
  attributes are large.

Set **Off-peak price per kWh** and **Peak price per kWh** to enable costing. One
of the two is enough for a flat tariff. With both at `0` the cost is reported as
*unknown* rather than as `0` — a sensor showing 0 € for every session reads as a
working meter reporting free electricity.

**The cost is an estimate.** The charger gives a duration and a total, with no
timestamps and no breakdown, so the off-peak share is reconstructed from the
session's wall-clock window and the energy apportioned by time. That is accurate
for a wallbox holding a setpoint, and less so while a nearly-full car tapers.

---

## Device triggers

In the automation editor, pick the charger as a device and these appear directly
— no need to know which sensor holds the state or what its values are called:

| Trigger | Fires when |
|---|---|
| Started charging | status → `charging` |
| Finished charging | status → `charged` |
| Reported a fault | status → `fault` |
| A cable was plugged in | status → `plugged_in` |
| Unplugged while charging | status `charging` → `idle` |

The last one is a *transition*, not a state, which is why it is worth exposing:
an interrupted charge is otherwise indistinguishable from a normal finish
without knowing the internal vocabulary.

---

## Blueprints

Three automation blueprints ship in
[`blueprints/automation/tuya_ev_charger/`](blueprints/automation/tuya_ev_charger):

| Blueprint | What it does |
|---|---|
| `charge_notifications.yaml` | Notify on charge complete, on a fault, and on an unplug mid-charge |
| `night_charge.yaml` | Start and stop on a schedule, only when a car is plugged in |
| `vehicle_from_presence.yaml` | Set `select.active_vehicle` from a tracker, so energy lands on the right car |

Copy them into `config/blueprints/automation/tuya_ev_charger/` and reload
automations, or import them by URL from **Settings → Automations → Blueprints →
Import**.

`night_charge.yaml` overlaps with the built-in off-peak windows on purpose: use
the option for a fixed schedule, the blueprint when the schedule has to depend
on something else (a Tempo colour, a calendar, the day of the week).

## Lovelace

A dedicated card is available: **[Tuya EV Charger Card](https://github.com/lachand/tuya-ev-charger-card)**.
Install it through HACS as a *Lovelace* custom repository — it is a separate
frontend plugin, not part of this integration.

A simplified YAML example is also provided: `lovelace/charge_intelligente.yaml`.

The card lives in this repository as a git submodule for development. If you
cloned before checking it out:

```bash
git submodule update --init
```

---

## Troubleshooting

A diagnostic script is included: `tools/tuya_autodetect_test.py`. Run it on a
machine on the same network as the charger.

```bash
python3 -m venv /tmp/ttv && /tmp/ttv/bin/pip install tinytuya
/tmp/ttv/bin/python tools/tuya_autodetect_test.py --scantime 30
```

It scans for Tuya devices, reports each one's IP, protocol version and MAC, and
**tests the control port (6668)**. Useful flags:

- `--device-id <gwId>` — check that a specific charger is reachable
- `--local-key <key>` — perform a real status read and print the grid voltage
- `--config-entries <path/to/.storage/core.config_entries>` — cross-check what
  Home Assistant has stored against what is actually on the network

Common outcomes:

| Symptom | Meaning |
|---|---|
| No Tuya device found | Charger offline, or you are not on its subnet (UDP broadcast does not cross VLANs/repeaters) |
| Found, but port 6668 **refused** | Something else holds the charger's single local connection (Smart Life app, another Tuya/LocalTuya integration), or the charger is on an isolated AP |
| Found, port open, read fails | Wrong `local_key` or protocol version |
| Stored `device_id` looks like an IP | Entry created by an old buggy scan — re-add the charger |

A Tuya charger accepts **only one local connection at a time**. If Home
Assistant cannot connect, make sure no other integration or app is holding it.

---

## Compatibility

Confirmed working:

- dé Portable EV Charger 3.5 kW (single-phase) — reference device
- dé Mobile Wallbox 11 kW, 3-phase (CEE 16 A) — L1/L2/L3 exposed
- Other Tuya chargers using protocol 3.3 / 3.4 / 3.5 with the same DP layout

If your charger reports a different DP layout, use `charger_profile_json` to
supply a custom mapping, and please open an issue with a DP dump taken **while
charging** so it can be supported natively.

## HACS compatibility

Repository side: description and valid topics. Integration side: valid
`hacs.json` keys, `issue_tracker` in `manifest.json`, and manifest keys sorted
(`domain`, `name`, then alphabetical).

The icon and logo live in [`custom_components/tuya_ev_charger/brand/`](custom_components/tuya_ev_charger/brand).
Since Home Assistant **2026.3**, an integration serves its own brand images from
there, taking priority over the central CDN, so no submission to the
[home-assistant/brands](https://github.com/home-assistant/brands) repo is
needed. On Home Assistant older than 2026.3 the icon falls back to the CDN, where
this integration is not registered, so no icon is shown.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — it says what gets merged, what does
not, and why. [CHANGELOG.md](CHANGELOG.md) summarises each release;
[docs/ROADMAP.md](docs/ROADMAP.md) records what is planned and what was
deliberately dropped.

Reporting an unsupported charger? The **Unsupported or misbehaving charger**
issue template asks for the three things every previous report has needed a
follow-up to obtain.

## Licence

[MIT](LICENSE).

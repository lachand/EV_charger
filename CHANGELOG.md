# Changelog

Notable changes per release. Full notes, with the reasoning behind each choice,
are on the [releases page](https://github.com/lachand/EV_charger/releases).

The 2.x line was published as pre-releases while it stabilised, which meant HACS
installed 1.0.4 on the stable channel. **From 2.11.1 onward, releases are
published normally** and HACS offers them without enabling beta versions.

## 2.20.0

- **`quality_scale.yaml`, written honestly.** The manifest had claimed `silver`
  for a long time with no file to back the claim. This is that file, every rule
  checked against the code: all 20 bronze and 10 silver rules met, with the five
  genuine gaps marked `todo` (services registered per-entry rather than in
  `async_setup`, full config-flow test coverage, the entity-platform tests below,
  translated service exceptions, and strict typing) and the inapplicable rules
  marked `exempt`. It also maps the road to gold and platinum.
- **`PARALLEL_UPDATES` on every platform** — 0 on the read-only ones, 1 on the
  writing ones, since the charger accepts a single local connection.
- **The eight untested entity platforms now have tests** (~880 lines that had
  none): switch, select, button, time, binary_sensor, entity, repairs and
  discovery. The write paths get the most attention — the write-skip that avoids
  a needless beep, the failure that must raise rather than be swallowed, and the
  schedule write that must preserve the other end of the window.
- Suite: 439 → **485 tests**.

## 2.19.0

- **The charge curve, taper included.** 2.18.0 learned a single rate; this records
  the instantaneous power against energy already delivered, so it captures the
  taper — the last kWh arriving slower than the first. A departure needing a lot of
  energy is planned against the integrated curve rather than a flat rate, and each
  vehicle's curve is exposed as a diagnostic sensor (disabled by default) with the
  full shape in its attributes. The curve integration refuses to extrapolate past
  what it has actually observed, falling back to the flat rate there.
- **Charge-health anomalies (B12).** Several recent sessions charging well below
  the car's established best, or a run of charges each cutting short, now raise a
  repair notice — a degrading cable, connector or contactor shows up in the history
  first. Deliberately conservative: several agreeing sessions are required, and the
  notice clears itself once a healthy session is recorded.
- **Per-vehicle long-term statistics (B13).** Confirmed and pinned by test: the
  per-vehicle energy sensors are proper energy meters, so the recorder already
  keeps their history and they work in the Energy dashboard. (Retroactive import of
  pre-existing sessions was left out: it needs the recorder API, cannot be tested
  under the stub harness, and adds little over the automatic history.)
- Suite: 409 → **439 tests**.

## 2.18.0

- **Departure planning learns what the car actually achieves.** The estimate used
  the *charger's* rating, which says nothing about the vehicle: a car limited to
  3.7 kW on a 7.4 kW charger had its charging time halved and was started hours
  too late to meet its deadline. Session history has held duration, energy and
  vehicle since 2.10.0, so the rate is derived from it — per vehicle, after three
  usable sessions, taking the best observed rate rather than the mean so
  deliberately slow surplus sessions do not distort it.
- Two safety rules: learning may only ever **lower** the assumed power (more time,
  earlier start), never raise it above what the hardware allows; and a history that
  cannot be read falls back to the old estimate rather than breaking regulation.
- Suite: 389 → **409 tests**.

## 2.17.0

- **Predictive pre-emption** (the limitation documented in 2.13.1). A cap is only
  as fast as its sensor, and a hob is +2 kW in under a second — but Home Assistant
  usually knows *before* the meter. Name the switches that announce a large load
  and their wattage is held back from the car immediately, on the state change
  rather than at the next poll. The reservation **expires** after two minutes, once
  the appliance is in the measurement, so the same load is never subtracted twice.
- **Adaptive polling.** The interval now follows what the charger is doing —
  faster while regulating, slower when idle, slower still asleep — instead of
  polling every 30 s into an empty garage and holding the single local connection
  for nothing.
- **New `release_connection` service.** Lends the local socket to the Smart Life
  app (or `tinytuya`) for a few minutes, then resumes by itself. The socket is
  closed rather than idled, since an open one holds the slot anyway; entities keep
  their last values because the charger is lent out, not broken. Previously the
  only way was disabling the integration.
- Suite: 339 → **389 tests**.

## 2.16.1

- **The options form is grouped into seven collapsible sections** instead of 30
  fields in one flat list: charger and polling, charging current, protection
  limits, vehicles, off-peak/departure/prices, solar surplus, house battery. The
  three most installs never touch start collapsed; the protection limits stay
  open, because a limit nobody notices is a limit nobody sets.
  Purely cosmetic: sections nest the submitted values, and those are flattened
  again before storage, so the stored options keep their flat shape and existing
  installations need no migration.
- **`async_migrate_entry` exists.** Nothing needs migrating yet, but without the
  hook Home Assistant refuses to load an entry whose version it does not
  recognise and the user gets a broken integration with no way back. It also
  refuses an entry written by a *newer* release rather than risk misreading it
  after a downgrade.
- Suite: 331 → **339 tests**.

## 2.16.0

- **Repair notices for settings that fail silently.** A protection limit with no
  sensor never engages; a malformed off-peak window is skipped by design; a
  departure time with no energy target is ignored. None of these raise anything
  today — the feature simply does nothing. Six checks now surface in
  *Settings → Repairs*, and clear themselves once fixed.
- **Inverted grid sensor detection.** If the grid reading consistently falls when
  the car draws more, the sign convention is reversed — which makes surplus
  regulation chase its own tail and load balancing compute more headroom than the
  supply has. Requires three consecutive contradicting observations, ignores
  changes too small to attribute and readings the grid barely followed: a false
  accusation about a working setup would be worse than silence.
- Suite: 312 → **331 tests**.

## 2.15.0

- **The decision reason is readable.** `sensor.surplus_last_decision_reason` is
  now an enum sensor with all 42 reasons translated (en/fr): *"Waiting for
  off-peak hours"* instead of `tariff_waiting_for_off_peak`. Because Home
  Assistant validates an enum state against its options list, a new reason
  without a translation now fails loudly — a test enforces it.
- **The reason sensor's attributes explain the decision**: which gate decided,
  which gates declined before it, and the figures weighed (surplus, thresholds,
  protection cap and its source, the current ladder). "Why is it not charging?"
  is answerable from the UI instead of from a reading of the source.
- **New `dry_run_surplus` service** — the constant had been declared since 2.4.0
  and registered nowhere. It reports what regulation *would* do without writing
  to the charger, as a notification and a bus event. Verified side-effect free:
  the timers are copied and the forecast average is left alone, so asking the
  question cannot change the answer to the next real evaluation.
- Three unused DP constants (`DP_DO_RESET`, `DP_EARCH_FREE_CFG`, `DP_HEARTBEAT`)
  are marked as such rather than deleted: unlike the constants removed in 2.4.0
  they do not claim a feature exists, they document a reverse-engineered protocol
  cross-checked in #5, and that is worth keeping.
- Suite: 304 → **312 tests**.

## 2.14.0

- **The surplus decision layer is extracted.** `_async_evaluate_once` had reached
  **300 lines with 27 exit points**, and the 2.5.0 refactor meant to fix that had
  in fact left the file *larger* (1 197 → 1 425 lines): every feature since added
  another branch. Decisions now live in `charge_gates.py` — no Home Assistant
  import — as an ordered list of gates over an immutable snapshot, returning one
  verdict per cycle. The controller resolves inputs and applies that verdict in a
  single place.
  - `_async_evaluate_once`: **300 → 31 lines**; `solar_surplus.py` 1 425 → 1 254.
  - The 25 near-identical exit blocks collapse to 4.
  - The current ladder is narrowed by the protection caps **when the context is
    built**, so the un-narrowed ladder never reaches a gate. The 2.13.1 ordering
    fix is now a property of the data: the `inspect.getsource` guard that asserted
    on the method's source text is gone, replaced by assertions on the gate list.
  - Start/stop delays, ramp cooldowns and battery hysteresis are testable without
    Home Assistant for the first time.
- **First tests for `surplus_profiles.py`** — the only function that rewrites a
  user's stored options had none — and for **`number.py`**, the current-write path
  that beeps the charger and can interrupt a charge.
- **`ruff format` enforced.** It had never run: 35 of 49 files had drifted,
  including stray 8/16/20-space indents in the surplus controller. Verified
  semantically neutral by comparing every reformatted file's AST.
- Suite: 237 → **304 tests**.

## 2.13.3

Two defects found by writing the state machine's first tests, before touching any
of its logic.

- **A protection reduction wrote twice and left the car slower than the cap
  allowed.** After capping the current, the cycle fell through into surplus
  regulation, which wrote a second time — two DP writes, two beeps. Worse, the
  ramp could not recognise the value just written (the charger still reports the
  old setpoint, no longer on the capped ladder), so it restarted from the minimum:
  a 15 A cap left the car charging at **7 A**. The cycle now ends at the cap and
  regulates normally from the next, refreshed reading.
- **`force_charge_for` under a protection cap gave the slowest rate, not the
  fastest.** Requesting 32 A when a cap had narrowed the ladder to 15 A fell back
  to the *minimum* (6 A), answering an explicit "charge as fast as possible" with
  the slowest possible rate. It now clamps to the highest current still offered.
- `tests/test_surplus_state_machine.py`: the state machine's first coverage —
  start/stop delays, ramp cooldowns in both directions, battery hysteresis,
  protection caps and force charge. It had none, which is why both defects above
  survived several releases.

## 2.13.2

- **tinytuya raised to >= 1.20.0, for a security fix that affects every user.**
  1.20.0 makes the AES-GCM message nonce and the **v3.4/v3.5 session-key client
  nonce** use `os.urandom` instead of a time-derived value, eliminating nonce/IV
  reuse under a session key; GCM frames failing their authentication tag are now
  rejected rather than passed on as raw ciphertext. Every charger this
  integration talks to runs 3.3/3.4/3.5.
- Dependabot's PR bumped only `requirements-test.txt`: its pip ecosystem does
  not understand Home Assistant's `manifest.json`, so the **runtime** requirement
  — the only one users install — was left at 1.16.0 and the fix would have
  reached nobody. A test now fails if the two floors drift apart again.
- CI actions moved to `checkout@v7` and `setup-python@v7`, clearing the
  "Node.js 20 is deprecated" warning on every run.
- Test floors raised to the versions actually exercised (pytest 9.1.1,
  voluptuous 0.16, voluptuous-serialize 2.7, ruff 0.15.22). No functional change:
  `>=` already resolved to these.

## 2.13.1

- **Inverter output limit** (#22, reported by @SergioMonC): `max_inverter_power_w`
  with a total-load sensor. Protects a hybrid inverter whose battery hides a
  sudden household draw from the grid meter — load balancing, which reads the
  grid, is blind to it. Reads *total load* instead; the tighter of the two
  protection caps applies. Documented honestly as risk-reducing, not
  trip-proof: a cap is only as fast as its sensor.
- **Bug fix:** `_ev_power_w` read L1 only, so on a three-phase charger the car's
  draw was under-reported up to 3x and *every* current cap (load balancing
  included) over-stated its headroom — the protection could allow the overload
  it exists to prevent. Now uses total power across phases.
- **Bug fix:** `force_charge_for` bypassed both protection limits. The
  force-charge branch returned before the caps were computed, so forcing a
  charge could push the installation past its breaker or its inverter rating.
  The service overrides *surplus regulation*, not the physical limits of the
  installation. Caps are applied first, and force charge runs on the
  already-capped current ladder.

  (2.13.0 shipped the first two items with this bug still present; it was
  withdrawn, so 2.13.1 is the first release of this feature.)

## 2.12.1

- **Brand icon.** The integration now ships a proper 256×256 (and 512×512 hDPI)
  icon in `brand/`, served locally since HA 2026.3. The file that was there was
  the manufacturer's non-square wordmark — invalid as an icon, and never
  displayed. A conformance test now guards size, squareness, transparency and
  trim, since a bad icon fails silently in the UI rather than in CI.
- Corrected the README: brand assets had **not** in fact been submitted to the
  home-assistant/brands repo, which is why no icon ever appeared.

## 2.12.0

- **Installation current limit** (#21, reported by @SergioMonC): `max_charge_current_a`
  and `min_charge_current_a`. A charger's rating is not its circuit's rating — a
  32 A unit on a 25 A breaker has a real ceiling of 20 A under ITC-BT-52.
  Applied inside `allowed_currents()`, the single list both the number entity and
  surplus regulation draw from, so nothing can write above it afterwards.
  Needs no grid sensor, unlike load balancing.

## 2.11.0

- `sensor.connection_health`: share of polls answered, with consecutive
  failures, last success/failure, fault verdict, relocations and key refreshes
  as attributes. Stays available while the charger is unreachable.
- Diagnostics now carry the integration version, the connection record and the
  last discovery scan.
- **Security fix:** a tinytuya scan record spells the charger identity `ip`,
  `gwId` and `key`, which the existing redaction did not cover. Embedding the
  scan without this would have published the device ID and local key in every
  diagnostics download attached to a public issue.
- Custom DP mappings are validated on save — bad JSON, unknown fields, empty
  values, or two fields on one DP — instead of being silently replaced by the
  default profile.

## 2.10.0

- Session history: each completed session logged with duration, energy,
  off-peak/peak split, vehicle and estimated cost. Last 60 kept.
- `sensor.last_session_cost` and `sensor.session_count` (the latter disabled by
  default).
- New `off_peak_price` / `peak_price` options. With neither set, cost is
  *unknown* rather than 0.
- Fixed before shipping: prices routed through the integer path would have
  rounded a 0.16 tariff to 0; and a restart would have logged the charger's
  stored session as if it had just happened.

## 2.9.0

- Five device triggers (started/finished charging, fault, plugged in, unplugged
  while charging), located by unique_id so renaming the entity does not break
  automations.
- **The CI had been failing on every push and had not been looked at.** hassfest
  rejected the `A`/`B`/`C` evcc translation keys; the labels were dropped, since
  the values must stay uppercase for evcc.
- **The repository had no licence at all**, failing HACS validation and leaving
  nobody with the right to reuse the code. Now MIT.

## 2.8.0

- Three automation blueprints: notifications (including unplugged mid-charge),
  scheduled charging, and vehicle selection from a tracker. `blueprints/` had
  been an empty directory since it was created.
- `set_vehicle_energy` service, to correct a total attributed to the wrong car.
- The four existing services documented, with examples.

## 2.7.0

- Configurable off-peak windows (`22:00-06:00, 12:30-14:30`), wrapping past
  midnight; a malformed window is skipped rather than fatal.
- Departure deadline that overrides the tariff, with a 20-minute safety margin
  and a pessimistic power estimate so the error is towards charging early.
- Load balancing documented (it shipped undocumented in 2.6.0).

## 2.6.0

- Dynamic load balancing: caps the charging current to keep the whole house
  under the subscribed power, and stops if even the minimum will not fit.
  Applies whether or not surplus mode is on.

## 2.5.1

- Tests for the vehicle accounting and Tuya Cloud paths.
- The options form rebuilt as a data-driven table instead of one large literal.
- `async_classify_fault` and `async_tcp_reachable` de-duplicated.

## 2.5.0

- Surplus arithmetic extracted into `surplus_decision.py` — plain values in,
  plain values out, no Home Assistant — and tested directly. The 1 200-line
  controller previously had no tests because exercising the maths meant standing
  up half of Home Assistant.

## 2.4.0

- ruff, coverage config, Dependabot.
- `icons.json`, minimum Home Assistant version declared, manifest completed.
- 19 dead `CONF_*` constants removed: they described tuning that was in fact
  hardcoded.

## 2.3.1

- `.gitmodules` restored — `git clone` had been fetching an empty card directory.
- Fault diagnosis throttled: 2.2.0 ran a TCP connect and often a full read on
  every failed poll, forever.
- Re-authentication flow, so a rotated `local_key` shows a "Reconfigure" banner
  instead of failing silently.
- The rediscovery scan moved out of the update cycle.

## 2.3.0

- Device page reorganised; advanced entities disabled by default, with a repair
  flow to tidy existing installs.

## 2.2.1

- Setup failures distinguish "nothing answers", "the charger refused the
  connection" and "the credentials are wrong". They all previously read as
  *Unable to reach charger with this settings*, which blamed the local key for a
  busy port.

## 2.2.0

- evcc export (`sensor.evcc_status`), per-vehicle energy tracking, repair issues.
- Writes skipped when the charger already holds the target value: every DP write
  makes it beep.

## 2.1.2

- Re-discovery no longer probes unrelated Tuya devices on the network.

## 2.1.1 / 2.0.1

- The options form returned HTTP 500 before it opened: the fix in 2.0.1 was
  valid voluptuous but unserialisable, and Home Assistant serialises the schema
  to draw the form.

## 2.0.0

- Self-healing local control: the charger is relocated by device ID after a DHCP
  change, and the `local_key` re-fetched from the Tuya Cloud after a re-pairing.
- 1 A current steps, three-phase support, session energy tracking.

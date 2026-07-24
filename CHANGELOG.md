# Changelog

Notable changes per release. Full notes, with the reasoning behind each choice,
are on the [releases page](https://github.com/lachand/EV_charger/releases).

The 2.x line was published as pre-releases while it stabilised, which meant HACS
installed 1.0.4 on the stable channel. **From 2.11.1 onward, releases are
published normally** and HACS offers them without enabling beta versions.

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

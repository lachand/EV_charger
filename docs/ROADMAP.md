# Roadmap

Working document for the multi-release improvement effort started after **2.3.0**.

It exists so the work can be picked up from a cold start: reading this file alone should be enough
to know where we are and what to do next, without re-auditing the repository.

**Update it in the same commit as the work it describes — never afterwards.**

---

## Resume here

- **Current version:** 2.11.1
- **Phase in progress:** Phase 7 done except **B5**. B4 ✅, B7 ✅, B8 ✅, B9 ✅, B10 ✅, A6.1 ✅.
- **Next concrete action:** the roadmap is **complete except B5**. What is left is a decision, not
  a task: **all 2.x releases are pre-releases**, so `/releases/latest` returns 1.0.4 and HACS users
  on stable have received none of this work. Promoting one to a full release is the owner's call.
  **B5** (vehicle auto-identification) is deliberately undone: it needs a third-party car
  integration to test against, which nobody here has, and the blueprint `vehicle_from_presence.yaml`
  already covers the practical case.
  Suite is at **184 tests**; all four CI jobs green.
- **Hardware:** unblocked as of 2026-07-22. Read paths were re-validated live (status, phases,
  energy, evcc letter, capability detection, 1 A steps).
- **Still unvalidated on hardware:** the DP 101 write behind `button.ready_to_charge` — it needs the
  car unplugged, and has not been exercised since it shipped.
- **Known limitation shipped in 2.7.0:** the tariff planner only applies when surplus mode is
  **off**. With surplus mode on, surplus regulation decides alone. Combining them properly (solar
  first, off-peak next, full price last) is the natural follow-up and is documented as a limitation
  in the README rather than hidden.

---

## Scope

Everything from the audit **except B6** (push updates). B6 is deliberately dropped: holding the
socket open would monopolise the charger's single local connection and lock out the Smart Life app —
the exact conflict that made this charger unreachable during the audit session. Sub-second latency
does not justify that.

## Phases

| Phase | Contents | Target version | State |
|---|---|---|---|
| 0 | This file | — | ✅ done |
| 1 | Lot 1 — verified bugs | 2.3.1 | ✅ done |
| 2 | Lot 2 + Lot 4 — HA conventions, tooling | 2.4.0 | ✅ done |
| 3 | Lot 5.1 + Lot 3.1 — extract and test the surplus decision layer | 2.5.0 | ✅ done |
| 4 | Lot 3.2/3.3 + Lot 5.2/5.3 — remaining tests and refactoring | 2.5.1 | ✅ done |
| 5 | B3 — dynamic load balancing | 2.6.0 | ✅ done |
| 6 | B1 then B2 — tariffs, departure planning | 2.7.0 | ✅ done |
| 7 | B4, B5, B7, B8, B9, B10 | 2.8+ | ✅ done except B5 (2.8.0 → 2.11.0) |
| 8 | Lot 6 — documentation | ongoing | ✅ done (A6.1 in 2.8.0; A6.2 + A6.3 in 2.11.1) |

The order is not arbitrary: each phase removes an obstacle for the next. The linter (phase 2) must
exist before part B adds code; the surplus decision layer (phase 3) is the technical prerequisite
for B1/B2/B3, which all plug into it.

---

## Part A — Verified defects

Every item below was confirmed in the repository, not assumed.

### Lot 1 — Bugs 🔴 (phase 1) — ✅ shipped in 2.3.1

Notes from the implementation:
- **A1.1** the card has its own repo (`lachand/tuya-ev-charger-card`) with its own `hacs.json`, so
  the gitlink was restored rather than dropped — and the URL is HTTPS, not the SSH remote the working
  copy uses, since anyone cloning has neither push access nor a GitHub key.
- **A1.4** the scan is now backgrounded on routine polls only. The **first refresh keeps it inline**:
  setup rebuilds the client from the stored host on every retry, so an in-memory fix from a
  background task would be discarded.

| # | State | Finding | Action |
|---|---|---|---|
| A1.1 | ✅ 2.3.1 | `git ls-files -s` shows `160000 … tuya-ev-charger-card` (a gitlink) but **`.gitmodules` does not exist**. A `git clone` therefore yields an **empty** directory and `git submodule update --init` fails with no URL. The card the README advertises cannot be obtained from the repo. | Restore `.gitmodules`, or unlink the gitlink and vendor the card / split it into its own HACS repo |
| A1.2 | ✅ 2.3.1 | `coordinator._async_failure_message()` calls `async_classify_fault()` on **every** failed cycle: a TCP connect, then a **full `status()`** if the port answers. On a charger with an open port but a wrong key that is a complete read every 30 s, forever. The repair issue is also re-created each pass. *(regression introduced in 2.2.0)* | Cache the verdict behind a cooldown, mirroring `REDISCOVERY_COOLDOWN_SECONDS` |
| A1.3 | ✅ 2.3.1 | `ConnectionFault.UNDECRYPTABLE` (key rotated by re-pairing) is **detected** but `ConfigEntryAuthFailed` is never raised, so Home Assistant never shows the re-authentication banner. | Add `async_step_reauth` / `async_step_reauth_confirm`, reusing `_build_credentials_schema()` and `_async_validate_input()` |
| A1.4 | ✅ 2.3.1 | The re-discovery scan (up to 12 s) runs **inside** `_async_update_data`, delaying the coordinator and startup by that much | Move it out of the update cycle |

### Lot 2 — Home Assistant conventions 🟠 (phase 2) — ✅ shipped in 2.4.0

| # | State | Finding |
|---|---|---|
| A2.1 | ✅ 2.4.0 | `hacs.json` declares **no minimum HA version**, yet the code needs ≈ **2024.11** (`runtime_data`, `_get_reconfigure_entry`, `async_update_reload_and_abort`, `helpers.service_info.dhcp`). Older installs get cryptic errors. |
| A2.2 | ✅ 2.4.0 | No `icons.json`: 20 hardcoded `icon="mdi:…"` (14 in `sensor.py`, 6 in `number.py`). HA 2024.2+ expects `icons.json`. |
| A2.3 | ✅ 2.4.0 | `manifest.json` lacks `integration_type: "device"` and `quality_scale`. |
| A2.4 | ✅ 2.4.0 | Services take `entry_id` as **free text**; HA provides a `config_entry` selector, and `target:` would address the device naturally. Service names/descriptions should move from `services.yaml` to the `services` key of `strings.json`. |

### Lot 3 — Tests 🟠 (phases 3–4) — ✅ done

Coverage now measured in CI. `surplus_decision` 98 %, `vehicles` 97 %, `cloud` 91 %, `helpers` 96 %.
The two low scores are deliberate: `solar_surplus` (17 %) is the state machine left unrefactored,
and the entity platforms are thin wrappers omitted from the report.

| # | State | Finding |
|---|---|---|
| A3.1 | ✅ 2.5.0 | **`solar_surplus.py` (1 197 lines) has zero tests** — state machine, thresholds, battery hysteresis, ramps, cooldowns. The costliest place for a regression and the least visible. |
| A3.2 | ✅ 2.5.1 | Also uncovered: `vehicles.py` (energy accounting, re-baselining on reset), `cloud.py`, `config_flow.py`, and every entity platform. 6 of 20 modules covered. |
| A3.3 | ✅ 2.5.1 | No coverage measurement in CI, so drift is invisible. |

### Lot 4 — Tooling and hygiene 🟡 (phase 2) — ✅ shipped in 2.4.0

Notes: enabling `ruff` surfaced 35 findings, two of them real — `Any` used in `coordinator.py`
annotations without an import, hidden by `from __future__ import annotations`. Removing the dead
`CONF_*` constants also orphaned 31 `DEFAULT_/MIN_/MAX_` companions, so 50 lines went in total.

| # | State | Finding |
|---|---|---|
| A4.1 | ✅ 2.4.0 | No linter, formatter or type checker (no `ruff`, `mypy`, `pre-commit`, `pyproject.toml`). CI runs only hassfest + HACS + pytest. `ruff` would have caught the import ordering fixed by hand several times. |
| A4.2 | ✅ 2.4.0 | `blueprints/automation/tuya_ev_charger/` is an **empty directory tree** (0 files) that git does not even track, and no blueprint is mentioned in the README. |
| A4.3 | ✅ 2.4.0 | **19 dead `CONF_*` constants** in `const.py`, used nowhere and not exposed in the UI: the whole surplus fine-tuning set (`RAMP_STEP_A`, `ADJUST_COOLDOWN_S`, `FORECAST_*`, `MAX_SESSION_*`, `LINE_VOLTAGE`…). The features exist but are hardcoded; the constants suggest otherwise to anyone reading `const.py`. |
| A4.4 | ✅ 2.4.0 | No Dependabot for `tinytuya` and the GitHub actions. |

### Lot 5 — Architecture 🟡 (phases 3–4)

| # | State | Finding |
|---|---|---|
| A5.1 | ✅ 2.5.0 | *(partial: the arithmetic is out in `surplus_decision.py`; the state machine stays in the controller — rewriting it blind, on hardware that cannot currently be reached, would be reckless)* **`solar_surplus.py` was a 1 197-line monolith** mixing sensor reading, state machine, decision, ramping and snapshotting. This is the direct cause of A3.1: untestable because it does everything. Extract a **pure decision layer** (inputs → decision) testable without HA. |
| A5.2 | ✅ 2.5.1 | `config_flow.py` is 914 lines, with the options schema as one giant literal. Make it data-driven. |
| A5.3 | ✅ 2.5.1 | `async_classify_fault()` re-implements `async_tcp_reachable()`. |

### Lot 6 — Documentation 🟢 (phase 8)

| # | State | Finding |
|---|---|---|
| A6.1 | ✅ 2.8.0 | Services documented in the README, one section each with a YAML example — including that `profile_assistant` output is what to attach to an issue. |
| A6.2 | ✅ 2.11.1 | No issue templates, although a DP dump was requested by hand in #5 and #7. A "device report" template demanding `python -m tinytuya scan` plus a dump **while charging** would pay for itself immediately. |
| A6.3 | ✅ 2.11.1 | No `CHANGELOG.md` and no `CONTRIBUTING.md`, despite four external PRs — three closed for lack of a frame (they also stripped the surplus module). |

---

## Part B — Features

| # | State | Feature | Notes |
|---|---|---|---|
| B1 | ✅ 2.7.0 | **Tariff-aware charging** (phase 6) | Shipped as **configurable off-peak windows**, not a price sensor: that is what French tariffs actually look like — a couple of fixed ranges, printed on the bill — and it needs no external integration. `charge_planner.py` is pure (times in, decision out) and tested without HA. Windows wrap past midnight; a malformed window is skipped rather than fatal. |
| B2 | ✅ 2.7.0 | **Departure-time charging** (phase 6) | "X kWh by 07:00", built on the same planner: charging waits for off-peak until waiting would miss the deadline, then starts regardless of tariff. A 20-minute safety margin absorbs ramp-up. When not charging, power is estimated pessimistically (single-phase) so the error is towards starting early, never towards missing the departure. |
| B3 | ✅ 2.6.0 | **Dynamic load balancing** (phase 5) | Cut charging current when the house draws too much, to avoid tripping the main breaker — the classic 6 kVA case with oven + hob + car. Grid power is **already** read for surplus mode: invert the logic (cap instead of follow) and reuse the existing ramp. Best value/effort ratio in part B. |
| B4 | ✅ 2.10.0 | **Cost tracking** (phase 7) | Cost per session and per vehicle from a price sensor. Extends 2.2.0's per-vehicle tracking: we know the kWh per car, not the euros. |
| B5 | ⬜ | **Automatic vehicle identification** (phase 7) | The "Active vehicle" select is manual because the charger cannot know which car is plugged in. Linking a car integration (Tesla, MyRenault, Kia/Hyundai…) gives the real SoC and the identity — removing the manual step *and* enabling "charge to 80 %" rather than in kWh. |
| B6 | ❌ | ~~Push updates~~ | **Dropped.** Holding the socket open would lock out every other client including the Smart Life app. |
| B7 | ✅ 2.8.0 | **Automation blueprints** (phase 7) | Three shipped: `charge_notifications` (complete / fault / unplugged mid-charge), `night_charge` (schedule, skipping nights with no car plugged in), `vehicle_from_presence` (sets `active_vehicle` from a tracker). A surplus blueprint was *not* written: surplus is a built-in mode with its own options, so a blueprint would only duplicate it worse. |
| B8 | ✅ 2.9.0 | **Device triggers** (phase 7) | `device_trigger.py`, five triggers over the `status` sensor. The status sensor is located by unique_id suffix, not entity_id, so renaming it does not break automations — and `evcc_status` shares that suffix, which is the trap the tests pin down. `unplugged_while_charging` is a transition (`charging → idle`), the one thing genuinely not expressible without reading the source. |
| B9 | ✅ 2.10.0 | **Session history** (phase 7) | DP 105 gives the **last** session (start, end, duration, energy). Accumulating them enables a browsable history — useful for expense claims or simply knowing last month's usage. |
| B10 | ✅ 2.11.0 | **Comfort and reliability** (phase 7) | **Done:** `set_vehicle_energy` service (with a clamp at zero and validation against the configured names), and notifications via the blueprint rather than a hardcoded notifier. **2.11.0:** `sensor.connection_health` (available *while* the charger is unreachable, which is the point); diagnostics now carry the integration version, the connection record and the discovery scan — with `ip`/`gwId`/`key` newly redacted, since a tinytuya scan record spells the same secrets differently from the config entry; custom DP mappings validated on save instead of silently falling back to the default profile. Original note: notifications (charge complete, fault, unexpected unplug); a connection-health sensor; a `set_vehicle_energy` service to correct a mis-attributed total; assisted custom DP mapping (validate `charger_profile_json`, show the DPs actually detected); richer diagnostics including the discovery scan result and the fault verdict — the two things always requested in reports (#5, #7). |

---

## Working rules

- `docs/ROADMAP.md` is updated **in the same commit** as the work it describes.
- One release per phase, with notes explaining the *why*, not just the *what*.
- `pytest` green before every commit; no lot closes without tests covering what it introduced.
- Commits carry **no** `Co-Authored-By` trailer.
- Keep crediting contributors whose ideas are used: `@alexsxb`, `@algirdasc`, `@1ud0v1c0`.

## Verification notes

- **A1.1**: `git clone` into a temp directory, check `tuya-ev-charger-card/` is not empty.
- **A1.2**: force a poll failure, confirm the log shows **one** classification per cooldown period,
  not one per cycle.
- **A1.3**: change the `local_key` in the entry; HA must show the re-authentication banner, and
  fixing it must reload the integration.
- **B1/B2/B3**: test the decision layer as pure unit tests (prices, thresholds, caps in → expected
  current out), no hardware. This is exactly what the A5.1 refactor unlocks.
- Hardware validation once the charger accepts local connections again.

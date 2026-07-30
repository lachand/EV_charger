# Roadmap

Working document for the improvement effort that followed the **2.13.2** audit.
It replaces the previous roadmap (2.3.0 → 2.13.x), which described a finished
piece of work and would have misled anyone picking this up cold.

Reading this file alone should be enough to resume without re-auditing the repo.
**Update it in the same commit as the work it describes — never afterwards.**

---

## Resume here

- **Current version:** 2.21.0
- **Phase in progress:** Phases 1–5 ✅ done. **A3 ✅ and A2 ✅** (2.20.0): quality_scale.yaml written,
  every entity platform tested. **2.21.0** was two field-reported bug fixes (continuous mode's
  ceiling ignoring a narrower preset list, PR #24; the options form dropping entity-selector picks
  on save, #26), not a roadmap phase. Remaining roadmap items: **phase 6** (B8 carbon intensity, B9
  daily forecast, B10 Tempo/RTE) and **phase 7** (B11 phase imbalance, B14 session receipt, B15
  vehicle subentries).
- **Next concrete action:** **B10 (Tempo/RTE)** is the most valuable remaining feature for French
  users — a red Tempo day makes peak power prohibitive, and `charge_planner.py` already accepts
  windows, so it is a matter of deriving them from a colour sensor. B8 (carbon intensity) and B9
  (daily forecast) follow the same shape. The `todo`s left in `quality_scale.yaml` (services in
  `async_setup`, strict typing, translated service exceptions) are the route to gold.
- **Next concrete action:** **B3, decision traceability.** `charge_gates.py` now
  enumerates every reason as `DecisionReason`, so translating them (en/fr) and
  attaching a structured trace to `sensor.surplus_last_decision_reason` is
  mostly plumbing. Then **B2**: implement `dry_run_surplus` — the constant exists
  in `const.py` and is registered nowhere (A6) — by building a `GateContext` from
  supplied values and running `evaluate()` without writing to the charger. The
  pure layer makes that nearly free.
- **Suite:** 304 tests. CI green on all four jobs; `ruff format --check` now
  enforced.
- **Hardware:** the charger was unreachable from the dev machine at the time of
  writing ("No route to host" on 192.168.1.237). **The DP 101 write behind
  `button.ready_to_charge` is still unvalidated** since it shipped.

---

## Phases

| Phase | Contents | Target | State |
|---|---|---|---|
| **1** | A1 decision layer + A2 partial + formatting | 2.14.0 | ✅ done |
| 2 | B3 traceability + B2 simulation + A6 leftovers | 2.15.0 | 🔄 next |
| 3 | B5 proactive repairs (2.16.0) + A4 form sections + A5 entry migration (2.16.1) | 2.16.x | ✅ done |
| 4 | **B1 predictive pre-emption** + B6 adaptive polling + B7 Smart Life coexistence | 2.17.0 | ✅ done |
| 5 | B4 learned curve (2.18.0) + taper, B12 anomalies, B13 statistics (2.19.0) | 2.19.0 | ✅ done |
| 6 | B8 carbon intensity + B9 daily forecast + B10 Tempo/RTE + A3 `quality_scale.yaml` | 2.19.0+ | ⬜ |
| 7 | B11 phase imbalance + B14 session receipt + B15 vehicle subentries | on demand | ⬜ |

---

## Verified debt

| # | State | Finding |
|---|---|---|
| **A1** | ✅ 2.14.0 | The 2.5.0 refactor extracted the arithmetic but the orchestration kept growing: 1 197 → 1 425 lines, with `_async_evaluate_once` at 300 lines and 27 exit points. It had already cost the `force_charge_for` bug (2.13.1), guarded only by asserting on the method's *source text*. Now `charge_gates.py`: the order is a list, the timers are a passed-in `TimerState`, and one verdict per cycle. **300 → 31 lines; 25 duplicated exit blocks → 4.** |
| **A2** | ✅ 2.20.0 | Ten modules had no tests (~1 350 lines). Done: `surplus_profiles.py` (it rewrites the user's stored options — the riskiest of the set) and `number.py` (the current-write path). **Left:** `switch.py` 181, `select.py` 139, `entity.py` 121, `repairs.py` 110, `time.py` 98, `binary_sensor.py` 85, `button.py` 78, `discovery.py` 66. |
| **A3** | ✅ 2.20.0 | `manifest.json` declares `quality_scale: silver` but `quality_scale.yaml` is absent — the file Home Assistant checks the claim against. Writing it honestly (marking `todo`/`exempt`) reveals the gaps mechanically and maps a route to gold. |
| **A4** | ✅ 2.16.1 | 30 options in one flat screen. HA has supported collapsible `section`s since 2024.6; the data-driven `_OPTIONS_FORM` only needs a `section` field on `_Opt`. |
| **A5** | ✅ 2.16.1 | `VERSION = 1` with no `async_migrate_entry`. Any change to `entry.data` would break existing installs with no net. |
| **A6** | ⬜ | `SERVICE_DRY_RUN_SURPLUS` declared in `const.py`, registered nowhere — it survived the 2.4.0 purge. Also `DP_DO_RESET`, `DP_EARCH_FREE_CFG`, `DP_HEARTBEAT`, declared and unused. Implement rather than delete the first (see B2). |
| **A7** | ✅ 2.14.0 | The previous roadmap described finished work. This file replaces it. |
| — | ✅ 2.14.0 | Formatting was never enforced: 35 of 49 files had drifted, including stray 8/16/20-space indents in the surplus controller. `ruff format --check .` is now part of CI. |

---

## Proposals

| # | State | Proposal |
|---|---|---|
| **B1** ⭐⭐ | ✅ 2.17.0 | **Predictive pre-emption of the inverter cap.** 2.13.1 documented an honest limit: a cap is only as fast as its sensor, and a hob is +2 kW in under a second. But HA often knows *before* the meter — a hob switch, a smart plug turning on. Let the user name entities that announce a large load, with a wattage to reserve for each, and reduce the car immediately on the state change rather than on the measurement. Turns a physical limit into a solvable problem; nothing in the HA ecosystem does it. |
| **B2** ⭐⭐ | ⬜ | **Simulation and replay.** Implement `dry_run_surplus` (A6): "given these sensor values, what would regulation do?", answered without writing to the charger. Plus a recording mode that logs decision inputs for offline replay. Turns "surplus won't start" reports into reproducible scenarios. Cheap now that the layer is pure. |
| **B3** ⭐⭐ | ⬜ | **Decision traceability.** 39 reasons exist; the user sees one, last, untranslated (`load_limit_no_headroom`). Add a structured trace attribute — which gates ran, which one bound, with values — and translate the reasons. Makes surplus self-diagnosing instead of requiring a code read. |
| **B4** ⭐⭐ | ✅ 2.18.0 | **Learned charge curve.** `_estimate_charge_power_kw` is deliberately pessimistic for want of anything better, so departure deadlines start charges too early. Session history (2.10.0) already stores duration, energy and power: derive the vehicle's real curve, taper included, per vehicle. No new data collection. |
| **B5** ⭐ | ✅ 2.16.0 | **Proactive config repairs.** `repairs.py` exists. Detect the silent misconfigurations: an **inverted grid sensor sign** (detectable by correlation — car power up while the meter goes down), an inverter cap with no total-load sensor (protection inert), a price of 0 with the cost sensor enabled, a malformed off-peak window (skipped by design, invisibly), a departure time with no energy target. Each currently produces a user convinced the feature is broken. |
| **B6** ⭐ | ✅ 2.17.0 | **Adaptive polling.** One local connection, polled every 30 s whether charging or asleep. Fast while charging (10 s, where regulation needs it), slow at rest, suspended in `SLEEP`. Less contention with the Smart Life app; `update_interval` is already adjustable at runtime. |
| **B7** ⭐ | ✅ 2.17.0 | **Explicit Smart Life coexistence.** A switch or service that *releases* the local socket for N minutes so the phone app can be used, then resumes on its own. Today the only way is disabling the whole integration. |
| **B8** | ⬜ | **Carbon-intensity charging.** Charge on the cleanest hours, not only the cheapest, from an electricitymaps/CO2 Signal sensor. `charge_planner.py` already takes windows; derive them from a sensor. |
| **B9** | ⬜ | **Daily-forecast planning.** The solar forecast is only an anti-drop guard (500 W). A day-ahead view can decide *when* to charge: wait for the production peak, or charge early if the afternoon looks cloudy. |
| **B10** | ⬜ | **Real French tariffs (Tempo / RTE).** Configurable off-peak windows cover the common case, but Tempo's blue/white/red days change everything: on a red day the peak price is prohibitive. RTE Tempo integrations exist in HACS; accept a colour sensor and modulate. |
| **B11** | ⬜ | **Phase-imbalance detection.** L1/L2/L3 are already decoded. A persistent imbalance on a three-phase charger points at wiring or the vehicle. Nobody exposes it; an installer would value it. |
| **B12** | ✅ 2.19.0 | **Session anomalies.** From the stored history: "this session charged far slower than usual" (degrading contactor or cable), "three interrupted sessions in a row", "energy delivered falling at equal current". Predictive maintenance from data already on disk. |
| **B13** | ✅ 2.19.0 | **Per-vehicle long-term statistics.** The per-vehicle sensors are `total_increasing` but do not land properly in the Energy dashboard. `async_add_external_statistics` would give a correct per-car history, retroactively included. |
| **B14** | ⬜ | **Session receipt.** End-of-charge notification or markdown entity: energy, duration, off-peak/peak split, estimated cost, vehicle. Every field exists since 2.10.0. Useful for expense claims or splitting a bill between flatmates. |
| **B15** | ⬜ | **Vehicle subentries.** `ConfigSubentryFlow` (HA 2025+): one vehicle = one subentry with its capacity, minimum current and learned curve (B4). Replaces the comma-separated `vehicles` list, which cannot carry per-car settings. |

---

## Conduct rules

Carried over from the previous effort, because they worked:

- `pytest` green and `ruff check` + `ruff format --check` clean before every commit.
- No batch ships without a test on what it introduced.
- This file updated **in the same commit** as the work it describes.
- One release per phase, with notes explaining the *why* and not only the *what*.
- Commits carry **no** `Co-Authored-By` trailer.
- Anything not validated on hardware is announced as such — in the release notes
  and in replies to reporters.

## What phase 1 proved about method

Writing the characterisation net **before** refactoring found two real defects in
code that had shipped several releases earlier, and neither would have surfaced
from a bug report because the only symptom was "charging slower than expected":

- a protection reduction wrote twice and left the car at 7 A under a 15 A cap;
- `force_charge_for` under a cap produced the *slowest* rate instead of the fastest.

Both were fixed in **2.13.3**, separately from the refactor, so the refactor could
be verified against a green net rather than a moving target. Worth repeating for
any future work on the same path.

# Tuya EV Charger Local (Home Assistant)

Français et English documentation.

Repository: https://github.com/lachand/EV_charger  
Author: Valentin Lachand Pascal (GitHub: [@lachand](https://github.com/lachand))

## Quickstart / Démarrage rapide

### FR

1. Ajoute ce dépôt dans HACS (`Intégrations` > `Dépôts personnalisés` > catégorie `Integration`).
2. Installe `Tuya EV Charger Local`, puis redémarre Home Assistant.
3. Récupère `host`, `device_id`, `local_key` (voir section `Récupérer la local_key`).
4. Ajoute l'intégration depuis `Paramètres` > `Appareils et services`.

### EN

1. Add this repository to HACS (`Integrations` > `Custom repositories` > `Integration` category).
2. Install `Tuya EV Charger Local`, then restart Home Assistant.
3. Collect `host`, `device_id`, `local_key` (see `Get the local_key` section).
4. Add the integration from `Settings` > `Devices & Services`.

---

## Français

Intégration Home Assistant locale pour piloter une borne de recharge Tuya en LAN via `tinytuya`.

### Installation avec HACS

1. Ouvre HACS dans Home Assistant.
2. Va dans `Intégrations` puis `⋮` > `Dépôts personnalisés`.
3. Ajoute l'URL de ce dépôt avec la catégorie `Integration`.
4. Recherche `Tuya EV Charger Local` dans HACS et installe l'intégration.
5. Redémarre Home Assistant.

### Configuration

1. Va dans `Paramètres` > `Appareils et services` > `Ajouter une intégration`.
2. Choisis `Tuya EV Charger Local`.
3. Renseigne:
   - `host` (IP locale de la borne)
   - `device_id`
   - `local_key`
   - `protocol_version` (`3.3`, `3.4` ou `3.5`, défaut: `3.5`)
   - `charger_profile` (`depow_v2` par défaut, `custom_json` possible)

### Récupérer la `local_key`

Méthode recommandée (TinyTuya + Tuya IoT Cloud):

1. Crée un compte développeur sur https://iot.tuya.com.
2. Crée un projet `Cloud` de type Smart Home.
3. Lie ton compte application Tuya/Smart Life (scan QR depuis l'app).
4. Installe TinyTuya puis lance l'assistant:
   ```bash
   python -m tinytuya wizard
   ```
5. Renseigne `API Key`, `API Secret` et la région du projet Tuya.
6. Récupère `device_id` et `local_key` dans le fichier généré (`devices.json`) ou dans la sortie terminal.

Notes:

- Si tu ré-appaires l'appareil (reset/rebind), la `local_key` peut changer.
- `local_key` est un secret: ne la publie pas.

### Options

- `scan_interval` (secondes): intervalle de rafraîchissement des données.
- `charger_profile`: profil de mapping DP utilisé pour dialoguer avec la borne (`depow_v2`, `generic_v1` ou `custom_json`).
- `charger_profile_json`: mapping DP personnalisé en JSON (optionnel, utilisé si `charger_profile = custom_json`).
  - Exemple minimal:
    ```json
    {
      "metrics": "102",
      "charger_info": "106",
      "work_state": "101",
      "work_state_debug": "109",
      "do_charge": "140",
      "current_target": "150",
      "max_current_cfg": "152",
      "nfc_cfg": "155",
      "downcounter": "108",
      "selftest": "103",
      "alarm": "104",
      "charge_history": "105",
      "adjust_current": "107",
      "product_variant": "157",
      "dp_num": "189",
      "reboot": "142"
    }
    ```
- Mode `surplus solaire` (optionnel):
  - `surplus_mode_enabled`: active la régulation automatique.
  - `surplus_mode`: `classic` ou `zero_injection`.
  - `tariff_mode`: mode tarif dynamique (`disabled`, `hphc`, `tempo`, `spot`).
  - `surplus_sensor_entity_id`: capteur puissance réseau (import/export) en W (sélection via liste déroulante).
  - `surplus_sensor_inverted`: à activer si ton capteur est inversé.
  - `surplus_curtailment_sensor_entity_id`: puissance bridée potentielle (W) (optionnel, surtout en `zero_injection`, sélection via liste déroulante).
  - `surplus_curtailment_sensor_inverted`: inversion du capteur de puissance bridée.
  - `surplus_battery_soc_sensor_entity_id`: capteur de pourcentage batterie (optionnel, liste déroulante).
  - `surplus_forecast_mode_enabled`: active le lissage via prévision solaire.
  - `surplus_dry_run_continuous_enabled`: active le dry-run continu (capteurs de décision simulée).
  - `surplus_departure_mode_enabled`: active l’objectif de départ.
  - `surplus_departure_time`: heure cible de départ (`HH:MM`).
  - `surplus_departure_target_energy_kwh`: énergie cible à délivrer avant l’heure de départ.
  - `surplus_forecast_sensor_entity_id`: capteur externe de prévision surplus/production (W) (optionnel).
  - `tariff_sensor_entity_id`: capteur de label tarifaire (optionnel, surtout `hphc`/`tempo`).
  - `tariff_price_sensor_entity_id`: capteur de prix spot (optionnel, mode `spot`).
  - `tariff_allowed_values`: labels tarifaires autorisés en CSV (ex: `hc,offpeak`).
  - `tariff_max_price_eur_kwh`: seuil max pour autoriser la charge en mode spot.
  - `surplus_battery_soc_threshold_pct`: seuil minimal de batterie pour autoriser la charge via le mode surplus.
  - `surplus_start_threshold_w` / `surplus_stop_threshold_w`: hystérésis démarrage/arrêt.
  - `surplus_target_offset_w`: delta de consigne (marge en W).
  - `surplus_start_delay_s` / `surplus_stop_delay_s`: temporisations anti oscillation.
  - `surplus_adjust_up_cooldown_s`: délai minimal entre deux augmentations de courant.
  - `surplus_adjust_down_cooldown_s`: délai minimal entre deux diminutions de courant.
  - `surplus_ramp_step_a`: pas max (A) par ajustement pour lisser les variations.
  - `surplus_forecast_weight_pct`: poids de la prévision externe dans la décision (%).
  - `surplus_forecast_smoothing_s`: lissage temporel de la consigne basée forecast (s).
  - `surplus_forecast_drop_guard_w`: garde anti-chute pour éviter les réactions trop brutales (W).
  - `surplus_min_run_time_s`: durée minimale d’une session avant arrêt automatique (anti cycles courts).
  - `surplus_max_session_duration_min`: limite de durée max d’une session (0 = désactivé).
  - `surplus_max_session_energy_kwh`: limite d’énergie max d’une session (0 = désactivé).
  - `surplus_max_session_end_time`: heure de fin max d’une session (`HH:MM`, vide = désactivé).
  - `surplus_line_voltage`: tension de référence pour convertir W -> A.

### Configuration Surplus: cas pratiques

1. `surplus_mode = classic` (Shelly / compteur réseau standard)
  - Utilise `surplus_sensor_entity_id` pour la puissance réseau.
  - L'intégration reconstruit automatiquement le surplus réel en tenant compte de la puissance EV interne (`power_l1`).
  - Si `surplus_battery_soc_sensor_entity_id` est défini, la charge ne démarre que si la batterie est >= `surplus_battery_soc_threshold_pct`.
  - `surplus_curtailment_sensor_entity_id` peut rester vide.
2. `surplus_mode = zero_injection` (installation qui bride la production)
  - Garde le même `surplus_sensor_entity_id` réseau.
  - Ajoute `surplus_curtailment_sensor_entity_id` avec la puissance bridée potentielle (si disponible).
  - L'intégration additionne le surplus réseau reconstruit et la puissance bridée potentielle pour fixer la consigne EV.
  - Si `surplus_battery_soc_sensor_entity_id` est défini, la puissance bridée n'est utilisée que lorsque la batterie atteint `surplus_battery_soc_threshold_pct`.
  - Si tu n'as pas ce capteur, le mode fonctionne mais se comportera proche du mode `classic`.

### Mode prévision solaire (forecast)

- Le mode forecast mélange le surplus instantané reconstruit avec un capteur externe de prévision (`surplus_forecast_sensor_entity_id`).
- La pondération est réglée par `surplus_forecast_weight_pct`.
- Un lissage exponentiel (`surplus_forecast_smoothing_s`) est appliqué pour réduire les oscillations sur passages nuageux courts.
- Un garde-fou (`surplus_forecast_drop_guard_w`) limite les baisses brusques de consigne entre deux mesures.
- Deux capteurs debug sont exposés: `surplus_forecast_sensor_power` et `surplus_forecast_blended_power`.

### Objectif départ (HH:MM + kWh)

- Active `surplus_departure_mode_enabled`, définis `surplus_departure_time` et `surplus_departure_target_energy_kwh`.
- L’intégration arbitre automatiquement:
  - priorité au surplus quand disponible,
  - bascule progressive sur un courant garanti si nécessaire pour tenir l’objectif à l’heure cible.
- Le courant garanti estimé est exposé via `surplus_departure_required_current`.
- Le reste à charger est exposé via `surplus_departure_remaining_energy`.

### Mini vue énergie (jour/semaine + efficacité surplus)

- `surplus_energy_today`: énergie chargée aujourd’hui.
- `surplus_energy_week`: énergie chargée sur la semaine ISO en cours.
- `surplus_efficiency_today` / `surplus_efficiency_week`: part estimée de charge couverte par le surplus local (%).

### Pilotage depuis le dashboard Home Assistant

- `switch.surplus_mode`: active/désactive rapidement la régulation surplus.
- `switch.surplus_departure_mode`: active/désactive l’objectif de départ.
- `select.surplus_strategy`: sélectionne `off`, `classic` ou `zero_injection`.
- `select.tariff_mode`: sélectionne le mode tarif (`disabled`, `hphc`, `tempo`, `spot`).
- `select.surplus_departure_time`: choisit l’heure cible de départ (pas de 15 min).
- `number.*surplus*`: ajuste seuils, deltas, délais, cooldown montée/descente, pas de rampe, seuil SOC batterie, protections de session et tension de calcul.
- `number.surplus_departure_target_energy_kwh`: énergie cible à atteindre avant l’heure de départ.
- `number.tariff_max_price_eur_kwh`: ajuste rapidement le seuil prix spot.
- `binary_sensor.surplus_regulation_active`: indique si la régulation pilote activement la charge.

### Diagnostics (support)

- L'intégration expose des diagnostics Home Assistant (`Télécharger les diagnostics`) avec:
  - configuration active et options surplus,
  - snapshot des capteurs surplus configurés,
  - dernière télémétrie de la borne,
  - DPS bruts remontés par le chargeur (utile pour mapping profil).
- Les secrets sensibles (`host`, `device_id`, `local_key`, numéros de série) sont masqués automatiquement.

### Compatibilité chargeurs (profils DP)

- Le client utilise un système de `charger_profile` pour mapper les DPs Tuya.
- Profil par défaut: `depow_v2` (comportement actuel).
- Profil `generic_v1` fourni comme base d'extension pour d'autres firmwares/modèles.
- Cette base permet d'ajouter de nouveaux profils sans modifier toute la logique métier.
- Service `tuya_ev_charger.profile_assistant`: analyse les DPS, suggère un profil et peut l'appliquer.

### Services Home Assistant

- `tuya_ev_charger.force_charge_for`: force une charge temporaire pendant `duration_minutes` (avec `current_a` optionnel).
- `tuya_ev_charger.pause_surplus`: met en pause la régulation surplus pendant `duration_minutes`.
- `tuya_ev_charger.dry_run_surplus`: simule une décision surplus sans action sur la borne (notification + événement bus).
- `tuya_ev_charger.set_surplus_profile`: applique un preset (`balanced`, `aggressive`, `conservative`) + option de mode.
- `tuya_ev_charger.profile_assistant`: génère un rapport de mapping DPS et suggestion de profil.

### Blueprint d'automatisation

- Blueprint inclus: `blueprints/automation/tuya_ev_charger/smart_charge_surplus_tariff.yaml`
- Cas visé: lancer un boost temporaire quand le mode surplus est OFF et que la plage horaire est creuse ou que le prix spot est bas.

### Carte Lovelace “Charge intelligente”

- Carte exemple fournie: `lovelace/charge_intelligente.yaml`
- Contient:
  - presets (`balanced`, `aggressive`, `conservative`),
  - actions rapides (`pause_surplus`, `force_charge_for`, `dry_run_surplus`),
  - états debug surplus + forecast,
  - résumé `charge_history` parsé.
- Remplace les placeholders `<...>` par tes vrais `entity_id`.

### Entités exposées

- `sensor`: mesures électriques, température, état, diagnostics, debug surplus/forecast, dry-run continu, objectif départ (courant/reste), mini vue énergie (jour/semaine/efficacité) et `charge_history` parsé (`timestamp`, `start`, `end`, `duration`, `raw_c`).
- `number`: consigne d'intensité + seuils/deltas/délais/cooldowns/rampe/SOC/tension + protections session + seuil spot.
- `switch`: session de charge, NFC, mode surplus solaire.
- `select`: stratégie surplus solaire (`off` / `classic` / `zero_injection`) + mode tarif.
- `binary_sensor`: état de régulation surplus active.
- `button`: redémarrage borne.

### Détails charge_history

- Le JSON brut `charge_history` est conservé dans `sensor.charge_history`.
- L’intégration expose aussi les champs parsés:
  - `t` -> `sensor.charge_history_timestamp`
  - `s` -> `sensor.charge_history_start_time`
  - `e` -> `sensor.charge_history_end_time`
  - `d` -> `sensor.charge_history_duration_s`
  - `c` -> `sensor.charge_history_raw_c`

### Notes

- Testé uniquement avec la référence: `DE-CHARGEUR-VOITURE-ELECTRIQUE-3KW-V2`.
- Cette intégration utilise une communication locale et n'a pas besoin du cloud Tuya pour les commandes.
- Les identifiants (`device_id`, `local_key`) doivent correspondre à la borne cible.

---

## English

Local Home Assistant integration to control a Tuya EV charger over LAN using `tinytuya`.

### Installation with HACS

1. Open HACS in Home Assistant.
2. Go to `Integrations` then `⋮` > `Custom repositories`.
3. Add this repository URL with category `Integration`.
4. Search for `Tuya EV Charger Local` in HACS and install it.
5. Restart Home Assistant.

### Configuration

1. Go to `Settings` > `Devices & Services` > `Add Integration`.
2. Select `Tuya EV Charger Local`.
3. Enter:
   - `host` (local IP of the charger)
   - `device_id`
   - `local_key`
   - `protocol_version` (`3.3`, `3.4`, or `3.5`, default: `3.5`)
   - `charger_profile` (`depow_v2` by default, `custom_json` supported)

### Get the `local_key`

Recommended method (TinyTuya + Tuya IoT Cloud):

1. Create a developer account at https://iot.tuya.com.
2. Create a Smart Home `Cloud` project.
3. Link your Tuya/Smart Life app account (QR code from the app).
4. Install TinyTuya and run the wizard:
   ```bash
   python -m tinytuya wizard
   ```
5. Provide your Tuya project `API Key`, `API Secret`, and region.
6. Read `device_id` and `local_key` from generated output (`devices.json`) or terminal output.

Notes:

- Re-pairing or resetting the device can rotate the `local_key`.
- Treat `local_key` as a secret and do not publish it.

### Options

- `scan_interval` (seconds): data refresh interval.
- `charger_profile`: DP mapping profile used to communicate with the charger (`depow_v2`, `generic_v1`, or `custom_json`).
- `charger_profile_json`: custom DP mapping JSON (optional, used when `charger_profile = custom_json`).
  - Minimal example:
    ```json
    {
      "metrics": "102",
      "charger_info": "106",
      "work_state": "101",
      "work_state_debug": "109",
      "do_charge": "140",
      "current_target": "150",
      "max_current_cfg": "152",
      "nfc_cfg": "155",
      "downcounter": "108",
      "selftest": "103",
      "alarm": "104",
      "charge_history": "105",
      "adjust_current": "107",
      "product_variant": "157",
      "dp_num": "189",
      "reboot": "142"
    }
    ```
- `solar surplus mode` (optional):
  - `surplus_mode_enabled`: enables automatic regulation.
  - `surplus_mode`: `classic` or `zero_injection`.
  - `tariff_mode`: dynamic tariff mode (`disabled`, `hphc`, `tempo`, `spot`).
  - `surplus_sensor_entity_id`: grid power sensor (import/export) in W (entity dropdown).
  - `surplus_sensor_inverted`: enable if your sensor sign is reversed.
  - `surplus_curtailment_sensor_entity_id`: potential curtailed power (W) (optional, mainly for `zero_injection`, entity dropdown).
  - `surplus_curtailment_sensor_inverted`: invert curtailed power sensor sign.
  - `surplus_battery_soc_sensor_entity_id`: battery SOC percentage sensor (optional, entity dropdown).
  - `surplus_forecast_mode_enabled`: enables solar forecast smoothing.
  - `surplus_dry_run_continuous_enabled`: enables continuous dry-run (simulated decision sensors).
  - `surplus_departure_mode_enabled`: enables departure target mode.
  - `surplus_departure_time`: target departure time (`HH:MM`).
  - `surplus_departure_target_energy_kwh`: target energy to deliver before departure time.
  - `surplus_forecast_sensor_entity_id`: external forecast surplus/production sensor (W) (optional).
  - `tariff_sensor_entity_id`: tariff label sensor (optional, mainly for `hphc`/`tempo`).
  - `tariff_price_sensor_entity_id`: spot price sensor (optional, for `spot` mode).
  - `tariff_allowed_values`: allowed label values as CSV (for example `hc,offpeak`).
  - `tariff_max_price_eur_kwh`: maximum allowed spot price in `spot` mode.
  - `surplus_battery_soc_threshold_pct`: minimum SOC threshold required for surplus charging.
  - `surplus_start_threshold_w` / `surplus_stop_threshold_w`: start/stop hysteresis.
  - `surplus_target_offset_w`: target delta margin (W).
  - `surplus_start_delay_s` / `surplus_stop_delay_s`: anti-flapping delays.
  - `surplus_adjust_up_cooldown_s`: minimum delay between current increases.
  - `surplus_adjust_down_cooldown_s`: minimum delay between current decreases.
  - `surplus_ramp_step_a`: maximum current step (A) per adjustment.
  - `surplus_forecast_weight_pct`: weight of external forecast in decision (%).
  - `surplus_forecast_smoothing_s`: time smoothing applied to forecast blend (s).
  - `surplus_forecast_drop_guard_w`: drop guard to avoid abrupt setpoint decreases (W).
  - `surplus_min_run_time_s`: minimum runtime before automatic stop (anti short-cycling).
  - `surplus_max_session_duration_min`: max session duration (0 = disabled).
  - `surplus_max_session_energy_kwh`: max session energy (0 = disabled).
  - `surplus_max_session_end_time`: latest session end time (`HH:MM`, empty = disabled).
  - `surplus_line_voltage`: reference voltage for W -> A conversion.

### Surplus Configuration: practical cases

1. `surplus_mode = classic` (Shelly / standard grid meter)
  - Use `surplus_sensor_entity_id` for grid power.
  - The integration automatically reconstructs real surplus using internal EV power telemetry (`power_l1`).
  - If `surplus_battery_soc_sensor_entity_id` is configured, charging starts only when SOC is >= `surplus_battery_soc_threshold_pct`.
  - `surplus_curtailment_sensor_entity_id` can stay empty.
2. `surplus_mode = zero_injection` (site with PV curtailment)
  - Keep the same grid `surplus_sensor_entity_id`.
  - Add `surplus_curtailment_sensor_entity_id` with potential curtailed power (if available).
  - The integration adds reconstructed grid surplus and potential curtailed power to compute EV setpoint.
  - If `surplus_battery_soc_sensor_entity_id` is configured, curtailed power is used only when battery SOC reaches `surplus_battery_soc_threshold_pct`.
  - Without that sensor, it still works but behaves close to `classic`.

### Solar forecast mode

- Forecast mode blends instantaneous reconstructed surplus with an external forecast sensor (`surplus_forecast_sensor_entity_id`).
- Blend weight is controlled by `surplus_forecast_weight_pct`.
- Exponential smoothing (`surplus_forecast_smoothing_s`) reduces oscillations during short cloud transitions.
- A drop guard (`surplus_forecast_drop_guard_w`) limits abrupt downward setpoint changes.
- Two debug sensors are exposed: `surplus_forecast_sensor_power` and `surplus_forecast_blended_power`.

### Departure target (HH:MM + kWh)

- Enable `surplus_departure_mode_enabled`, set `surplus_departure_time`, and `surplus_departure_target_energy_kwh`.
- The controller arbitrates automatically:
  - prioritize surplus when available,
  - progressively enforce guaranteed current when needed to hit the target by departure.
- Estimated guaranteed current is exposed via `surplus_departure_required_current`.
- Remaining energy to target is exposed via `surplus_departure_remaining_energy`.

### Mini energy view (day/week + surplus efficiency)

- `surplus_energy_today`: charged energy today.
- `surplus_energy_week`: charged energy for current ISO week.
- `surplus_efficiency_today` / `surplus_efficiency_week`: estimated share covered by local surplus (%).

### Dashboard control entities

- `switch.surplus_mode`: quick on/off for surplus regulation.
- `switch.surplus_departure_mode`: quick on/off for departure target.
- `select.surplus_strategy`: choose `off`, `classic`, or `zero_injection`.
- `select.tariff_mode`: choose tariff mode (`disabled`, `hphc`, `tempo`, `spot`).
- `select.surplus_departure_time`: choose departure target time (15-minute steps).
- `number.*surplus*`: tune thresholds, offsets, delays, up/down cooldowns, ramp step, SOC threshold, session protections, and voltage.
- `number.surplus_departure_target_energy_kwh`: target energy to reach before departure time.
- `number.tariff_max_price_eur_kwh`: tune spot-price threshold.
- `binary_sensor.surplus_regulation_active`: shows whether regulation is actively driving charging.

### Exposed entities

- `sensor`: electrical values, temperature, state, diagnostics, surplus/forecast debug, continuous dry-run, departure target (required current/remaining energy), mini energy view (day/week/efficiency), and parsed `charge_history` (`timestamp`, `start`, `end`, `duration`, `raw_c`).
- `number`: current setpoint + surplus thresholds/offsets/delays/cooldowns/ramp/SOC/voltage + session protections + spot price threshold.
- `switch`: charging session, NFC, solar surplus mode.
- `select`: solar surplus strategy (`off` / `classic` / `zero_injection`) + tariff mode.
- `binary_sensor`: surplus regulation activity.
- `button`: charger reboot.

### charge_history details

- Raw `charge_history` JSON remains available in `sensor.charge_history`.
- Parsed fields are also exposed:
  - `t` -> `sensor.charge_history_timestamp`
  - `s` -> `sensor.charge_history_start_time`
  - `e` -> `sensor.charge_history_end_time`
  - `d` -> `sensor.charge_history_duration_s`
  - `c` -> `sensor.charge_history_raw_c`

### Diagnostics (support)

- The integration exposes Home Assistant diagnostics (`Download diagnostics`) with:
  - active configuration and surplus options,
  - configured surplus sensor snapshots,
  - latest charger telemetry,
  - raw DPS payload (useful for profile mapping).
- Sensitive secrets (`host`, `device_id`, `local_key`, serial identifiers) are automatically redacted.

### Charger compatibility (DP profiles)

- The client now relies on a `charger_profile` DP mapping layer.
- Default profile: `depow_v2` (current behavior).
- `generic_v1` is included as an extension baseline for additional firmwares/models.
- This design allows adding new charger mappings without rewriting core control logic.
- `tuya_ev_charger.profile_assistant` service analyzes DPS and suggests a profile (with optional auto-apply).

### Home Assistant services

- `tuya_ev_charger.force_charge_for`: force charging for `duration_minutes` (optional `current_a`).
- `tuya_ev_charger.pause_surplus`: pause surplus regulation for `duration_minutes`.
- `tuya_ev_charger.dry_run_surplus`: simulate surplus decision without sending commands to the charger (notification + bus event).
- `tuya_ev_charger.set_surplus_profile`: apply a preset (`balanced`, `aggressive`, `conservative`) with optional mode override.
- `tuya_ev_charger.profile_assistant`: generate a DPS mapping report and profile suggestion.

### Automation blueprint

- Included blueprint: `blueprints/automation/tuya_ev_charger/smart_charge_surplus_tariff.yaml`
- Use case: trigger temporary force charge when surplus is OFF and cheap-hours window or spot price condition is met.

### Lovelace “Smart Charging” card

- Example card provided: `lovelace/charge_intelligente.yaml`
- Includes:
  - presets (`balanced`, `aggressive`, `conservative`),
  - quick actions (`pause_surplus`, `force_charge_for`, `dry_run_surplus`),
  - surplus + forecast debug states,
  - parsed `charge_history` summary.
- Replace `<...>` placeholders with your own `entity_id`.

### Notes

- Tested only with reference: `de-portable-ev-charger-3-5kw-v2`.
- This integration uses local communication and does not require Tuya cloud for commands.
- Credentials (`device_id`, `local_key`) must match the target charger.

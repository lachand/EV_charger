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
- Mode `surplus solaire` (optionnel):
  - `surplus_mode_enabled`: active la régulation automatique.
  - `surplus_mode`: `classic` ou `zero_injection`.
  - `surplus_sensor_entity_id`: capteur puissance réseau (import/export) en W.
  - `surplus_sensor_inverted`: à activer si ton capteur est inversé.
  - `surplus_curtailment_sensor_entity_id`: puissance bridée potentielle (W) (optionnel, surtout en `zero_injection`).
  - `surplus_curtailment_sensor_inverted`: inversion du capteur de puissance bridée.
  - `surplus_start_threshold_w` / `surplus_stop_threshold_w`: hystérésis démarrage/arrêt.
  - `surplus_target_offset_w`: delta de consigne (marge en W).
  - `surplus_start_delay_s` / `surplus_stop_delay_s`: temporisations anti oscillation.
  - `surplus_adjust_cooldown_s`: délai minimal entre deux changements d'intensité.
  - `surplus_line_voltage`: tension de référence pour convertir W -> A.

### Configuration Surplus: cas pratiques

1. `surplus_mode = classic` (Shelly / compteur réseau standard)
   - Utilise `surplus_sensor_entity_id` pour la puissance réseau.
   - L'intégration reconstruit automatiquement le surplus réel en tenant compte de la puissance EV interne (`power_l1`).
   - `surplus_curtailment_sensor_entity_id` peut rester vide.
2. `surplus_mode = zero_injection` (installation qui bride la production)
   - Garde le même `surplus_sensor_entity_id` réseau.
   - Ajoute `surplus_curtailment_sensor_entity_id` avec la puissance bridée potentielle (si disponible).
   - L'intégration additionne le surplus réseau reconstruit et la puissance bridée potentielle pour fixer la consigne EV.
   - Si tu n'as pas ce capteur, le mode fonctionne mais se comportera proche du mode `classic`.

### Entités exposées

- `sensor`: mesures électriques, température, état, diagnostics.
- `number`: consigne d'intensité.
- `switch`: session de charge, NFC.
- `button`: redémarrage borne.

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
- `solar surplus mode` (optional):
  - `surplus_mode_enabled`: enables automatic regulation.
  - `surplus_mode`: `classic` or `zero_injection`.
  - `surplus_sensor_entity_id`: grid power sensor (import/export) in W.
  - `surplus_sensor_inverted`: enable if your sensor sign is reversed.
  - `surplus_curtailment_sensor_entity_id`: potential curtailed power (W) (optional, mainly for `zero_injection`).
  - `surplus_curtailment_sensor_inverted`: invert curtailed power sensor sign.
  - `surplus_start_threshold_w` / `surplus_stop_threshold_w`: start/stop hysteresis.
  - `surplus_target_offset_w`: target delta margin (W).
  - `surplus_start_delay_s` / `surplus_stop_delay_s`: anti-flapping delays.
  - `surplus_adjust_cooldown_s`: minimum delay between current setpoint updates.
  - `surplus_line_voltage`: reference voltage for W -> A conversion.

### Surplus Configuration: practical cases

1. `surplus_mode = classic` (Shelly / standard grid meter)
   - Use `surplus_sensor_entity_id` for grid power.
   - The integration automatically reconstructs real surplus using internal EV power telemetry (`power_l1`).
   - `surplus_curtailment_sensor_entity_id` can stay empty.
2. `surplus_mode = zero_injection` (site with PV curtailment)
   - Keep the same grid `surplus_sensor_entity_id`.
   - Add `surplus_curtailment_sensor_entity_id` with potential curtailed power (if available).
   - The integration adds reconstructed grid surplus and potential curtailed power to compute EV setpoint.
   - Without that sensor, it still works but behaves close to `classic`.

### Exposed entities

- `sensor`: electrical values, temperature, state, diagnostics.
- `number`: current setpoint.
- `switch`: charging session, NFC.
- `button`: charger reboot.

### Notes

- Tested only with reference: `de-portable-ev-charger-3-5kw-v2`.
- This integration uses local communication and does not require Tuya cloud for commands.
- Credentials (`device_id`, `local_key`) must match the target charger.

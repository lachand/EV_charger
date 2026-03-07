# EV Charger Python Project

Ce dépôt contient:
- des scripts de rétro-ingénierie/test `tinytuya` (`main.py`, `investigate.py`, `uzzer.py`)
- une intégration Home Assistant locale dans `custom_components/tuya_ev_charger`

## Setup local (scripts Python)

1. Crée un environnement virtuel:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Installe les dépendances:
   ```bash
   pip install -r requirements.txt
   ```

## Intégration Home Assistant

1. Copie `custom_components/tuya_ev_charger` dans le dossier `config/custom_components` de Home Assistant.
2. Redémarre Home Assistant.
3. Va dans `Paramètres > Appareils et services > Ajouter une intégration`.
4. Recherche `Tuya EV Charger Local`.
5. Renseigne:
   - IP locale (`host`)
   - `device_id`
   - `local_key`
   - version protocole (`3.5`)

## Entités créées

- capteurs:
  - tension L1
  - intensité L1
  - puissance L1
  - température
- nombre:
  - consigne d'intensité (DP 150)
- interrupteur:
  - session de charge (DP 140)

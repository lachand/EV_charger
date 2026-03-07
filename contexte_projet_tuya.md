# Contexte de développement : Intégration Home Assistant pour borne de recharge Tuya locale

## Directives pour l'assistant IA
Tu interviens en tant qu'expert en ingénierie logicielle et domotique. Le public cible est de niveau Master 1 en informatique. 
- Utilise systématiquement le typage statique (PEP 8, module `typing`).
- Structure tes explications avec des intentions pédagogiques claires.
- Privilégie une approche orientée objet avec séparation des préoccupations (couche matérielle vs couche Home Assistant).
- N'utilise pas d'émoticônes. 
- Utilise la notation francophone pour les titres (une seule majuscule au début).

## État actuel du projet
Nous développons un composant personnalisé (Custom Component) Home Assistant pour piloter en local une borne de recharge pour véhicule électrique basée sur l'écosystème Tuya. 
La phase de rétro-ingénierie et de fuzzing réseau est terminée. Nous avons validé le dialogue cryptographique local asynchrone via la bibliothèque `tinytuya`.

## Paramètres de l'environnement matériel
- Protocole identifié : Version 3.5 (Crucial pour le typage binaire).
- Adresse IP locale : 192.168.1.238
- Port TCP : 6668
- Device ID : bf23dbbd3d2eb2c804aswb
- Local Key : ~gPc[ep#D{i?^].:

## Cartographie des registres matériels (Data Points - DP) validés
L'ingénierie inverse a permis d'identifier le modèle de données suivant (paradigme asymétrique) :
- **DP 101 (`x_work_state`)** : Entier, état brut de fonctionnement (ex: 101 au repos).
- **DP 102 (`x_metrics`)** : Chaîne sérialisée contenant un JSON avec la télémétrie.
  - Exemple au repos : `{"L1":[2350,0,0],"L2":[2350,0,0],"L3":[2350,0,0],"t":280,"p":0,"d":0,"e":0}`
  - Facteurs d'échelle identifiés : Tension (`L1[0]`) divisée par 10 (ex: 2350 = 235.0V). Température (`t`) divisée par 10 (ex: 280 = 28.0°C).
  - *Action en attente : Les diviseurs pour l'intensité (`L1[1]`) et la puissance (`L1[2]`) doivent encore être calculés via un relevé en charge active.*
- **DP 106 (`x_charger_info`)** : Chaîne JSON contenant des infos statiques (ex: firmware 1.8.2).
- **DP 107 (`x_adjust_current`)** : Chaîne sérialisée affichant les capacités (ex: `"[6, 8, 10, 13, 16]"`).
- **DP 109 (`x_work_st_debug`)** : Enumération textuelle de l'état (ex: "IDLE", "SLEEP").
- **DP 140 (`x_do_charge`)** : Booléen, commutateur présumé pour démarrer/arrêter la session.
- **DP 150 (`x_charge_current`)** : Entier (Type `value`). C'est le registre unifié de commande en lecture/écriture validé par fuzzing pour le protocole 3.5.
- **DP 152 (`x_max_current_cfg`)** : Entier. Limite matérielle statique (16A), en lecture seule dynamique.

## Architecture logicielle planifiée
Le développement doit suivre les standards de Home Assistant :
1. **Couche abstraction (`tuya_ev_charger.py`)** : Classe asynchrone gérant le multiplexage `tinytuya` via `asyncio.to_thread`.
2. **Config Flow (`config_flow.py`)** : Interface utilisateur pour la saisie des identifiants locaux (IP, Device ID, Local Key, Version). Aucune donnée sensible codée en dur.
3. **Coordinateur (`coordinator.py`)** : Implémentation de `DataUpdateCoordinator` pour scruter la borne toutes les 30 secondes et distribuer les métriques du DP 102.
4. **Plateformes (`sensor.py`, `number.py`, `switch.py`)** : 
   - `sensor` pour la tension, puissance, intensité, température (issues du DP 102).
   - `number` pour la consigne (DP 150), avec typage strict en `int` lors de la mutation.
   - `switch` pour le contrôle de session (DP 140).

## Prochaine étape immédiate
La prochaine action de l'utilisateur sera de fournir le dictionnaire JSON d'état (DP 102) capturé pendant que le véhicule charge activement, afin de finaliser l'extraction des diviseurs d'intensité et de puissance dans le DTO `EVMetrics`.
import asyncio
import logging
from typing import Any, Dict, List, Tuple, Optional
import tinytuya  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- PARAMÈTRES D'ENVIRONNEMENT ---
DEVICE_ID: str = "bf23dbbd3d2eb2c804aswb"
LOCAL_IP: str = "192.168.1.238"  # Renseignez l'adresse IP locale
LOCAL_KEY: str = "~gPc[ep#D{i?^].:"
PROTOCOL_VERSION: str = "3.5"

# --- PARAMÈTRES DU TEST ---
# Nous cherchons à modifier la consigne actuelle (qui est à 6) vers 10.
TARGET_AMPERAGE: int = 10
VERIFICATION_DP: str = "150"

# Matrice des cas de test : (Identifiant du registre, Charge utile typée)
TEST_MATRIX: List[Tuple[str, Any]] = [
    # Tests sur le registre d'état (peu probable mais testé pour exhaustivité)
    ("150", TARGET_AMPERAGE),
    ("150", str(TARGET_AMPERAGE)),

    # Tests sur le registre d'ajustement (le plus probable pour une commande)
    ("107", TARGET_AMPERAGE),                      # Type Entier: 10
    ("107", str(TARGET_AMPERAGE)),                 # Type Chaîne: "10"
    ("107", [TARGET_AMPERAGE]),                    # Type Liste d'entiers: [10]
    ("107", f"[{TARGET_AMPERAGE}]"),               # Type Tableau sérialisé: "[10]"
    ("107", [str(TARGET_AMPERAGE)]),               # Type Liste de chaînes: ["10"]
    
    # Tests sur le registre de configuration maximale
    ("152", TARGET_AMPERAGE),
    ("152", str(TARGET_AMPERAGE))
]

async def verify_state(device: tinytuya.Device) -> Optional[int]:
    """Interroge la borne pour lire la consigne d'intensité actuellement active."""
    try:
        payload: Dict[str, Any] = await asyncio.to_thread(device.status)
        if payload and "dps" in payload:
            current_val = payload["dps"].get(VERIFICATION_DP)
            if current_val is not None:
                return int(current_val)
    except Exception as error:
        logger.error(f"Erreur lors de la vérification d'état : {error}")
    return None

async def run_fuzzer() -> None:
    """Orchestre l'exécution itérative de la matrice de test sur le réseau."""
    logger.info("Initialisation de la sonde réseau Tuya...")
    device = tinytuya.Device(
        dev_id=DEVICE_ID,
        address=LOCAL_IP,
        local_key=LOCAL_KEY,
        version=PROTOCOL_VERSION
    )

    logger.info(f"Vérification de l'état initial du DP {VERIFICATION_DP}...")
    initial_state = await verify_state(device)
    logger.info(f"État initial détecté : {initial_state}A")

    if initial_state == TARGET_AMPERAGE:
        logger.warning("La borne est déjà configurée sur l'ampérage cible. Changez TARGET_AMPERAGE.")
        return

    logger.info("Début de l'itération sur la matrice de test.")
    logger.info("-" * 50)

    for dp_id, payload_value in TEST_MATRIX:
        payload_type = type(payload_value).__name__
        logger.info(f"Tentative -> DP: {dp_id} | Type: {payload_type:5} | Valeur: {repr(payload_value)}")
        
        try:
            # Envoi de la commande avec délégation de thread
            response = await asyncio.to_thread(device.set_value, dp_id, payload_value)
            
            # Un retour None signifie un timeout (paquet ignoré par le micrologiciel)
            if response is None:
                logger.warning("  Résultat : Rejet silencieux (Timeout).")
            elif "Error" in response:
                logger.warning(f"  Résultat : Rejet applicatif du micrologiciel -> {response['Error']}")
            else:
                logger.info(f"  Résultat : Acquittement reçu -> {response}")
                
            # Attente pour permettre au processeur d'appliquer le changement
            logger.info("  Attente de 2 secondes pour propagation...")
            await asyncio.sleep(2)
            
            # Vérification de l'application de la consigne
            current_state = await verify_state(device)
            if current_state == TARGET_AMPERAGE:
                logger.info("=" * 50)
                logger.info(f"SUCCÈS CONFIRMÉ ! La consigne a été modifiée avec succès.")
                logger.info(f"Signature d'écriture validée : Registre {dp_id}, Type {payload_type}, Format {repr(payload_value)}")
                logger.info("=" * 50)
                return  # Arrêt du fuzzer dès qu'une solution viable est trouvée
            else:
                logger.info(f"  Vérification : Échec. L'état est toujours à {current_state}A.")
                
        except Exception as e:
            logger.error(f"  Exception critique lors de l'échange réseau : {e}")

        logger.info("-" * 50)
        # Petite pause pour éviter de saturer la pile TCP du microcontrôleur entre deux tests
        await asyncio.sleep(1)

    logger.error("Fin de la matrice. Aucune combinaison n'a permis de modifier la consigne.")

if __name__ == "__main__":
    try:
        asyncio.run(run_fuzzer())
    except KeyboardInterrupt:
        logger.info("Processus d'exploration interrompu.")
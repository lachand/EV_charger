import logging
import json
from typing import Dict, Any, Optional
import tinytuya  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- PARAMÈTRES RÉSEAU VALIDÉS ---
DEVICE_ID: str = "bf23dbbd3d2eb2c804aswb"
LOCAL_IP: str = "192.168.1.238"  
LOCAL_KEY: str = "~gPc[ep#D{i?^].:"
PROTOCOL_VERSION: str = "3.5"

def capturer_etat_charge() -> None:
    """
    Séquence d'interrogation synchrone pour extraire la télémétrie en temps réel.
    """
    logger.info("Instanciation de la sonde réseau Tuya (Protocole 3.5)...")
    
    device = tinytuya.Device(
        dev_id=DEVICE_ID,
        address=LOCAL_IP,
        local_key=LOCAL_KEY,
        version=PROTOCOL_VERSION
    )
    
    # Configuration d'un délai d'attente robuste pour éviter les rejets silencieux
    device.set_socketTimeout(5)

    logger.info(f"Émission de la requête d'état vers l'hôte {LOCAL_IP}...")
    
    try:
        # L'appel bloque le thread jusqu'à la réception de la trame TCP complète
        payload: Optional[Dict[str, Any]] = device.status()
        
        if payload is None:
            logger.error("Délai d'attente expiré (Timeout). Le périphérique est injoignable.")
            return
            
        if "Error" in payload:
            logger.error(f"Le contrôleur signale une erreur de déchiffrement : {payload['Error']}")
            return
            
        logger.info("Télémétrie extraite avec succès. Dictionnaire d'état décodé :")
        # L'affichage indenté permet une lecture aisée des sous-structures JSON
        print(json.dumps(payload, indent=4, sort_keys=True))
            
    except Exception as error:
        logger.error(f"Interruption critique lors de la transaction d'entrée/sortie : {error}")

if __name__ == "__main__":
    capturer_etat_charge()
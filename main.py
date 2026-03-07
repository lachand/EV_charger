import asyncio
import logging
from ev_charger import TuyaEVCharger

# Configuration de la sortie standard pour le terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- PARAMÈTRES À MODIFIER ---
DEVICE_ID = "bf23dbbd3d2eb2c804aswb"
LOCAL_IP = "192.168.1.238" # Indiquer l'IP locale réelle
LOCAL_KEY = "~gPc[ep#D{i?^].:"
# -----------------------------

async def run_terminal_test() -> None:
    """Séquence de validation de la communication matérielle."""
    logger.info("Démarrage de la séquence de test CLI...")
    
    # 1. Instanciation du contrôleur
    charger = TuyaEVCharger(
        device_id=DEVICE_ID,
        ip_address=LOCAL_IP,
        local_key=LOCAL_KEY,
        version="3.5" # Ajuster à "3.4" si le déchiffrement échoue
    )
    
    # 2. Établissement de la session
    await charger.connect()
    
    # 3. Test de lecture (Télémétrie)
    logger.info("--- Test de lecture ---")
    metrics = await charger.get_metrics()
    if metrics:
        logger.info(f"État: {metrics.raw_status}")
        logger.info(f"Tension: {metrics.voltage_l1}V | Intensité: {metrics.current_l1}A")
    else:
        logger.error("Échec de la récupération des métriques.")
        return

    # 4. Test d'écriture (Mutation)
    logger.info("--- Test de mutation ---")
    nouvelle_consigne = 10  # Test avec un entier pour valider notre coercition de type
    success = await charger.set_charge_current(nouvelle_consigne)
    
    if success:
        logger.info("Succès de l'écriture sur le périphérique.")
    else:
        logger.error("Échec de la mutation.")
        return
        
    # 5. Vérification (Seconde lecture)
    logger.info("Attente de 2 secondes pour propagation de l'état...")
    await asyncio.sleep(2)
    metrics_verify = await charger.get_metrics()
    if metrics_verify:
         logger.info(f"Nouvel état consolidé: {metrics_verify.raw_status}")

if __name__ == "__main__":
    try:
        # Lancement de la boucle d'événements
        asyncio.run(run_terminal_test())
    except KeyboardInterrupt:
        logger.info("Exécution interrompue par l'utilisateur.")
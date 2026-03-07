import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, Union

import tinytuya  # type: ignore

logger = logging.getLogger(__name__)

ALLOWED_CURRENTS = ("6", "8", "10", "13", "16")

@dataclass
class EVMetrics:
    voltage_l1: float
    current_l1: float
    power_l1: float
    temperature: float
    raw_status: str

class TuyaEVCharger:
    DP_WORK_STATE = "109"
    DP_METRICS = "102"
    DP_DO_CHARGE = "140"
    DP_CURRENT_TARGET = "150"
    DP_MAX_CURRENT_CFG = "152"

    def __init__(self, device_id: str, ip_address: str, local_key: str, version: str = "3.5") -> None:
        self.device_id = device_id
        self.ip_address = ip_address
        self.local_key = local_key
        self.version = version
        # Changement du typage : utilisation du composant synchrone officiel
        self._device: Optional[tinytuya.Device] = None 

    async def connect(self) -> None:
        """
        Instancie l'objet matériel. Cette opération est purement logicielle et ne
        déclenche pas d'E/S bloquantes, elle peut donc rester directement dans la coroutine.
        """
        self._device = tinytuya.Device(
            dev_id=self.device_id,
            address=self.ip_address,
            local_key=self.local_key,
            version=self.version
        )
        logger.info("Pont matériel instancié via la classe standard.")

    async def _send_command(self, dp_id: str, value: Any) -> bool:
        if self._device is None:
            raise ConnectionError("Périphérique non connecté.")
            
        try:
            # Délégation de la méthode bloquante vers le pool de threads de l'interpréteur
            response: Dict[str, Any] = await asyncio.to_thread(
                self._device.set_value, dp_id, value
            )
            
            if response and "Error" not in response:
                return True
            logger.error(f"Rejet sur le DP {dp_id} : {response}")
            return False
            
        except Exception as error:
            logger.error(f"Erreur d'entrée/sortie réseau : {error}")
            return False

    async def set_charge_current(self, amperage: Union[int, str]) -> bool:
        """
        Modifie la configuration de l'intensité maximale allouée.
        """
        # Étape 1 : Validation stricte via la chaîne de caractères (Règle métier)
        sanitized_amperage_str = str(amperage)
        if sanitized_amperage_str not in ALLOWED_CURRENTS:
            raise ValueError(f"Consigne '{sanitized_amperage_str}' non supportée.")
        
        # Étape 2 : Coercition inverse en type primitif entier pour le socket local
        payload_amperage: int = int(amperage)

        logger.info(f"Émission consigne : {payload_amperage}A (typée en int pour le matériel)")
        
        # Émission avec le type entier attendu par le firmware (registre de consigne validé)
        return await self._send_command(self.DP_CURRENT_TARGET, payload_amperage)

    async def get_metrics(self) -> Optional[EVMetrics]:
        if self._device is None:
            raise ConnectionError("Périphérique non connecté.")
            
        try:
            # Délégation de l'interrogation d'état vers le pool de threads
            payload: Dict[str, Any] = await asyncio.to_thread(self._device.status)
            
            if not payload or "Error" in payload:
                return None

            dps = payload.get("dps", {})
            work_state: str = dps.get(self.DP_WORK_STATE, "UNKNOWN")
            
            raw_metrics_str: str = dps.get(self.DP_METRICS, "{}")
            metrics_dict: Dict[str, Any] = json.loads(raw_metrics_str)

            l1_data = metrics_dict.get("L1", [0, 0, 0])
            voltage = float(l1_data[0]) / 10.0
            current = float(l1_data[1]) / 10.0
            power = float(l1_data[2]) / 10.0
            temperature = float(metrics_dict.get("t", 0)) / 10.0

            return EVMetrics(voltage, current, power, temperature, work_state)
            
        except Exception as parse_error:
            logger.error(f"Erreur de parsing ou d'exécution de thread : {parse_error}")
            return None

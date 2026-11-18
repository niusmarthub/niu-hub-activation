
"""Lecture Bluetooth ECO-WORTHY MPPT - deux plages Modbus."""

import asyncio
import logging

from bleak import BleakClient

from homeassistant.components import bluetooth
from homeassistant.components.sensor import SensorEntity

_LOGGER = logging.getLogger(__name__)

FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"
FFF2 = "0000fff2-0000-1000-8000-00805f9b34fb"

# Fonction Modbus 03 : lecture uniquement
REQUEST_MEASURES = bytes.fromhex("01 03 00 00 00 0F 05 CE")
REQUEST_LOAD = bytes.fromhex("01 03 04 01 00 0F 55 3E")


def valid_response(data):
    """Valider une réponse Modbus et son CRC."""
    if len(data) < 5:
        return False

    if data[:3] != b"\x01\x03\x1e":
        return False

    if len(data) != 3 + data[2] + 2:
        return False

    crc = 0xFFFF
    for byte in data[:-2]:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

    return data[-2:] == crc.to_bytes(2, "little")


def register_value(data, start_address, address):
    """Lire un registre 16 bits dans une réponse."""
    index = 3 + (address - start_address) * 2
    return int.from_bytes(data[index:index + 2], "big")


REGISTER_NOTES = {
    0x0001: "Tension PV probable",
    0x0002: "Tension batterie",
    0x0005: "Tension de sortie possible",
    0x0006: "Courant de sortie probable",
    0x0007: "Tension de sortie selon le code actuel, à vérifier",
    0x0008: "Puissance affichée dans l’appli, origine à vérifier",
    0x0009: "Température selon le code actuel, à vérifier",
    0x000A: "Température possible",
    0x040F: "État LOAD possible",
}


def diagnostic_registers(measures, load):
    """Attribuer un numéro fixe à chaque registre des deux réponses validées."""
    rows = []
    for block, start, frame, first_number in (
        ("L01", 0x0000, measures, 1),
        ("L02", 0x0401, load, 16),
    ):
        for offset in range(frame[2] // 2):
            address = start + offset
            raw = register_value(frame, start, address)
            rows.append({
                "numero": first_number + offset,
                "bloc": block,
                "adresse": f"0x{address:04X}",
                "decimale": raw,
                "hexadecimale": f"0x{raw:04X}",
                "repere": REGISTER_NOTES.get(address, "Non identifié"),
            })
    return rows


class EcoWorthyHub:
    """Une connexion Bluetooth partagée entre tous les capteurs."""

    def __init__(self, hass):
        self.hass = hass
        self.entities = []
        self.values = {}
        self.lock = asyncio.Lock()

    async def update(self):
        async with self.lock:
            await self._read()

    async def _read(self):
        devices = bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        )

        matches = [
            item for item in devices
            if "MPPT-BT02" in (item.name or "").upper()
        ]

        if len(matches) != 1 and not any(item.address.upper() == "40:D6:3C:57:75:DD" for item in matches):
            _LOGGER.warning(
                "ECO-WORTHY : %s dongle(s) détecté(s)",
                len(matches),
            )
            return

        device = bluetooth.async_ble_device_from_address(
            self.hass, next((item.address for item in matches if item.address.upper() == "40:D6:3C:57:75:DD"), matches[0].address), connectable=True
        )

        if device is None:
            _LOGGER.warning("ECO-WORTHY : dongle inaccessible")
            return

        queue = asyncio.Queue()

        def on_notification(sender, data):
            queue.put_nowait(bytes(data))

        try:
            async with BleakClient(device, timeout=15) as client:
                await client.start_notify(FFF1, on_notification)

                responses = []

                for request in (REQUEST_MEASURES, REQUEST_LOAD):
                    # Vider les anciennes notifications.
                    while not queue.empty():
                        queue.get_nowait()

                    await client.write_gatt_char(
                        FFF2, request, response=False
                    )

                    response = await asyncio.wait_for(
                        queue.get(), timeout=8
                    )

                    if not valid_response(response):
                        raise ValueError(
                            "Réponse Modbus invalide : "
                            + response.hex(" ")
                        )

                    responses.append(response)

                    await asyncio.sleep(0.3)

                await client.stop_notify(FFF1)

            measures, load = responses

            self.values = {
                "frame": measures.hex(" "),
                "load_frame": load.hex(" "),
                "diagnostic_registers": diagnostic_registers(measures, load),
                "battery_voltage": register_value(
                    measures, 0x0000, 0x0002
                ) / 10,
                "load_voltage": register_value(
                    measures, 0x0000, 0x0007
                ) / 10,
                "temperature": register_value(
                    measures, 0x0000, 0x0009
                ),
                "load_state": (
                    "ON"
                    if register_value(load, 0x0401, 0x040F) == 1
                    else "OFF"
                ),
            }

            for entity in self.entities:
                entity.async_write_ha_state()

        except Exception as err:
            _LOGGER.warning(
                "ECO-WORTHY : lecture Bluetooth échouée : %s",
                err,
            )


async def async_setup_platform(
    hass, config, async_add_entities, discovery_info=None
):
    hub = EcoWorthyHub(hass)

    entities = [
        EcoWorthyRegisterDiagnostic(hub),
        EcoWorthySensor(
            hub, "frame", "ECO-WORTHY Trame Modbus",
            "eco_worthy_modbus_frame"
        ),
        EcoWorthySensor(
            hub, "battery_voltage", "ECO-WORTHY Batterie Tension",
            "eco_worthy_battery_voltage", "V", "voltage"
        ),
        EcoWorthySensor(
            hub, "load_voltage", "ECO-WORTHY LOAD Tension",
            "eco_worthy_load_voltage", "V", "voltage"
        ),
        EcoWorthySensor(
            hub, "temperature", "ECO-WORTHY Temperature Sonde",
            "eco_worthy_battery_temperature", "°C", "temperature"
        ),
        EcoWorthySensor(
            hub, "load_state", "ECO-WORTHY LOAD Etat",
            "eco_worthy_load_state"
        ),
    ]

    hub.entities = entities
    async_add_entities(entities, True)


class EcoWorthySensor(SensorEntity):
    """Capteur alimenté par la lecture Bluetooth partagée."""

    _attr_should_poll = False

    def __init__(
        self, hub, key, name, unique_id,
        unit=None, device_class=None
    ):
        self.hub = hub
        self.key = key
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

    @property
    def native_value(self):
        return self.hub.values.get(self.key)

    @property
    def available(self):
        return self.key in self.hub.values

    async def async_added_to_hass(self):
        """Démarrer une seule boucle de lecture pour le hub."""
        if self.key != "frame":
            return

        async def polling_loop():
            while True:
                await self.hub.update()
                await asyncio.sleep(5)

        self._polling_task = asyncio.create_task(
            polling_loop(),
            name="eco_worthy_polling"
        )

    async def async_will_remove_from_hass(self):
        """Arreter proprement la lecture Bluetooth."""
        await super().async_will_remove_from_hass()
        task = getattr(self, "_polling_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class EcoWorthyRegisterDiagnostic(EcoWorthySensor):
    """Tableau de la dernière lecture valide, sans interrogation supplémentaire."""

    def __init__(self, hub):
        super().__init__(
            hub,
            "diagnostic_registers",
            "ECO-WORTHY Registres Modbus",
            "eco_worthy_modbus_registers",
        )

    @property
    def native_value(self):
        return len(self.hub.values[self.key]) if self.available else None

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        return {
            "registres": self.hub.values[self.key],
            "trame_l01": self.hub.values["frame"],
            "trame_l02": self.hub.values["load_frame"],
        }

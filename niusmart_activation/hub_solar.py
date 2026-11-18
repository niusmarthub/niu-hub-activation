"""Read the ECO-WORTHY MPPT over Bluetooth and publish its measurements."""

import asyncio

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
REQUEST_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
REQUEST_MEASURES = bytes.fromhex("01 03 00 00 00 0F 05 CE")
REQUEST_LOAD = bytes.fromhex("01 03 04 01 00 0F 55 3E")

SENSORS = {
    "battery_voltage": ("Tension batterie", "V", "voltage"),
    "load_voltage": ("Tension sortie LOAD", "V", "voltage"),
    "temperature": ("Température sonde", "°C", "temperature"),
    "load_state": ("État sortie LOAD", None, None),
}


def registers(frame):
    if len(frame) != 35 or frame[:3] != b"\x01\x03\x1e":
        raise ValueError("Format de réponse Modbus invalide.")
    crc = 0xFFFF
    for byte in frame[:-2]:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    if frame[-2:] != crc.to_bytes(2, "little"):
        raise ValueError("CRC Modbus invalide.")
    return [int.from_bytes(frame[i:i + 2], "big") for i in range(3, 33, 2)]


async def read_solar(address):
    from bleak import BleakClient, BleakScanner

    discovered = await BleakScanner.discover(timeout=5, return_adv=True)
    matches = [
        device for device, adv in discovered.values()
        if "MPPT-BT02" in (adv.local_name or device.name or "").upper()
        and (not address or device.address.upper() == address.upper())
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Plusieurs adaptateurs MPPT-BT02 détectés. Renseignez solar_bluetooth_address."
            if matches else "Adaptateur MPPT-BT02 introuvable."
        )

    queue = asyncio.Queue()

    def on_notification(_sender, data):
        queue.put_nowait(bytes(data))

    async with BleakClient(matches[0], timeout=15) as client:
        await client.start_notify(NOTIFY_UUID, on_notification)
        responses = []
        for request in (REQUEST_MEASURES, REQUEST_LOAD):
            while not queue.empty():
                queue.get_nowait()
            await client.write_gatt_char(REQUEST_UUID, request, response=False)
            responses.append(registers(await asyncio.wait_for(queue.get(), timeout=8)))
            if request == REQUEST_MEASURES:
                await asyncio.sleep(0.3)

    measures, load = responses
    return {
        "battery_voltage": measures[2] / 10,
        "load_voltage": measures[5] / 10,
        "temperature": measures[9],
        "load_state": {0: "OFF", 1: "ON"}.get(load[14], "Inconnu"),
    }, measures, load


def direct_snapshot(values, measures, load):
    """Adapt the add-on's Bluetooth reader to the shared solar publisher."""
    states = [
        {"entity_id": f"sensor.eco_worthy_{key}", "state": str(values[key]),
         "attributes": {"friendly_name": f"ECO-WORTHY {key.replace('_', ' ')}",
                        **({"unit_of_measurement": unit, "device_class": kind} if unit else {})}}
        for key, (_, unit, kind) in SENSORS.items()
    ]
    registers = []
    for block, start, raw_values, first in (("L01", 0, measures, 1), ("L02", 0x0401, load, 16)):
        for offset, raw in enumerate(raw_values):
            address = start + offset
            registers.append({"numero": first + offset, "bloc": block, "adresse": f"0x{address:04X}",
                              "decimale": raw, "hexadecimale": f"0x{raw:04X}",
                              "repere": {2: "Tension batterie", 5: "Tension de sortie utilisée par Niu Hub",
                                         9: "Température selon le code actuel, à vérifier",
                                         0x040F: "État LOAD possible"}.get(address, "Non identifié")})
    return states, registers

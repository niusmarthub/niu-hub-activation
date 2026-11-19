"""Read the ECO-WORTHY MPPT over Bluetooth and publish its measurements."""

import asyncio
import json
import re
from datetime import datetime

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
REQUEST_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
REQUEST_MEASURES = bytes.fromhex("01 03 00 00 00 0F 05 CE")
REQUEST_LOAD = bytes.fromhex("01 03 04 01 00 0F 55 3E")

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


def legacy_measures(root, states, block="l01"):
    """Read a validated Modbus frame from the installed Home Assistant driver."""
    registry = root / ".storage" / "core.entity_registry"
    entries = json.loads(registry.read_text())["data"]["entities"] if registry.is_file() else []
    mqtt_ids = {entry["entity_id"] for entry in entries if entry.get("platform") == "mqtt"}
    candidates = [("registres_modbus", f"trame_{block}")]
    if block == "l01":
        candidates.append(("trame_modbus", None))
    for source, attribute in candidates:
        for item in states:
            entity_id = item["entity_id"]
            if entity_id in mqtt_ids or not re.fullmatch(rf"sensor\.eco_worthy_{source}(?:_[0-9]+)?", entity_id):
                continue
            state = item["state"]
            if state in ("unavailable", "unknown"):
                continue
            frame = item.get("attributes", {}).get(attribute) if attribute else state
            if isinstance(frame, str):
                try:
                    return registers(bytes.fromhex(frame))
                except ValueError:
                    continue
    return None


def recently_reported(states, now):
    """Use Home Assistant's report time to exclude stale Bluetooth states."""
    for item in states:
        reported_at = item.get("last_reported") or item.get("last_updated")
        if not isinstance(reported_at, str):
            continue
        try:
            reported = datetime.fromisoformat(reported_at)
        except ValueError:
            continue
        if reported.tzinfo is not None and -5 <= now - reported.timestamp() <= 20:
            yield item


def web_solar_readings(status):
    readings = {}
    for key, row in status.items():
        value = row["value"]
        if value is None:
            continue
        if key == "load_state":
            readings[key] = value
        elif key == "pv_energy_total":
            readings[key] = int(value)
        else:
            readings[key] = float(value)
    return readings


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

    return tuple(responses)


def direct_diagnostic_rows(measures, load):
    notes = {
        0x0001: "Tension PV",
        0x0002: "Tension batterie",
        0x0006: "Courant de sortie LOAD",
        0x0008: "Puissance de sortie LOAD",
        0x0009: "Température de la sonde",
    }
    registers = []
    for block, start, raw_values, first in (("L01", 0, measures, 1), ("L02", 0x0401, load, 16)):
        for offset, raw in enumerate(raw_values):
            address = start + offset
            registers.append({"numero": first + offset, "bloc": block, "adresse": f"0x{address:04X}",
                              "decimale": raw, "hexadecimale": f"0x{raw:04X}",
                              "repere": notes.get(address, "Non identifié")})
    return registers

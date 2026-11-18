"""Read only the Tuya inventory and publish supported measurements."""

import json
import re
from ipaddress import IPv4Address
from math import isfinite

import tinytuya

from hub_mqtt import mqtt_service, publish_samples


# PJ1103B (product ID bbcg1hrkrj5rifsd). The scale applies to the local DP value.
ENERGY_PRODUCT = "bbcg1hrkrj5rifsd"
ENERGY_PROFILE = {
    "101": ("Puissance A", "W", 10, "power"),
    "102": ("Sens A", None, 1, None),
    "104": ("Sens B", None, 1, None),
    "105": ("Puissance B", "W", 10, "power"),
    "106": ("Énergie directe A", "kWh", 100, "energy"),
    "107": ("Énergie inverse A", "kWh", 100, "energy"),
    "108": ("Énergie directe B", "kWh", 100, "energy"),
    "109": ("Énergie inverse B", "kWh", 100, "energy"),
    "110": ("Facteur de puissance A", None, 100, None),
    "111": ("Fréquence", "Hz", 100, "frequency"),
    "112": ("Tension", "V", 10, "voltage"),
    "113": ("Courant A", "mA", 1, "current"),
    "114": ("Courant B", "mA", 1, "current"),
    "115": ("Puissance totale", "W", 10, "power"),
    "121": ("Facteur de puissance B", None, 100, None),
    "130": ("Énergie directe totale", "kWh", 100, "energy"),
    "131": ("Énergie inverse totale", "kWh", 100, "energy"),
}
ENERGY_SETTINGS = {
    "116": ("Étalonnage de la tension", None, 1000, None),
    "117": ("Étalonnage du courant A", None, 1000, None),
    "118": ("Étalonnage de la puissance A", None, 1000, None),
    "119": ("Étalonnage de l’énergie directe A", None, 1000, None),
    "122": ("Étalonnage de la fréquence", None, 1000, None),
    "123": ("Étalonnage du courant B", None, 1000, None),
    "124": ("Étalonnage de la puissance B", None, 1000, None),
    "125": ("Étalonnage de l’énergie directe B", None, 1000, None),
    "127": ("Étalonnage de l’énergie inverse A", None, 1000, None),
    "128": ("Étalonnage de l’énergie inverse B", None, 1000, None),
    "129": ("Intervalle de remontée", "s", 1, None),
}
ENERGY_UNITS = {
    "power": {"W": "W", "w": "W", "kW": "kW", "kw": "kW", "KW": "kW", "mW": "mW", "MW": "MW"},
    "energy": {"wh": "Wh", "kwh": "kWh", "mwh": "MWh"},
    "current": {"ma": "mA", "a": "A"},
    "voltage": {"mv": "mV", "v": "V", "kv": "kV"},
    "frequency": {"hz": "Hz", "khz": "kHz"},
}
ENERGY_CODES = {
    "power": {"cur_power", "power", "total_power", "power_a", "power_b", "power_live"},
    "energy": {"add_ele", "energy_total", "forward_energy_total", "reverse_energy_total",
               "total_forward_energy", "total_reverse_energy", "energy_line_1", "energy_line_2"},
    "current": {"cur_current", "current", "current_a", "current_b", "current_live",
                "current_line_1", "current_line_2"},
    "voltage": {"cur_voltage", "voltage", "voltage_a", "voltage_b", "voltage_live"},
    "frequency": {"freq", "frequency", "ac_frequency", "frequency_live"},
}
DP_ID = re.compile(r"[1-9][0-9]{0,4}")
RELAY_CODE = re.compile(r"(?:switch|relay)(?:_[1-9][0-9]*)?")
TOPIC_ID = re.compile(r"[a-zA-Z0-9_-]+")


def read_datapoints(module, endpoint):
    ip = str(IPv4Address(endpoint["ip"]))
    version = endpoint.get("version")
    if version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
        return None
    device = tinytuya.Device(module["tuya_device_id"], ip, module["local_key"], version=float(version))
    device.set_socketTimeout(3)
    device.set_socketRetryLimit(1)
    response = device.status()
    values = response.get("dps") if isinstance(response, dict) else None
    return values if isinstance(values, dict) and values else None


def declared_measurement(item, code, label):
    """Use explicit units and scale from the inventory when available."""
    unit, scale = item.get("unit"), item.get("scale")
    if not isinstance(unit, str) or type(scale) is not int or not 0 <= scale <= 9:
        return None
    device_class = item.get("device_class")
    if not isinstance(device_class, str):
        device_class = next((key for key, codes in ENERGY_CODES.items() if code in codes), "")
    units = ENERGY_UNITS.get(device_class, {})
    unit = unit.strip()
    unit = units.get(unit) if device_class == "power" else units.get(unit.lower())
    if not unit:
        return None
    return label, unit, 10**scale, device_class


def public_datapoints(module, values):
    known = {}
    for item in module.get("datapoints") or []:
        if not isinstance(item, dict):
            continue
        dp_id = str(item.get("dp_id", ""))
        if DP_ID.fullmatch(dp_id):
            known[dp_id] = item
    profile = ENERGY_PROFILE | ENERGY_SETTINGS if module.get("product_id") == ENERGY_PRODUCT else {}
    values = {str(key): value for key, value in (values or {}).items() if DP_ID.fullmatch(str(key))}
    rows = []
    for dp_id in sorted(known.keys() | values.keys() | profile.keys(), key=int):
        item = known.get(dp_id, {})
        code = item.get("status_code") or item.get("name")
        code = code.strip().lower() if isinstance(code, str) else ""
        label = item.get("name") or code or f"DP {dp_id}"
        raw = values.get(dp_id)
        value, unit, device_class = raw, None, None
        declared = declared_measurement(item, code, label)
        builtin_measurement = module.get("product_id") == ENERGY_PRODUCT and dp_id in ENERGY_PROFILE
        eligible = declared is not None or builtin_measurement
        measure = declared or profile.get(dp_id)
        if measure:
            label, unit, divisor, device_class = measure
            if divisor != 1 and (type(raw) is int or type(raw) is float and isfinite(raw)):
                value = raw / divisor
        relay = bool(RELAY_CODE.fullmatch(code)) and (raw is None or type(raw) is bool)
        if device_class == "energy":
            state_class = item.get("state_class") if declared else "total_increasing"
            if state_class not in ("total", "total_increasing"):
                state_class = None
        else:
            state_class = "measurement"
        rows.append({
            "id": dp_id, "name": label, "value": value, "raw": raw, "unit": unit,
            "relay_candidate": relay, "device_class": device_class, "energy_measurement": eligible,
            "state_class": state_class,
        })
    return rows


def snapshot_modules(feed, endpoints, on_read_error):
    modules = []
    for module in feed["modules"]:
        device_id = module["tuya_device_id"]
        endpoint = endpoints[device_id]
        values = None
        # An old address may now belong to another device. Use a current discovery.
        if endpoint["discovered"]:
            try:
                values = read_datapoints(module, endpoint)
            except Exception as exc:
                on_read_error(exc)
        modules.append({
            "id": device_id, "name": module.get("device_name") or device_id,
            "product": module.get("product_name"), "product_id": module.get("product_id"),
            "ip": endpoint["ip"], "protocol": endpoint["version"],
            "seen": endpoint["discovered"], "read": values is not None,
            "datapoints": public_datapoints(module, values),
        })
    return modules


def publish_energy(hub_id, modules, expire_after):
    if not TOPIC_ID.fullmatch(hub_id):
        raise ValueError("Identifiant de sujet MQTT invalide")
    messages = []
    for module in modules:
        device_id = module["id"]
        if not TOPIC_ID.fullmatch(device_id):
            continue
        for dp in module["datapoints"]:
            if not dp["energy_measurement"]:
                continue
            value = dp["value"]
            if type(value) not in (str, int, float) or (type(value) is float and not isfinite(value)):
                continue
            device_class = dp["device_class"]
            if device_class is None and dp["id"] in ("102", "104"):
                if value not in ("FORWARD", "REVERSE"):
                    continue
            elif type(value) not in (int, float):
                continue
            unique_id = f"niusmart_{device_id}_dp{dp['id']}"
            state_topic = f"niusmart/{hub_id}/tuya/{device_id}/{dp['id']}/state"
            config = {
                "name": dp["name"], "unique_id": unique_id, "state_topic": state_topic,
                "device": {"identifiers": [f"niusmart_tuya_{device_id}"], "name": module["name"]},
                "expire_after": expire_after,
            }
            if dp["unit"]:
                config["unit_of_measurement"] = dp["unit"]
            if device_class:
                config["device_class"] = device_class
            if type(value) in (int, float) and dp["state_class"]:
                config["state_class"] = dp["state_class"]
            messages.extend((
                (f"homeassistant/sensor/{unique_id}/config", json.dumps(config), True),
                (state_topic, str(value), False),
            ))
    if messages:
        publish_samples(mqtt_service(), messages)
    return len(messages) // 2

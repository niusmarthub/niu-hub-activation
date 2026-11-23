"""Read and control local Tuya datapoints through MQTT."""

import json
import re
from ipaddress import IPv4Address
from math import isfinite
from time import monotonic

import tinytuya

from hub_mqtt import mqtt_service, publish_samples, relay_hub_availability


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
RELAY_CODE = re.compile(r"(?:switch|relay)(?:_[0-9]+)?")
TOPIC_ID = re.compile(r"[a-zA-Z0-9_-]+")
RELAY_COMMAND_MAX_AGE = 15


def local_device(module, endpoint):
    ip = str(IPv4Address(endpoint["ip"]))
    version = endpoint.get("version")
    if version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
        return None
    device = tinytuya.Device(module["tuya_device_id"], ip, module["local_key"], version=float(version))
    device.set_socketTimeout(3)
    device.set_socketRetryLimit(1)
    return device


def device_datapoints(device):
    response = device.status()
    values = response.get("dps") if isinstance(response, dict) else None
    return values if isinstance(values, dict) and values else None


def read_datapoints(module, endpoint):
    device = local_device(module, endpoint)
    return device_datapoints(device) if device is not None else None


def set_confirmed_relay(module, endpoint, dp_id, desired):
    """Apply an explicit state to a relay already mapped to this module and DP."""
    if type(desired) is not bool or not isinstance(dp_id, str) or not DP_ID.fullmatch(dp_id):
        raise ValueError("Commande relais invalide")
    if not endpoint.get("discovered") and endpoint.get("ip_source") != "manual":
        raise RuntimeError("Module relais non détecté")
    device = local_device(module, endpoint)
    if device is None:
        raise RuntimeError("Protocole du relais non pris en charge")
    current = (device_datapoints(device) or {}).get(dp_id)
    if type(current) is not bool:
        raise RuntimeError("État du relais indisponible")
    if current is desired:
        return current

    device.set_status(desired, switch=int(dp_id))
    confirmed = (device_datapoints(device) or {}).get(dp_id)
    if confirmed is not desired:
        raise RuntimeError("Le relais n’a pas confirmé l’état demandé")
    return confirmed


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
        relay = not measure and bool(RELAY_CODE.fullmatch(code))
        if device_class == "energy":
            state_class = item.get("state_class") if declared else "total_increasing"
            if state_class not in ("total", "total_increasing"):
                state_class = None
        else:
            state_class = "measurement"
        rows.append({
            "id": dp_id, "name": label, "value": value, "raw": raw, "unit": unit,
            "relay_candidate": relay, "relay_channel": int(code.rsplit("_", 1)[-1])
            if relay and "_" in code else 1 if relay else None, "device_class": device_class, "energy_measurement": eligible,
            "state_class": state_class,
        })
    return rows


def snapshot_modules(feed, endpoints, on_read_error, *, executor=None, cloud_states=None):
    def snapshot(module):
        device_id = module["tuya_device_id"]
        endpoint = endpoints[device_id]
        values = None
        # An old address may now belong to another device. Use a current discovery.
        if endpoint["discovered"] or endpoint.get("ip_source") == "manual":
            try:
                values = read_datapoints(module, endpoint)
            except Exception as exc:
                on_read_error(exc)
        source = "local" if values is not None else "unavailable"
        if values is None and cloud_states and device_id in cloud_states:
            values = cloud_states[device_id]
            source = "tuya_cloud"
        return {
            "source": source,
            "id": device_id, "name": module.get("device_name") or device_id,
            "product": module.get("product_name"), "product_id": module.get("product_id"),
            "ip": endpoint["ip"], "protocol": endpoint["version"],
            "seen": endpoint["discovered"], "read": values is not None,
            "manual_ip": endpoint.get("manual_ip"), "ip_source": endpoint.get("ip_source"),
            "datapoints": public_datapoints(module, values),
        }

    read = executor.map if executor is not None else map
    return list(read(snapshot, feed["modules"]))


def apply_relay_commands(feed, endpoints, modules, commands, executor, is_current, on_error):
    if not commands:
        return
    pending = {}
    for device_id, dp_id, desired, received in commands:
        pending.setdefault(device_id, {})[dp_id] = (desired, received)
    inventory = {module["tuya_device_id"]: module for module in feed["modules"]}

    def apply(module):
        device_id = module["id"]
        requested = pending.get(device_id)
        if not requested or module.get("source", "local") != "local" or not (module["seen"] or module.get("ip_source") == "manual") or not module["read"]:
            return
        for dp in module["datapoints"]:
            request = requested.get(dp["id"])
            if not request or not dp["relay_candidate"] or type(dp["raw"]) is not bool:
                continue
            desired, received = request
            if monotonic() - received > RELAY_COMMAND_MAX_AGE or not is_current():
                continue
            try:
                confirmed = set_confirmed_relay(inventory[device_id], endpoints[device_id], dp["id"], desired)
                dp["raw"] = dp["value"] = confirmed
            except Exception as exc:
                on_error(exc)

    list(executor.map(apply, (module for module in modules if module["id"] in pending)))


def publish_module_states(hub_id, modules, expire_after, *, energy_enabled, sent=None, refresh_after=30,
                          tracked_switches=None, save_switches=None):
    if not TOPIC_ID.fullmatch(hub_id):
        raise ValueError("Identifiant de sujet MQTT invalide")
    messages = []
    counts = {'energy': 0, 'other': 0}
    retired_topics = set()
    tracked_switches = tracked_switches if tracked_switches is not None else set()
    active_switches = set()
    published_datapoints = []
    relay_number = 0
    for module in modules:
        device_id = module["id"]
        if not TOPIC_ID.fullmatch(device_id):
            continue
        if module["product_id"] == ENERGY_PRODUCT:
            # The old diagnostic used a different entity ID and retained its raw state.
            retired_topics.update((
                f"homeassistant/sensor/niusmart_{device_id}_test_dp101/config",
                f"niusmart/{hub_id}/tuya/energy/test/101/state",
            ))
        relay_names = {}
        relay_channels = sorted(
            (dp for dp in module["datapoints"] if dp["relay_candidate"]),
            key=lambda dp: (dp.get("relay_channel") or int(dp["id"]), int(dp["id"])),
        )
        for dp in relay_channels:
            relay_number += 1
            relay_names[dp["id"]] = f"Switch {relay_number}"
        for dp in module["datapoints"]:
            energy_measurement = dp["energy_measurement"]
            value = dp["value"]
            dp_id = dp["id"]
            unique_id = f"niusmart_{device_id}_dp{dp_id}"
            topic_base = f"niusmart/{hub_id}/tuya/{device_id}/{dp_id}"
            state_topic = f"{topic_base}/state"
            device = {"identifiers": [f"niusmart_tuya_{device_id}"], "name": module["name"]}
            if dp["relay_candidate"] and (module["source"] == "local" and module["seen"] or (device_id, dp_id) in tracked_switches):
                active_switches.add((device_id, dp_id))
                config = {
                    "name": relay_names[dp_id], "unique_id": unique_id, "state_topic": state_topic,
                    "command_topic": f"{topic_base}/set", "device": device, "qos": 1,
                    "payload_on": "on", "payload_off": "off", "retain": False, "optimistic": False,
                    "availability_mode": "all",
                    "availability": [{"topic": relay_hub_availability(hub_id)},
                                     {"topic": f"{topic_base}/availability"}],
                }
                retired_topics.add(f"homeassistant/sensor/{unique_id}/config")
                messages.append((f"homeassistant/switch/{unique_id}/config", json.dumps(config), True))
                published_datapoints.append((dp, config["name"], "switch"))
                online = module["read"] and type(value) is bool
                if online:
                    messages.append((state_topic, "on" if value else "off", False))
                    counts["other"] += 1
                messages.append((f"{topic_base}/availability", "online" if online else "offline", True))
                continue
            if energy_measurement:
                if not energy_enabled:
                    continue
                if (module["product_id"] == ENERGY_PRODUCT
                        and dp["device_class"] is None and dp["id"] in ("102", "104")):
                    if not module["read"]:
                        continue
                    if value not in ("FORWARD", "REVERSE"):
                        value = "Inconnu"
                elif type(value) not in (int, float):
                    continue
            elif module["product_id"] == ENERGY_PRODUCT:
                # Calibration fields and unidentified meter DPs remain on the dashboard.
                continue
            if type(value) not in (bool, str, int, float):
                continue
            if isinstance(value, float) and not isfinite(value):
                continue
            if isinstance(value, str) and (not value.strip() or len(value) > 255):
                continue
            device_class = dp["device_class"]
            config = {
                "name": dp["name"], "unique_id": unique_id, "state_topic": state_topic,
                "device": device,
                "expire_after": expire_after,
            }
            if dp["unit"]:
                config["unit_of_measurement"] = dp["unit"]
            if device_class:
                config["device_class"] = device_class
            if (module["product_id"] == ENERGY_PRODUCT and device_class == "power"
                    and dp["unit"] == "W" and dp["id"] in ("101", "105", "115")):
                config["suggested_display_precision"] = 1
            if energy_measurement and type(value) in (int, float) and dp["state_class"]:
                config["state_class"] = dp["state_class"]
            payload = str(value)
            if isinstance(value, bool):
                payload = "on" if value else "off"
            counts["energy" if energy_measurement else "other"] += 1
            messages.extend((
                (f"homeassistant/sensor/{unique_id}/config", json.dumps(config), True),
                (state_topic, payload, False),
            ))
            published_datapoints.append((dp, config["name"], "sensor"))
    for device_id, dp_id in tracked_switches - active_switches:
        retired_topics.update((
            f"homeassistant/switch/niusmart_{device_id}_dp{dp_id}/config",
            f"niusmart/{hub_id}/tuya/{device_id}/{dp_id}/availability",
        ))
    messages[:0] = [(topic, "", True) for topic in sorted(retired_topics)]
    if active_switches - tracked_switches:
        # Record potential retained configs before sending them, including partial sends.
        if save_switches is not None:
            save_switches(tracked_switches | active_switches)
        tracked_switches.update(active_switches)
    if sent is None:
        sent = {}
    now = monotonic()
    outgoing = []
    for message in messages:
        topic, payload, retained = message
        previous = sent.get(topic)
        if previous is None or previous[0] != payload:
            outgoing.append(message)
        elif topic not in retired_topics and now - previous[1] >= refresh_after:
            outgoing.append(message)
    if outgoing:
        publish_samples(mqtt_service(), outgoing)
        sent.update({topic: (payload, now) for topic, payload, _ in outgoing})
    if tracked_switches - active_switches:
        if save_switches is not None:
            save_switches(active_switches)
        tracked_switches.intersection_update(active_switches)
    active_topics = {topic for topic, _, _ in messages}
    for topic in sent.keys() - active_topics:
        del sent[topic]
    for dp, name, mode in published_datapoints:
        dp.update(mqtt_name=name, mqtt_mode=mode)
    return counts

"""Home Assistant integration updates and read-only ECO-WORTHY MQTT export."""

import json
import math
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from hub_mqtt import mqtt_service, publish_samples

INPUT_DEVICE = {"identifiers": ["rem-0021_ndih432"], "name": "NDIH-432 module"}
INPUT_IDS = {f"rem-0021_n4dih32_di{number:02d}" for number in range(1, 33)}
CAN_SOURCE = Path("/usr/local/share/niu_can")
TOPIC = re.compile(r"[A-Za-z0-9_-]+")
INVALID_STATE = {"unknown", "unavailable", ""}

SOLAR_FIELDS = {
    "pv_voltage": ("Panneau solaire", "Tension PV", ("pv voltage", "solar panel voltage", "solar voltage", "panel voltage", "tension pv", "tension photovoltaïque")),
    "pv_power": ("Panneau solaire", "Puissance PV", ("pv power", "solar panel power", "solar power", "panel charging power", "puissance pv", "puissance du panneau")),
    "pv_energy_total": ("Panneau solaire", "Production totale", ("total generation", "total generated energy", "total production", "production totale", "lifetime yield")),
    "battery_voltage": ("Batterie", "Tension batterie", ("battery voltage", "tension batterie", "tension de la batterie")),
    "battery_charge_current": ("Batterie", "Courant de charge", ("battery charge current", "battery charging current", "charging current to battery", "courant de charge batterie", "courant de charge de la batterie")),
    "battery_charge_power": ("Batterie", "Puissance de charge", ("battery charge power", "battery charging power", "puissance de charge batterie", "puissance de charge de la batterie")),
    "load_voltage": ("Sortie LOAD", "Tension de sortie", ("load voltage", "output voltage", "tension de sortie")),
    "load_current": ("Sortie LOAD", "Courant de sortie", ("load current", "output current", "courant de sortie")),
    "load_energy_total": ("Sortie LOAD", "Consommation totale", ("total consumption", "total load energy", "total consumed energy", "consommation totale")),
}
SOLAR_ALIASES = {**SOLAR_FIELDS, "pv_current": (None, None, ("pv current", "panel current", "solar panel current"))}
SOLAR_UNITS = {
    "voltage": {"v", "mv", "kv"}, "power": {"w", "kw", "mw"},
    "current": {"a", "ma"}, "total": {"wh", "kwh", "mwh", "j", "kj", "mj", "gj"},
}


def core_api(path, data=None):
    request = Request(f"http://supervisor/core/api/{path}", data=data, headers={
        "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}",
        "Content-Type": "application/json",
    })
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def yaml_mapping(node):
    if not isinstance(node, yaml.MappingNode) or node.flow_style:
        return {}
    return {key.value: (key, value) for key, value in node.value if isinstance(key, yaml.ScalarNode)}


def group_inputs(source):
    mqtt = yaml_mapping(yaml.compose(source)).get("mqtt", (None, None))[1]
    sensors = yaml_mapping(mqtt).get("binary_sensor", (None, None))[1]
    if not isinstance(sensors, yaml.SequenceNode) or sensors.flow_style:
        return source
    matched = set()
    inserts = []
    newline = "\r\n" if "\r\n" in source else "\n"
    for sensor in sensors.value:
        keys = yaml_mapping(sensor)
        identifier = keys.get("unique_id", (None, None))[1]
        topic = keys.get("state_topic", (None, None))[1]
        if not isinstance(identifier, yaml.ScalarNode) or identifier.value not in INPUT_IDS:
            continue
        if identifier.value in matched or not isinstance(topic, yaml.ScalarNode) or topic.value != "niusmart/data":
            raise ValueError("Configuration des entrées NDIH-432 inattendue")
        matched.add(identifier.value)
        if "device" in keys:
            device = yaml_mapping(keys["device"][1])
            ids = device.get("identifiers", (None, None))[1]
            if (not isinstance(ids, yaml.SequenceNode)
                    or [item.value for item in ids.value] != INPUT_DEVICE["identifiers"]):
                raise ValueError("Un appareil MQTT différent est déjà associé au NDIH-432")
            continue
        end = source.find("\n", identifier.end_mark.index)
        if end < 0:
            raise ValueError("Bloc NDIH-432 incomplet")
        indent = " " * keys["unique_id"][0].start_mark.column
        block = newline.join((
            f"{indent}device:",
            f"{indent}  identifiers: {json.dumps(INPUT_DEVICE['identifiers'])}",
            f"{indent}  name: {json.dumps(INPUT_DEVICE['name'])}",
            "",
        ))
        inserts.append((end + 1, block))
    if not matched:
        return source
    if matched != INPUT_IDS:
        raise ValueError("Le bloc MQTT ne contient pas les 32 entrées attendues")
    for index, block in reversed(inserts):
        source = source[:index] + block + source[index:]
    yaml.compose(source)
    return source


def configure_inputs(root, reload_flag, write_json):
    path = root / "configuration.yaml"
    if not path.is_file():
        return
    original = path.read_bytes()
    source = original.decode("utf-8")
    updated = group_inputs(source)
    if updated != source:
        details = path.stat()
        backup = root / "configuration.yaml.niu-before-ndih432"
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as saved:
                saved.write(original)
                saved.flush()
                os.fsync(saved.fileno())
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as temporary:
            candidate = Path(temporary.name)
            try:
                os.fchown(temporary.fileno(), details.st_uid, details.st_gid)
                os.fchmod(temporary.fileno(), stat.S_IMODE(details.st_mode))
                temporary.write(updated.encode("utf-8"))
                temporary.flush()
                os.fsync(temporary.fileno())
                if path.read_bytes() != original:
                    raise RuntimeError("La configuration Home Assistant a changé pendant la mise à jour")
                write_json(reload_flag, {"pending": True})
                candidate.replace(path)
            finally:
                candidate.unlink(missing_ok=True)
    if reload_flag.is_file():
        core_api("services/mqtt/reload", b"{}")
        reload_flag.unlink(missing_ok=True)


def install_climate(root, source=CAN_SOURCE):
    target = root / "custom_components" / "niu_can"
    if target.exists() or target.is_symlink():
        return False
    target.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as workspace:
        pending = Path(workspace) / "niu_can"
        shutil.copytree(source, pending)
        pending.rename(target)
    return True


def normalized(value):
    return " ".join(re.sub(r"[^\w]+|_", " ", str(value).lower()).split())


def metric_for(entity_id, name, unit, state):
    name_keys = (normalized(entity_id.split(".", 1)[-1]), normalized(name))
    try:
        if not math.isfinite(float(state)):
            return None
    except (TypeError, ValueError):
        return None
    unit = str(unit).lower()
    for metric, (_, _, aliases) in SOLAR_ALIASES.items():
        if unit not in SOLAR_UNITS[metric.rsplit("_", 1)[-1]]:
            continue
        if any(candidate == alias or candidate.endswith(" " + alias)
               for candidate in name_keys for alias in aliases):
            return metric
    return None


def solar_payloads(root, hub_id, states):
    registry = root / ".storage" / "core.entity_registry"
    rows = json.loads(registry.read_text())["data"]["entities"] if registry.is_file() else []
    sources = {row["entity_id"]: row for row in rows
               if normalized(row.get("platform")).replace(" ", "") == "ecoworthy"}
    mqtt_ids = {row["entity_id"] for row in rows if row.get("platform") == "mqtt"}
    messages = []
    grouped = {}
    for item in states:
        entity_id = item["entity_id"]
        domain, _, object_id = entity_id.partition(".")
        attributes = item.get("attributes", {})
        name = attributes.get("friendly_name") or object_id
        source = sources.get(entity_id)
        prefix_matches = normalized(object_id).replace(" ", "").startswith("ecoworthy")
        if domain not in ("sensor", "binary_sensor") or not TOPIC.fullmatch(object_id):
            continue
        if source is None and not prefix_matches:
            continue
        if source and source.get("disabled_by"):
            continue
        # Exclude the entities created by this MQTT export even after registry reloads.
        if entity_id in mqtt_ids or (
            object_id.startswith("eco_worthy_registres_modbus") and "registres" in attributes
        ):
            continue
        state = item["state"]
        unit = attributes.get("unit_of_measurement")
        metric = metric_for(entity_id, name, unit, state)
        source_key = source.get("unique_id") if source else None
        if not isinstance(source_key, str) or not TOPIC.fullmatch(source_key):
            source_key = object_id
        uid = f"niusmart_{hub_id.lower()}_eco_worthy_{domain}_{source_key}"
        base = f"niusmart/{hub_id}/solar/eco_worthy/{domain}/{source_key}"
        config = {
            "name": name, "unique_id": uid, "state_topic": f"{base}/state",
            "expire_after": 120,
            "device": {"identifiers": [f"niusmart_{hub_id.lower()}_eco_worthy"],
                       "name": f"{hub_id} ECO-WORTHY MPPT"},
        }
        if domain == "binary_sensor":
            config.update(payload_on="on", payload_off="off")
        for key in ("device_class", "unit_of_measurement", "state_class"):
            if attributes.get(key) is not None:
                config[key] = attributes[key]
        messages.append((f"homeassistant/{domain}/{uid}/config", json.dumps(config), True))
        if state not in INVALID_STATE:
            messages.append((config["state_topic"], state, False))
        if metric:
            value = {"value": state, "unit": unit, "entity_id": entity_id}
            grouped.setdefault(metric, []).append(value)
    for power_key, voltage_key, current_key in (
        ("pv_power", "pv_voltage", "pv_current"),
        ("battery_charge_power", "battery_voltage", "battery_charge_current"),
    ):
        if grouped.get(power_key) or any(len(grouped.get(key, [])) != 1 for key in (voltage_key, current_key)):
            continue
        voltage, current = (grouped[key][0] for key in (voltage_key, current_key))
        scale = {"v": 1, "mv": 0.001, "kv": 1000, "a": 1, "ma": 0.001}
        watts = float(voltage["value"]) * scale[voltage["unit"].lower()]
        watts *= float(current["value"]) * scale[current["unit"].lower()]
        if not math.isfinite(watts):
            continue
        value = str(round(watts, 3))
        grouped[power_key] = [{"value": value, "unit": "W", "estimated": True}]
        uid = f"niusmart_{hub_id.lower()}_eco_worthy_calculated_{power_key}"
        state_topic = f"niusmart/{hub_id}/solar/eco_worthy/calculated/{power_key}/state"
        config = {
            "name": SOLAR_FIELDS[power_key][1] + " (calculée)",
            "unique_id": uid, "state_topic": state_topic, "expire_after": 120,
            "device_class": "power", "unit_of_measurement": "W", "state_class": "measurement",
            "device": {"identifiers": [f"niusmart_{hub_id.lower()}_eco_worthy"],
                       "name": f"{hub_id} ECO-WORTHY MPPT"},
        }
        messages.extend(((f"homeassistant/sensor/{uid}/config", json.dumps(config), True),
                         (state_topic, value, False)))
    status = {
        key: {"group": group, "name": label,
              **(grouped[key][0] if len(grouped.get(key, [])) == 1 else {"value": None})}
        for key, (group, label, _) in SOLAR_FIELDS.items()
    }
    return messages, status


def publish_solar(root, hub_id, enabled, topic_file, load_json, write_json, *, states=None):
    if not TOPIC.fullmatch(hub_id):
        raise ValueError("Identifiant MQTT solaire invalide")
    saved = load_json(topic_file)
    if not isinstance(saved, dict) or saved.get("hub_id") != hub_id:
        saved = {}
    prefix = f"homeassistant/sensor/niusmart_{hub_id.lower()}_eco_worthy_"
    binary_prefix = prefix.replace("/sensor/", "/binary_sensor/", 1)
    previous = {topic for topic in saved.get("topics", []) if isinstance(topic, str)
                and topic.startswith((prefix, binary_prefix)) and topic.endswith("/config")}
    legacy = set()
    if not saved.get("legacy_cleared"):
        legacy = {f"homeassistant/sensor/niusmart_{hub_id}_eco_worthy_{key}/config"
                  for key in ("battery_voltage", "load_voltage", "temperature", "load_state")}
    if enabled:
        messages, status = solar_payloads(root, hub_id, core_api("states") if states is None else states)
    else:
        messages, status = [], {}
    topics = {topic for topic, _, retained in messages if retained}
    if enabled and not topics:
        return status

    def remember(active, legacy_cleared):
        write_json(topic_file, {"hub_id": hub_id, "topics": sorted(active),
                                "legacy_cleared": legacy_cleared})

    if topics - previous:
        remember(previous | topics, bool(saved.get("legacy_cleared")))
    retired = ((previous | legacy) - topics)
    messages.extend((topic, "", True) for topic in sorted(retired))
    if messages:
        publish_samples(mqtt_service(), messages)
    if retired or legacy:
        remember(topics, True)
    return status


def climate_payloads(root, hub_id, count, states):
    registry = root / ".storage" / "core.entity_registry"
    if not registry.is_file():
        return []
    sources = {}
    for entry in json.loads(registry.read_text())["data"]["entities"]:
        match = re.fullmatch(r"niu_dometic_r([12])_([a-z0-9_]+)", entry.get("unique_id", ""))
        if entry.get("platform") == "niu_can" and not entry.get("disabled_by") and match:
            sources[entry["entity_id"]] = (int(match[1]), match[2])

    messages = []
    for item in states:
        entity_id = item["entity_id"]
        if entity_id not in sources:
            continue
        zone, metric = sources[entity_id]
        if zone > count:
            continue
        source_domain = entity_id.partition(".")[0]
        if source_domain not in ("sensor", "binary_sensor", "climate"):
            continue
        domain = "sensor" if source_domain == "climate" else source_domain
        attrs = item.get("attributes", {})
        uid = f"niusmart_{hub_id.lower()}_dometic_r{zone}_{metric}"
        topic = f"niusmart/{hub_id}/climate/r{zone}/{metric}/state"
        discovery = {
            "name": attrs.get("friendly_name", metric),
            "unique_id": uid, "state_topic": topic, "expire_after": 120,
            "device": {"identifiers": [f"niusmart_{hub_id.lower()}_dometic_r{zone}"],
                       "name": f"{hub_id} Dometic R{zone}"},
        }
        if source_domain != "climate":
            for key in ("unit_of_measurement", "device_class", "state_class"):
                if attrs.get(key) is not None:
                    discovery[key] = attrs[key]
        if domain == "binary_sensor":
            discovery.update(payload_on="on", payload_off="off")
        messages.append((f"homeassistant/{domain}/{uid}/config", json.dumps(discovery), True))
        if item["state"] not in INVALID_STATE:
            messages.append((topic, item["state"], False))
    return messages


def publish_climate(root, hub_id, count, topic_file, load_json, write_json, *, states=None):
    if not TOPIC.fullmatch(hub_id):
        raise ValueError("Identifiant MQTT climatisation invalide")
    saved = load_json(topic_file) or {}
    owned = re.compile(rf"homeassistant/(?:sensor|binary_sensor)/niusmart_{re.escape(hub_id.lower())}_dometic_r[12]_[a-z0-9_]+/config")
    previous = {topic for topic in saved.get("topics", []) if isinstance(topic, str) and owned.fullmatch(topic)} \
        if isinstance(saved, dict) and saved.get("hub_id") == hub_id else set()
    messages = climate_payloads(root, hub_id, count, states) if count and states is not None else []
    active = {topic for topic, _, retained in messages if retained}
    # A reduction in the configured number of zones is authoritative even if Core is offline.
    allowed_prefixes = tuple(
        f"niusmart_{hub_id.lower()}_dometic_r{zone}_" for zone in range(1, count + 1)
    )
    if count and not active:
        active = {topic for topic in previous
                  if topic.split("/")[2].startswith(allowed_prefixes)}
    if active - previous:
        write_json(topic_file, {"hub_id": hub_id, "topics": sorted(previous | active)})
    messages.extend((topic, "", True) for topic in sorted(previous - active))
    if messages:
        publish_samples(mqtt_service(), messages)
    if previous - active:
        write_json(topic_file, {"hub_id": hub_id, "topics": sorted(active)})
    return len(active)


def configure_climate(root):
    registry = root / ".storage" / "core.config_entries"
    if registry.is_file() and any(entry.get("domain") == "niu_can"
                                 for entry in json.loads(registry.read_text())["data"]["entries"]):
        return "configured"
    try:
        result = core_api("config/config_entries/flow", b'{"handler":"niu_can"}')
    except OSError:
        return "waiting_core"
    return "configured" if result.get("type") == "create_entry" else "waiting_core"

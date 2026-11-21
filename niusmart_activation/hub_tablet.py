"""Publish the activated site's presentation settings to Home Assistant."""

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from hub_homeassistant import core_api

CARD_SOURCE = Path("/usr/local/share/niu_tablet")
CARD_DIRECTORY = Path("www/niusmart/tablet")
TABLET_STATE = "states/sensor.niu_tablet_config"
OPTION_NAMES = (
    "toilet_count", "door_count", "climate_count", "has_fresh_water", "has_waste_water",
    "has_water_control", "has_exterior_lighting", "has_power_monitoring", "has_solar_panels",
)
ENTITY_ID = re.compile(r"(?:switch|binary_sensor|sensor|climate)\.[a-z0-9_]+")
SOLAR_KEYS = ("pv_voltage", "pv_power", "battery_voltage", "battery_temperature",
              "load_state", "load_voltage", "load_current", "load_power")


def install_cards(root):
    directory = root / CARD_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    for source in sorted(CARD_SOURCE.glob("*.js")):
        destination = directory / source.name
        content = source.read_bytes()
        if destination.is_file() and destination.read_bytes() == content:
            continue
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as staged:
            name = Path(staged.name)
            try:
                staged.write(content)
                staged.flush()
                os.fchmod(staged.fileno(), 0o644)
                os.replace(name, destination)
            finally:
                name.unlink(missing_ok=True)


def fetch_mappings(config, base_url):
    url = base_url + "/hub_tablet_config.php"
    request = Request(url, headers={"Accept": "application/json"})
    request.add_unredirected_header("X-API-Key", config["api_key"])
    request.add_unredirected_header("X-Niu-Device", config["device_code"])
    with urlopen(request, timeout=10) as response:
        if response.geturl() != url:
            raise ValueError("Redirection du service tablette refusée")
        data = json.load(response)
    if (not isinstance(data, dict) or data.get("success") is not True
            or data.get("site_id") != config["site_id"]
            or data.get("device_code") != config["device_code"]
            or not isinstance(data.get("mappings"), list)):
        raise ValueError("Réponse tablette invalide")
    return [
        {"function_type": row["function_type"], "function_number": row.get("function_number"),
         "entity_id": row["entity_id"], "custom_name": str(row.get("custom_name") or "")[:255]}
        for row in data["mappings"]
        if isinstance(row, dict) and isinstance(row.get("function_type"), str)
        and isinstance(row.get("entity_id"), str) and ENTITY_ID.fullmatch(row["entity_id"])
    ]


def tablet_attributes(root, config, mappings):
    site = config.get("site_config", {})
    options = {key: site.get(key, 0) for key in OPTION_NAMES}
    registry = root / ".storage/core.entity_registry"
    entities = json.loads(registry.read_text(encoding="utf-8"))["data"]["entities"] if registry.is_file() else []
    climate, solar = {}, {}
    hub_key = config["device_code"].lower()
    for entry in entities:
        entity = entry.get("entity_id", "")
        unique_id = entry.get("unique_id", "")
        if entry.get("disabled_by") or not ENTITY_ID.fullmatch(entity):
            continue
        platform = entry.get("platform")
        if platform == "niu_can" and options["climate_count"]:
            match = re.fullmatch(r"niu_dometic_r([12])_(climate|state|mode|fan|heat_setpoint|cool_setpoint|power_fail)", unique_id)
            if match and int(match[1]) <= options["climate_count"]:
                metric = "thermostat" if match[2] == "climate" else match[2]
                climate.setdefault(match[1], {})[metric] = entity
        if platform == "mqtt" and options["has_solar_panels"]:
            for key in SOLAR_KEYS:
                domain = "binary_sensor" if key == "load_state" else "sensor"
                if unique_id == f"niusmart_{hub_key}_eco_worthy_{domain}_eco_worthy_{key}":
                    solar[key] = entity
    return {"options": options, "mappings": mappings, "climate": climate, "solar": solar}


def publish_tablet(state, attributes=None):
    payload = {"state": state, "attributes": attributes or {}}
    core_api(TABLET_STATE, json.dumps(payload).encode("utf-8"))

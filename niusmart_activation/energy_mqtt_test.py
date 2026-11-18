"""Publish one read-only Tuya ENERGY sample for the REM-0021 pilot."""

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import Event

import paho.mqtt.client as mqtt
import tinytuya


class EnergyTestError(Exception):
    """A diagnostic message safe to display on the local page."""


MEASUREMENTS = {
    "cur_power": "Puissance", "power": "Puissance",
    "cur_voltage": "Tension", "voltage": "Tension",
    "cur_current": "Courant", "current": "Courant",
    "add_ele": "Énergie", "total_forward_energy": "Énergie",
}


def select_measurement(datapoints, values):
    by_code = {dp.get("status_code"): str(dp["dp_id"]) for dp in datapoints}
    for code, label in MEASUREMENTS.items():
        dp_id = by_code.get(code)
        value = values.get(dp_id)
        if type(value) in (int, float) and isfinite(value):
            return dp_id, label, value

    if not values:
        raise EnergyTestError("ENERGY n’a renvoyé aucun datapoint local.")

    dp_ids = sorted(
        (dp_id for dp_id in values if dp_id.isascii() and dp_id.isdecimal()),
        key=int,
    )
    for dp_id in dp_ids:
        value = values[dp_id]
        if type(value) in (int, float) and isfinite(value):
            return dp_id, f"DP {dp_id}", value

    details = ", ".join(f"DP {dp_id} ({type(values[dp_id]).__name__})" for dp_id in dp_ids[:10])
    if len(dp_ids) > 10:
        details += ", …"
    message = "ENERGY n’a renvoyé aucune valeur numérique directe."
    if details:
        message += f" Datapoints reçus : {details}."
    raise EnergyTestError(message)


def test_energy(feed, inventory):
    if not feed or not isinstance(inventory, dict) or feed["hub_id"] != inventory.get("hub_id"):
        raise EnergyTestError("Lance d’abord un balayage Tuya sur ce hub.")
    try:
        scanned_at = datetime.fromisoformat(inventory.get("scanned_at") or "")
        fresh = timedelta(0) <= datetime.now(timezone.utc) - scanned_at <= timedelta(minutes=15)
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        raise EnergyTestError("Relance le balayage Tuya pour actualiser l’inventaire.")

    # The hub's API identifier and the unit prefix used in device names can differ.
    energy_modules = [
        item for item in feed["modules"]
        if (item.get("device_name") or "").strip().rsplit("-", 1)[-1].casefold() == "energy"
    ]
    if not energy_modules:
        raise EnergyTestError("NiuSmart ne liste pas de module ENERGY pour ce hub.")
    if len(energy_modules) != 1:
        raise EnergyTestError("Plusieurs modules ENERGY sont associés à ce hub.")
    module = energy_modules[0]
    name = module["device_name"]
    device_id = module["tuya_device_id"]
    endpoint = inventory["modules"].get(device_id, {})
    if not endpoint.get("discovered") or endpoint.get("version") != "3.5":
        raise EnergyTestError("ENERGY doit être détecté en protocole 3.5 avant le test.")

    broker = mqtt_service()
    device = tinytuya.Device(device_id, endpoint["ip"], module["local_key"], version=3.5)
    device.set_socketTimeout(3)
    device.set_socketRetryLimit(1)
    reply = device.status()
    values = reply.get("dps") if isinstance(reply, dict) else None
    if not isinstance(values, dict):
        raise EnergyTestError("ENERGY n’a renvoyé aucune mesure locale exploitable.")

    dp_id, label, value = select_measurement(module["datapoints"], values)

    # The inventory feed doesn't include conversion factors, so label the value as raw.
    unique_id = f"niusmart_{device_id}_test_dp{dp_id}"
    state_topic = f"niusmart/{feed['hub_id']}/tuya/energy/test/{dp_id}/state"
    discovery = {
        "name": f"Test {label} (valeur brute)",
        "unique_id": unique_id,
        "state_topic": state_topic,
        "device": {"identifiers": [f"niusmart_tuya_{device_id}"], "name": name},
        "expire_after": 300,
    }
    publish_sample(broker, f"homeassistant/sensor/{unique_id}/config", discovery, value)
    return f"MQTT a reçu la valeur brute {value} pour le datapoint {dp_id} d’ENERGY."


def mqtt_service():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise EnergyTestError("Le service MQTT du Supervisor est inaccessible.")
    request = urllib.request.Request("http://supervisor/services/mqtt")
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
    except (OSError, ValueError):
        raise EnergyTestError("Le Supervisor n’a pas fourni de broker MQTT.") from None
    if result.get("result") != "ok" or not result.get("data", {}).get("host"):
        raise EnergyTestError("Le Supervisor n’a pas fourni de broker MQTT.")
    return result["data"]


def publish_sample(broker, config_topic, discovery, value):
    protocol = mqtt.MQTTv31 if broker.get("protocol") == "3.1" else mqtt.MQTTv311
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=protocol)
    if broker.get("username"):
        client.username_pw_set(broker["username"], broker.get("password"))
    if broker.get("ssl"):
        client.tls_set()
    client.connect_timeout = 5
    connected = Event()
    reason = []

    def on_connect(_client, _userdata, _flags, code, _properties):
        reason.append(code)
        connected.set()

    client.on_connect = on_connect
    try:
        client.connect(broker["host"], broker["port"])
        client.loop_start()
        if not connected.wait(5) or reason[0].is_failure:
            raise EnergyTestError("Le broker MQTT a refusé la connexion ou n’a pas répondu.")
        for topic, payload in ((config_topic, json.dumps(discovery)), (discovery["state_topic"], str(value))):
            message = client.publish(topic, payload, qos=1, retain=True)
            message.wait_for_publish(timeout=5)
            if not message.is_published():
                raise EnergyTestError("Le broker MQTT n’a pas confirmé la publication.")
    finally:
        client.disconnect()
        client.loop_stop()

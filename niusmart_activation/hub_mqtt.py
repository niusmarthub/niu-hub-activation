"""Publish measurement discovery and state to the Supervisor MQTT service."""

import json
import os
import urllib.request
from threading import Event

import paho.mqtt.client as mqtt


def mqtt_service():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("Le service MQTT du Supervisor est inaccessible.")
    request = urllib.request.Request("http://supervisor/services/mqtt")
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
    except (OSError, ValueError):
        raise RuntimeError("Le Supervisor n’a pas fourni de broker MQTT.") from None
    if result.get("result") != "ok" or not result.get("data", {}).get("host"):
        raise RuntimeError("Le Supervisor n’a pas fourni de broker MQTT.")
    return result["data"]


def publish_samples(broker, messages):
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
            raise RuntimeError("Le broker MQTT a refusé la connexion ou n’a pas répondu.")
        for topic, payload, retain in messages:
            message = client.publish(topic, payload, qos=1, retain=retain)
            message.wait_for_publish(timeout=5)
            if not message.is_published():
                raise RuntimeError("Le broker MQTT n’a pas confirmé la publication.")
    finally:
        client.disconnect()
        client.loop_stop()

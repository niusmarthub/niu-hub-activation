"""Publish Tuya state and receive relay commands through Supervisor MQTT."""

import json
import os
import urllib.request
from queue import Empty, Full, Queue
from threading import Event
from time import monotonic

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


def mqtt_client(broker):
    protocol = mqtt.MQTTv31 if broker.get("protocol") == "3.1" else mqtt.MQTTv311
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=protocol)
    if broker.get("username"):
        client.username_pw_set(broker["username"], broker.get("password"))
    if broker.get("ssl"):
        client.tls_set()
    client.connect_timeout = 5
    return client


def relay_hub_availability(hub_id):
    return f"niusmart/{hub_id}/tuya/relay_availability"


class RelayCommandListener:
    def __init__(self, broker, hub_id):
        self.hub_id = hub_id
        self.commands = Queue(maxsize=128)
        self.client = mqtt_client(broker)
        self.availability_topic = relay_hub_availability(hub_id)
        self.client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.connect_async(broker["host"], broker["port"])
        self.client.loop_start()

    def on_connect(self, client, _userdata, _flags, reason, _properties):
        if not reason.is_failure:
            client.subscribe(f"niusmart/{self.hub_id}/tuya/+/+/set", qos=1)

    def on_subscribe(self, client, _userdata, _mid, reasons, _properties):
        if reasons and all(not reason.is_failure for reason in reasons):
            client.publish(self.availability_topic, "online", qos=1, retain=True)

    def on_message(self, _client, _userdata, message):
        parts = message.topic.split("/")
        if (len(parts) != 6 or parts[:3] != ["niusmart", self.hub_id, "tuya"]
                or parts[5] != "set" or message.retain or message.payload not in (b"on", b"off")):
            return
        try:
            self.commands.put_nowait((parts[3], parts[4], message.payload == b"on", monotonic()))
        except Full:
            return

    def drain(self):
        commands = []
        for _ in range(self.commands.qsize()):
            try:
                commands.append(self.commands.get_nowait())
            except Empty:
                break
        return commands

    def close(self):
        try:
            if self.client.is_connected():
                message = self.client.publish(self.availability_topic, "offline", qos=1, retain=True)
                message.wait_for_publish(timeout=2)
        finally:
            self.client.disconnect()
            self.client.loop_stop()


def publish_samples(broker, messages):
    client = mqtt_client(broker)
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


def provision_mqtt(root):
    """Use the existing broker or have Supervisor install and start Mosquitto."""
    from hub_netbird import supervisor

    try:
        mqtt_service()
        return "ready"
    except RuntimeError:
        pass

    slug = "core_mosquitto"
    installed = any(addon.get("slug") == slug for addon in supervisor("/addons").get("addons", []))
    if not installed:
        entries = root / ".storage" / "core.config_entries"
        if entries.is_file() and any(entry.get("domain") == "mqtt"
                                     for entry in json.loads(entries.read_text())["data"]["entries"]):
            return "existing_configuration"
        supervisor(f"/store/addons/{slug}/install", {}, timeout=120)
    if supervisor(f"/addons/{slug}/info").get("state") != "started":
        supervisor(f"/addons/{slug}/start", {}, timeout=45)
    return "starting"

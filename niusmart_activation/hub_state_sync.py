"""Send the hub's Home Assistant states to the NiuSmart inventory."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hub_homeassistant import core_api


RECONNECT_INTERVAL = 2

STATE_TEMPLATE = r"""
{%- set rows = namespace(items=[]) -%}
{%- set excluded = '_(countdown(_[0-9]+)?|child_lock|light_mode|max_set|min_set|mini_set|liquid_depth_max)$' -%}
{%- for id in integration_entities('mqtt') | sort -%}
    {%- set identifiers = device_attr(id, 'identifiers') or [] -%}
    {%- set managed = identifiers | selectattr('0', 'eq', 'mqtt')
        | map(attribute='1') | select('match', '^niusmart_tuya_') | list -%}
    {%- set input = id is match('^binary_sensor[.]n4dih32_di(0[1-9]|[12][0-9]|3[0-2])$') -%}
    {%- set unidentified_tank_dp = id is search('_(black_tank|fresh_water)_dp_[0-9]+$') -%}
    {%- if input or (managed and id is match('^(sensor|binary_sensor|switch)[.]')
                     and id is not search(excluded) and not unidentified_tank_dp) -%}
        {%- set rows.items = rows.items + [{
            'entity_id': id,
            'source': 'n4dih32' if input else 'mqtt',
            'state': states(id),
            'updated': states[id].last_updated.isoformat() if states[id] else '',
            'friendly_name': state_attr(id, 'friendly_name') or id
        }] -%}
    {%- endif -%}
{%- endfor -%}
{{ rows.items | to_json }}
"""


def send_request(base_url, config, endpoint, payload):
    url = f"{base_url}/{endpoint}"
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    request.add_unredirected_header("X-API-Key", config["api_key"])
    try:
        with urlopen(request, timeout=8) as response:
            if response.geturl() != url:
                raise RuntimeError("Redirection de synchronisation refusée")
            result = json.load(response)
    except HTTPError as exc:
        with exc:
            if exc.code == 404:
                error = json.load(exc)
                if isinstance(error, dict) and error.get("error") == "entity_not_registered":
                    return False
            if exc.code == 403:
                error = json.load(exc)
                if isinstance(error, dict) and error.get("error") == "entity_disabled":
                    return True
        raise
    if (not isinstance(result, dict) or result.get("success") is not True
            or result.get("device_code") != config["device_code"]
            or result.get("site_id", config["site_id"]) != config["site_id"]
            or result.get("entity_id") != payload["entity_id"]
            or result.get("source") != payload["source"]):
        raise RuntimeError("Réponse NiuSmart invalide")
    return True


def read_inventory():
    rows = core_api("template", json.dumps({"template": STATE_TEMPLATE}).encode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("Inventaire Home Assistant invalide")
    return {row["entity_id"]: row for row in rows}


def send_state(config, base_url, row, sent, still_active):
    key = row["source"], row["entity_id"]
    state = row["state"]
    if sent.get(key) == state or not still_active():
        return
    payload = {"entity_id": row["entity_id"], "source": row["source"], "state": state}
    if not send_request(base_url, config, "device_api.php", payload):
        if not still_active():
            return
        name = row["friendly_name"].encode("utf-8")[:255].decode("utf-8", errors="ignore")
        registration = {
            "entity_id": row["entity_id"], "source": row["source"],
            "platform": row["source"], "friendly_name": name,
        }
        if not send_request(base_url, config, "device_discovery_api.php", registration):
            raise RuntimeError("Enregistrement NiuSmart introuvable")
        if not still_active():
            return
        if not send_request(base_url, config, "device_api.php", payload):
            raise RuntimeError("Entité toujours absente après la découverte")
    sent[key] = state


def sync_states(config, base_url, sent, still_active):
    request = Request(
        "http://supervisor/core/api/stream?restrict=state_changed,entity_registry_updated",
        headers={
            "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}",
            "Accept": "text/event-stream",
        },
    )
    # Subscribe before reading current states so every change during startup is queued.
    with urlopen(request, timeout=90) as stream:
        inventory = read_inventory()
        active_keys = {(row["source"], entity_id) for entity_id, row in inventory.items()}
        for key in sent.keys() - active_keys:
            del sent[key]
        for row in inventory.values():
            send_state(config, base_url, row, sent, still_active)

        for line in stream:
            if not still_active():
                return
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if not raw.startswith(b"{"):
                continue
            event = json.loads(raw)
            data = event.get("data", {})
            entity_id = data.get("entity_id")
            if event.get("event_type") == "entity_registry_updated":
                updated_inventory = read_inventory()
                for removed_id in inventory.keys() - updated_inventory.keys():
                    removed = inventory.pop(removed_id)
                    sent.pop((removed["source"], removed_id), None)
                for added_id in updated_inventory.keys() - inventory.keys():
                    inventory[added_id] = updated_inventory[added_id]
                    send_state(config, base_url, inventory[added_id], sent, still_active)
                continue
            row = inventory.get(entity_id)
            state = data.get("new_state")
            if not row or not state or state["last_updated"] <= row["updated"]:
                continue
            row["updated"] = state["last_updated"]
            if row["state"] == state["state"]:
                continue
            row["state"] = state["state"]
            send_state(config, base_url, row, sent, still_active)
        if still_active():
            raise ConnectionError("Connexion aux événements Home Assistant fermée")

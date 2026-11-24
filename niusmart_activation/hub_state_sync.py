"""Send the hub's Home Assistant states to the NiuSmart inventory."""

import json
import os
import time
from urllib.request import Request, urlopen

from hub_homeassistant import core_api


RECONNECT_INTERVAL = 2

STATE_TEMPLATE = r"""
{%- set rows = namespace(items=[]) -%}
{%- set excluded = '_(countdown(_[0-9]+)?|child_lock|light_mode|max_set|min_set|mini_set|liquid_depth_max)$' -%}
{%- for id in integration_entities('mqtt') | sort -%}
    {%- set identifiers = device_attr(id, 'identifiers') or [] -%}
    {%- set mqtt_ids = identifiers | selectattr('0', 'eq', 'mqtt')
        | map(attribute='1') | list -%}
    {%- set managed = mqtt_ids | select('match', '^niusmart_tuya_') | list -%}
    {%- set eco_probe = id is match('^sensor[.]')
        and state_attr(id, 'device_class') == 'temperature'
        and (mqtt_ids | select('match', '^niusmart_[a-z0-9_-]+_eco_worthy$') | list) -%}
    {%- set input = id is match('^binary_sensor[.]n4dih32_di(0[1-9]|[12][0-9]|3[0-2])$') -%}
    {%- set unidentified_tank_dp = id is search('_(black_tank|fresh_water)_dp_[0-9]+$') -%}
    {%- if input or eco_probe or (managed and id is match('^(sensor|binary_sensor|switch)[.]')
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
{%- set can_ids = [
    'climate.control_niu_dometic_r1_thermostat',
    'climate.control_niu_dometic_r2_thermostat',
    'binary_sensor.control_niu_dometic_diagnostics_niu_dometic_r1_power_fail',
    'binary_sensor.control_niu_dometic_diagnostics_niu_dometic_r2_power_fail'
] -%}
{%- for id in integration_entities('niu_can') | select('in', can_ids) -%}
    {%- if states[id] -%}
        {%- set rows.items = rows.items + [{
            'entity_id': id, 'source': 'niu_can', 'state': states(id),
            'updated': states[id].last_updated.isoformat(),
            'friendly_name': state_attr(id, 'friendly_name') or id,
            'attributes': states[id].attributes
        }] -%}
    {%- endif -%}
{%- endfor -%}
{{ rows.items | to_json }}
"""


def read_inventory():
    rows = core_api("template", json.dumps({"template": STATE_TEMPLATE}).encode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("Inventaire Home Assistant invalide")
    return {row["entity_id"]: row for row in rows}


CAN_ATTRIBUTES = frozenset({
    'temperature', 'current_temperature', 'fan_mode', 'preset_mode', 'etat_reel',
    'mode_reel', 'ventilateur', 'consigne_chauffage_f', 'consigne_climatisation_f',
})


def capture_state(queue, config, row, still_active):
    state = row['state']
    if not isinstance(state, str) or not state.strip() or len(state.encode('utf-8')) > 50:
        return
    if not still_active():
        return
    attributes = {key: val for key, val in row.get('attributes', {}).items()
                  if key in CAN_ATTRIBUTES} if row['source'] == 'niu_can' else None
    queue.append(config, row['entity_id'], row['source'], state, time.time(),
                 attributes=attributes, friendly_name=row['friendly_name'])


def sync_states(config, queue, still_active, rescan):
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
        for row in inventory.values():
            capture_state(queue, config, row, still_active)

        for line in stream:
            if not still_active():
                return
            if rescan.is_set():
                rescan.clear()
                inventory = read_inventory()
                for row in inventory.values():
                    capture_state(queue, config, row, still_active)
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
                    inventory.pop(removed_id)
                for added_id in updated_inventory.keys() - inventory.keys():
                    inventory[added_id] = updated_inventory[added_id]
                    capture_state(queue, config, inventory[added_id], still_active)
                continue
            row = inventory.get(entity_id)
            state = data.get("new_state")
            if not row or not state or state["last_updated"] <= row["updated"]:
                continue
            row["updated"] = state["last_updated"]
            if row["state"] == state["state"] and row["source"] != "niu_can":
                continue
            row["state"] = state["state"]
            if row["source"] == "niu_can":
                row["attributes"] = state.get("attributes", {})
            capture_state(queue, config, row, still_active)
        if still_active():
            raise ConnectionError("Connexion aux événements Home Assistant fermée")

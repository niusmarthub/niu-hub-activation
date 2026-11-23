"""Read-only Tuya Cloud fallback through Home Assistant's device/entity registries.

Never use names to associate devices: Tuya identifiers must match the NiuSmart inventory.
The bridge does not send cloud commands or change Home Assistant configuration.
"""
import os
import re
from datetime import datetime, timezone

import websocket

INVALID = {"unknown", "unavailable", "none", ""}
CLOUD_MAX_AGE = 1800
ALIAS = {
    "va_temperature": ("temperature",),
    "humidity_value": ("humidity",),
    "va_humidity": ("humidity",),
    "battery_percentage": ("battery",),
    "liquid_depth": ("distance", "profondeur"),
    "liquid_level_percent": ("niveau_du_liquide", "liquid_level"),
}


def _ws_request(commands):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    ws = websocket.create_connection(
        "ws://supervisor/core/api/websocket", timeout=10,
        http_proxy_host=None,
    )
    try:
        greeting = __import__("json").loads(ws.recv())
        if greeting.get("type") != "auth_required":
            return None
        ws.send(__import__("json").dumps({"type": "auth", "access_token": token}))
        if __import__("json").loads(ws.recv()).get("type") != "auth_ok":
            return None
        results = []
        for index, command in enumerate(commands, 1):
            ws.send(__import__("json").dumps({"id": index, "type": command}))
            response = __import__("json").loads(ws.recv())
            if response.get("id") != index or not response.get("success"):
                return None
            results.append(response.get("result", []))
        return results
    finally:
        ws.close()


def _raw_value(value, dp):
    if value in INVALID:
        return None
    kind = dp.get("type")
    if kind == "Boolean":
        return {"on": True, "off": False}.get(value.lower())
    try:
        numeric = float(value)
        if not __import__("math").isfinite(numeric):
            return None
        scale = dp.get("scale", 0)
        if not isinstance(scale, int) or not 0 <= scale <= 9:
            scale = 0
        return round(numeric * (10 ** scale))
    except (TypeError, ValueError):
        return value if len(value) < 128 else None


def read_cloud_states(feed):
    """Return {tuya_device_id: {dp_id: raw_value}} for verified Tuya entities."""
    response = _ws_request(["config/device_registry/list", "config/entity_registry/list", "get_states"])
    if response is None:
        return {}
    devices, entities, states = response
    expected = {item["tuya_device_id"]: item for item in feed.get("modules", [])}
    matched = {}
    for device in devices:
        identifiers = device.get("identifiers") or []
        ids = [identifier[1] for identifier in identifiers
               if isinstance(identifier, (list, tuple)) and len(identifier) == 2
               and identifier[0] == "tuya" and identifier[1] in expected]
        if len(ids) == 1:
            matched[device["id"]] = ids[0]
    by_entity = {state.get("entity_id"): state for state in states}
    now = datetime.now(timezone.utc)
    output = {}
    for entity in entities:
        device_id = matched.get(entity.get("device_id"))
        if not device_id or entity.get("platform") != "tuya" or entity.get("disabled_by"):
            continue
        state = by_entity.get(entity.get("entity_id"))
        if not state or str(state.get("state", "")).lower() in INVALID:
            continue
        try:
            stamp = datetime.fromisoformat(state["last_updated"].replace("Z", "+00:00"))
            if abs((now - stamp).total_seconds()) > CLOUD_MAX_AGE:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        module = expected[device_id]
        candidates = []
        uid = str(entity.get("unique_id") or "").lower()
        entity_name = str(entity.get("entity_id") or "").lower()
        device_class = str(entity.get("device_class") or entity.get("original_device_class") or "").lower()
        for dp in module.get("datapoints") or []:
            code = str(dp.get("status_code") or "").lower()
            dp_id = str(dp.get("dp_id") or "")
            if not code or not dp_id.isdigit() or re.fullmatch(r"(?:switch|relay)(?:_\d+)?", code):
                continue
            aliases = ALIAS.get(code, ())
            if uid.endswith(code) or any(uid.endswith(alias) for alias in aliases):
                candidates.append((3, dp))
            elif device_class and device_class in aliases:
                candidates.append((2, dp))
            elif any(entity_name.endswith("_" + alias) for alias in aliases):
                candidates.append((1, dp))
        if not candidates:
            continue
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            continue
        dp = candidates[0][1]
        value = _raw_value(str(state["state"]), dp)
        if value is not None:
            output.setdefault(device_id, {})[str(dp["dp_id"])] = value
    return output

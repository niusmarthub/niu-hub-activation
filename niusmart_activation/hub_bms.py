"""Read Bluetooth BMS telemetry from Home Assistant's existing integration."""
import json
import math
import re

from hub_homeassistant import core_api

BMS_SOURCE = "bms_ble"
BMS_SUMMARY_METRICS = frozenset({"voltage", "current", "stored_energy", "design_capacity", "charge_pct"})
STANDBY_CURRENT = 0.1
DEVICE_ID = re.compile(r"[a-f0-9]{32}")
MODEL_ENTITY = re.compile(r"(?:sensor|binary_sensor)\.dp04s007l4s100a_(.+?)(?:_([0-9]+))?")
METRICS = {
    "charge_pct": (("batterie", "battery", "battery_level"), "Charge (%)"),
    "voltage": (("tension", "voltage"), "Tension (V)"),
    "current": (("courant", "current"), "Courant (A)"),
    "temperature": (("temperature",), "Température (°C)"),
    "power": (("puissance", "power"), "Puissance (W)"),
    "stored_energy": (("energie_stockee", "stored_energy"), "Énergie stockée (Wh)"),
    "runtime": (("runtime",), "Autonomie estimée (h)"),
    "cycles": (("cycles", "charge_cycles"), "Cycles"),
    "design_capacity": (("design_capacity",), "Capacité nominale (Ah)"),
    "delta_voltage": (("delta_cell_voltage",), "Écart cellules (V)"),
    "problem": (("probleme", "problem"), "Problème BMS"),
}
METRIC_BY_SUFFIX = {suffix: metric for metric, (suffixes, _) in METRICS.items() for suffix in suffixes}

BMS_TEMPLATE = """
{%- set rows = namespace(items=[]) -%}
{%- for entity in integration_entities('bms_ble') | sort -%}
    {%- set device = device_id(entity) -%}
    {%- if device and states[entity] and entity is match('^(sensor|binary_sensor)[.]dp04s007l4s100a_') -%}
        {%- set rows.items = rows.items + [{
            'device_id': device,
            'device_name': device_attr(device, 'name_by_user'),
            'entity_id': entity,
            'state': states(entity),
            'cells': state_attr(entity, 'cell_voltages')
        }] -%}
    {%- endif -%}
{%- endfor -%}
{{ rows.items | to_json }}
"""


def numeric_state(value):
    if type(value) not in (str, int, float):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def battery_readings(rows):
    batteries = {}
    for row in rows:
        device = row.get("device_id", "")
        match = MODEL_ENTITY.fullmatch(row.get("entity_id", ""))
        if not DEVICE_ID.fullmatch(device) or not match:
            continue
        metric = METRIC_BY_SUFFIX.get(match[1])
        if not metric:
            continue
        battery = batteries.setdefault(device, {"values": {}, "name": ""})
        state = row.get("state")
        if metric == "problem":
            value = state if state in ("on", "off") else None
        else:
            value = numeric_state(state)
            if metric == "charge_pct" and value is not None and not 0 <= value <= 100:
                value = None
        battery["values"][metric] = value if value is not None else "unavailable"
        if metric == "charge_pct":
            battery["name"] = row.get("device_name") or f"Batterie BMS {match[2] or '1'}"
        if metric == "delta_voltage" and isinstance(row.get("cells"), list):
            for index, cell in enumerate(row["cells"][:32], 1):
                voltage = numeric_state(cell) if value is not None else None
                battery["values"][f"cell_{index}"] = voltage if voltage is not None else "unavailable"

    for device, battery in batteries.items():
        values = battery["values"]
        if not battery["name"]:
            continue
        current = numeric_state(values.get("current"))
        if current is None:
            mode = "unavailable"
        elif current > STANDBY_CURRENT:
            mode = "charging"
        elif current < -STANDBY_CURRENT:
            mode = "discharging"
        else:
            mode = "standby"
        values["mode"] = mode
        for metric, value in values.items():
            if metric.startswith("cell_"):
                label = f"Cellule {metric[5:]} (V)"
            else:
                label = "Mode estimé" if metric == "mode" else METRICS[metric][1]
            yield (f"sensor.niu_bms_{device}_{metric}", str(value), f"{battery['name']} · {label}")


def read_battery_readings():
    rows = core_api("template", json.dumps({"template": BMS_TEMPLATE}).encode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("Inventaire BMS Home Assistant invalide")
    return tuple(battery_readings(rows))

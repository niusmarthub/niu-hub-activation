"""Match authenticated NiuSmart inventory with local TinyTuya discovery.

Pure reconciliation: no relay commands, no deletion of offline modules, no keys
in the output. The caller must supply an authenticated API response.
"""


def reconcile_modules(feed, discovered, previous=None):
    if not isinstance(feed, dict) or feed.get("success") is not True:
        raise ValueError("Invalid NiuSmart Tuya feed")
    modules = feed.get("modules")
    if not isinstance(modules, list):
        raise ValueError("Invalid Tuya module list")
    if not isinstance(discovered, dict):
        raise ValueError("Invalid discovery result")
    previous = previous if isinstance(previous, dict) else {}
    result = {}
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("Invalid module")
        device_id = module.get("tuya_device_id")
        if not isinstance(device_id, str) or not device_id or device_id in result:
            raise ValueError("Invalid or duplicate Tuya device ID")
        found = discovered.get(device_id)
        ip = found.get("ip") if isinstance(found, dict) else None
        if not isinstance(ip, str) or not ip:
            old = previous.get(device_id)
            ip = old.get("ip") if isinstance(old, dict) else None
            online = False
        else:
            online = True  # Seen by LAN discovery, not a verified connection
        result[device_id] = {
            "device_name": module.get("device_name"),
            "ip": ip,
            "discovered": online,
            "datapoint_count": len(module.get("datapoints", []))
            if isinstance(module.get("datapoints"), list) else 0,
        }
    return result

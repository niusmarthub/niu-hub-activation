"""Read-only LAN discovery of Tuya devices; no local keys or commands.

Discovery is disabled by default until validated on REM-0021.
Results are kept under the add-on private /data directory.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import tinytuya


def discover_once(data_dir="/data"):
    """Return number of discovered devices; never log device IDs or keys."""
    raw = tinytuya.deviceScan()
    found = {}
    if isinstance(raw, dict):
        for device in raw.values():
            if not isinstance(device, dict):
                continue
            device_id = device.get("gwId") or device.get("id")
            ip = device.get("ip")
            if isinstance(device_id, str) and device_id and isinstance(ip, str) and ip:
                found[device_id] = {"ip": ip, "version": device.get("version")}
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "devices": found,
    }
    fd, tmp = tempfile.mkstemp(prefix=".tuya-", dir=path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path / "tuya_discovery.json")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return len(found)

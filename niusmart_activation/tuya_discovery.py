"""Discover Tuya devices on the hub's local network."""
import json
import os
import tempfile
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import tinytuya
from tinytuya.scanner import devices as scan_devices


def scan_results(raw):
    found = {}
    for device in raw.values() if isinstance(raw, dict) else ():
        if not isinstance(device, dict):
            continue
        device_id = device.get("gwId") or device.get("id")
        ip = device.get("ip")
        version = device.get("version")
        if isinstance(device_id, str) and device_id and isinstance(ip, str) and ip:
            found[device_id] = {"ip": ip, "version": str(version) if version else None}
    return found


def active_scan_targets(discovered_ips, anchor_ips):
    """Use one directly connected IPv4 network confirmed by an expected device."""
    anchor_addresses = {IPv4Address(ip) for ip in anchor_ips}
    if not anchor_addresses:
        return []
    private_ranges = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
    networks = set()
    with Path("/proc/net/route").open(encoding="ascii") as routes:
        next(routes)
        for line in routes:
            _, destination, gateway, flags, _, _, _, mask, *_ = line.split()
            if gateway != "00000000" or not int(flags, 16) & 1:
                continue
            address = IPv4Address(int.from_bytes(bytes.fromhex(destination), "little"))
            netmask = IPv4Address(int.from_bytes(bytes.fromhex(mask), "little"))
            network = IPv4Network(f"{address}/{netmask}")
            if (network.prefixlen >= 24 and any(network.subnet_of(block) for block in private_ranges)
                    and any(ip in network for ip in anchor_addresses)):
                networks.add(network)
    if len(networks) != 1:
        return []
    network = networks.pop()
    return [f"{ip}/32" for ip in network.hosts() if str(ip) not in discovered_ips]


def discover_missing(modules, targets):
    """Use only the stored keys for the missing devices and return endpoint metadata."""
    raw = scan_devices(
        discover=False, poll=False, color=False, forcescan=targets,
        wantids=[module["id"] for module in modules], tuyadevices=modules,
    )
    expected = {module["id"] for module in modules}
    target_ips = {target.removesuffix("/32") for target in targets}
    return {
        device_id: info for device_id, info in scan_results(raw).items()
        if device_id in expected and info["ip"] in target_ips and info["version"] not in (None, "0", "0.0")
    }


def discover_once(data_dir="/data"):
    """Return number of discovered devices; never log device IDs or keys."""
    found = scan_results(tinytuya.deviceScan())
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

"""QX210 LuCI traffic sampling. No third-party dependencies or router changes.

Counters are interface totals, not verified cellular-only usage. Never send router
credentials to NiuSmart. State is persisted in /data across add-on restarts.
"""
import http.cookiejar
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG_PATH = Path(os.environ.get("NIU_ROUTER_CONFIG", "/homeassistant/niusmart/router_config.json"))
STATE_PATH = Path(os.environ.get("NIU_ROUTER_USAGE_STATE", "/data/qx210_traffic.json"))
INTERFACE = "eth0"
INTERVAL = 300
_lock = threading.Lock()
_status = {"status": "waiting", "interface": INTERFACE, "source": "QX210 LuCI; LTE-only unverified"}


def _request(opener, url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "NiuHubActivation/traffic"})
    with opener.open(req, timeout=12) as response:
        return response.read(1024 * 1024)


def read_counters():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    host = str(cfg["host"]).strip()
    if not host or any(ch not in "0123456789." for ch in host):
        raise ValueError("Router host must be an IPv4 address")
    username, password = cfg["username"], cfg["password"]
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise ValueError("Missing router credentials")
    base = "http://" + host + "/cgi-bin/luci/"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    payload = urllib.parse.urlencode({"luci_username": username, "luci_password": password}).encode()
    _request(opener, base, payload)
    raw = _request(opener, base + "admin/status/realtime/bandwidth_status/" + INTERFACE)
    readings = json.loads(raw)
    if not isinstance(readings, list) or not readings:
        raise ValueError("No router bandwidth readings")
    last = readings[-1]
    if not isinstance(last, list) or len(last) < 4:
        raise ValueError("Invalid bandwidth reading")
    rx, tx = int(last[1]), int(last[3])
    if rx < 0 or tx < 0:
        raise ValueError("Negative counters")
    return rx, tx



def read_dhcp_leases():
    """Read QX210 DHCP IPv4 leases using the same authenticated LuCI session."""
    from ipaddress import IPv4Address, IPv4Network
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    host = str(cfg["host"]).strip()
    IPv4Address(host)
    username, password = cfg["username"], cfg["password"]
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise ValueError("Missing router credentials")
    base = "http://" + host + "/cgi-bin/luci/"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    payload = urllib.parse.urlencode({"luci_username": username, "luci_password": password}).encode()
    _request(opener, base, payload)
    raw = _request(opener, base + "admin/status/overview?status=1")
    response = json.loads(raw)
    leases = response.get("leases") if isinstance(response, dict) else None
    if not isinstance(leases, list):
        raise ValueError("Router DHCP lease list unavailable")
    lan = IPv4Network("192.168.1.0/24")
    addresses = set()
    for lease in leases:
        if not isinstance(lease, dict):
            continue
        try:
            address = IPv4Address(lease.get("ipaddr", ""))
        except (ValueError, TypeError):
            continue
        if address in lan and address != lan.network_address and address != lan.broadcast_address:
            addresses.add(str(address))
    return sorted(addresses, key=lambda ip: int(IPv4Address(ip)))


def _save_atomic(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def sample():
    rx, tx = read_counters()
    now = int(time.time())
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (FileNotFoundError, ValueError):
        state = {}
    previous = state.get("last")
    delta_rx = delta_tx = 0
    reset = False
    if isinstance(previous, dict):
        old_rx, old_tx = previous.get("rx"), previous.get("tx")
        if isinstance(old_rx, int) and isinstance(old_tx, int):
            if rx >= old_rx and tx >= old_tx:
                delta_rx, delta_tx = rx - old_rx, tx - old_tx
            else:
                reset = True  # do not invent traffic during a reboot or counter rollover
    totals = state.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}
    totals["rx"] = max(0, int(totals.get("rx", 0))) + delta_rx
    totals["tx"] = max(0, int(totals.get("tx", 0))) + delta_tx
    state = {"last": {"rx": rx, "tx": tx, "at": now}, "totals": totals,
             "last_delta": {"rx": delta_rx, "tx": delta_tx}, "counter_reset": reset}
    _save_atomic(state)
    return {"status": "ok", "interface": INTERFACE, "sampled_at": now,
            "rx_bytes": rx, "tx_bytes": tx, "tracked_rx_bytes": totals["rx"],
            "tracked_tx_bytes": totals["tx"], "counter_reset": reset,
            "source": "QX210 LuCI; LTE-only unverified"}


def current_status():
    with _lock:
        return dict(_status)


def polling_loop(stop_event):
    global _status
    while not stop_event.is_set():
        try:
            result = sample()
        except Exception as exc:
            result = {"status": "unavailable", "interface": INTERFACE,
                      "error": type(exc).__name__, "source": "QX210 LuCI"}
        with _lock:
            _status = result
        stop_event.wait(INTERVAL)

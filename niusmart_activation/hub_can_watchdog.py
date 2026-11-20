"""Check and restore the configured Dometic SocketCAN link."""

import json
import subprocess


def can_link():
    result = subprocess.run(
        ["ip", "-j", "-d", "link", "show", "dev", "can1"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    [link] = json.loads(result.stdout)
    if link.get("linkinfo", {}).get("info_kind") != "can":
        raise ValueError("can1 n’est pas une interface CAN")
    return link


def check_can1():
    """Restore an administratively down can1 without cycling an active link."""
    if "UP" in can_link()["flags"]:
        return "up"

    subprocess.run(
        ["ip", "link", "set", "dev", "can1", "up", "type", "can",
         "bitrate", "250000", "restart-ms", "100"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    if "UP" not in can_link()["flags"]:
        raise RuntimeError("can1 est toujours à l’arrêt après la commande")
    return "restored"

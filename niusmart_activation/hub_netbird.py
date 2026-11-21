"""Provision the local NetBird add-on through the Home Assistant Supervisor."""

import ipaddress
import json
import os
import re
import subprocess
import urllib.request


NETBIRD_REPOSITORY = "https://github.com/netbirdio/addon-netbird"
NETBIRD_SLUG = re.compile(r"[a-z0-9]+_netbird")


def supervisor(path, payload=None, timeout=15):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("Supervisor indisponible")
    request = urllib.request.Request("http://supervisor" + path)
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(payload).encode()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if result.get("result") != "ok":
        raise RuntimeError("Le Supervisor a refusé la demande")
    return result.get("data") or {}


def tunnel_ipv4():
    """Find the host NetBird tunnel IPv4 without exposing credentials."""
    try:
        output = subprocess.check_output(
            ["ip", "-j", "-4", "address", "show", "dev", "wt0"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        )
        for link in json.loads(output):
            if "UP" not in link.get("flags", []):
                continue
            for address in link.get("addr_info", []):
                if address.get("family") == "inet" and address.get("scope") == "global":
                    return str(ipaddress.IPv4Address(address["local"]))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return None
    return None


def provision_netbird(settings, hub_name, pending, save_pending):
    installed = [
        row for row in supervisor("/addons").get("addons", [])
        if NETBIRD_SLUG.fullmatch(row.get("slug", ""))
    ]
    if len(installed) > 1:
        return "Plusieurs installations NetBird détectées"

    setup_key = settings.get("setup_key")
    started_here = bool(pending)
    if installed:
        slug = installed[0]["slug"]
        info = supervisor(f"/addons/{slug}/info")
        if info.get("state") == "started":
            if pending == slug:
                save_pending(None)
            address = tunnel_ipv4()
            return f"Tunnel actif · {address}" if address else "En marche · tunnel non détecté"
        if info.get("state") != "stopped":
            return "Démarrage de NetBird en cours"
        if pending and pending != slug:
            return "Installation NetBird différente de celle prévue"
    else:
        if not isinstance(setup_key, str) or not setup_key.strip():
            return "Installation en attente de la clé d’enrôlement"
        repository = settings.get("repository") or NETBIRD_REPOSITORY
        store = supervisor("/store")
        sources = {row.get("source", "").rstrip("/") for row in store.get("repositories", [])
                   if isinstance(row.get("source"), str)}
        if repository.rstrip("/") not in sources:
            supervisor("/store/repositories", {"repository": repository}, timeout=45)
            store = supervisor("/store")
        repository_ids = {
            row.get("slug") for row in store.get("repositories", [])
            if isinstance(row.get("source"), str)
            and row["source"].rstrip("/") == repository.rstrip("/")
        }
        candidates = [
            row["slug"] for row in store.get("addons", [])
            if NETBIRD_SLUG.fullmatch(row.get("slug", ""))
            and row["slug"].rsplit("_", 1)[0] in repository_ids
        ]
        if len(candidates) != 1 or (pending and pending != candidates[0]):
            return "Client NetBird introuvable dans le dépôt configuré"
        slug = candidates[0]
        save_pending(slug)
        supervisor(f"/store/addons/{slug}/install", {}, timeout=120)
        started_here = True

    if started_here:
        if not isinstance(setup_key, str) or not setup_key.strip():
            return "Configuration en attente de la clé d’enrôlement"
        options = {"setup_key": setup_key.strip()}
        hostname = re.sub(r"[^a-z0-9-]+", "-", hub_name.lower()).strip("-")[:63].rstrip("-")
        if hostname:
            options["hostname"] = hostname
        for field in ("management_url", "admin_url"):
            if settings.get(field):
                options[field] = settings[field]
        supervisor(f"/addons/{slug}/options", {"options": options, "boot": "auto"})
    supervisor(f"/addons/{slug}/start", {}, timeout=45)
    if started_here:
        save_pending(None)
    return "Démarrage demandé · connexion au réseau à confirmer"

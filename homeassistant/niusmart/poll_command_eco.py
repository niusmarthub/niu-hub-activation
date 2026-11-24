"""Run the existing NiuSmart command poll at the cadence selected by the local Hub."""
import fcntl
import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ECO_INTERVAL = 20 * 60
MODE_REFRESH_INTERVAL = 5
HUB_STATUS_URL = "http://127.0.0.1:8099/status"
STATE_PATH = Path(tempfile.gettempdir()) / "niusmart-command-poll.json"
POLL_SCRIPT = Path(__file__).with_name("poll_command.sh")


def read_mode():
    # Talk to the Hub locally even when an HTTP proxy is configured.
    opener = build_opener(ProxyHandler({}))
    with opener.open(HUB_STATUS_URL, timeout=1) as response:
        status = json.load(response)
    mode = (status.get("batch_sync") or {}).get("mode")
    return mode if status.get("activated") and mode in {"normal", "eco"} else None


def main():
    with STATE_PATH.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Vérification déjà en cours")
            return 0

        stream.seek(0)
        try:
            state = json.load(stream)
            if not isinstance(state, dict):
                state = {}
        except (ValueError, OSError):
            state = {}

        now = time.monotonic()
        mode = state.get("mode", "normal")
        last_check = state.get("mode_checked_at", float("-inf"))
        if now < last_check or now - last_check >= MODE_REFRESH_INTERVAL:
            try:
                observed = read_mode()
                if observed:
                    mode = observed
                state["hub_status"] = "OK" if observed else "mode absent"
            except (OSError, ValueError) as exc:
                # Preserve the last confirmed mode during a local Hub restart.
                state["hub_status"] = type(exc).__name__
            state["mode"] = mode
            state["mode_checked_at"] = now

        last_poll = state.get("last_poll_at", float("-inf"))
        due = mode != "eco" or now < last_poll or now - last_poll >= ECO_INTERVAL
        if due:
            state["last_poll_at"] = now

        stream.seek(0)
        stream.truncate()
        json.dump(state, stream)
        stream.flush()

        hub_status = state.get("hub_status", "inconnu")
        if due:
            print(f"Mode script : {mode.upper()} | Hub : {hub_status} | Requête NiuSmart lancée", flush=True)
            return subprocess.run(["bash", str(POLL_SCRIPT)], check=False).returncode
        remaining = max(1, int(ECO_INTERVAL - (now - last_poll) + 0.999))
        print(f"Mode script : ÉCONO | Hub : {hub_status} | Prochaine requête dans {remaining} s")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Persistent FIFO for the multisite hub batch endpoint."""
import json
import sqlite3
import shutil
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

class EventQueue:
    def __init__(self, path="/data/niu_batch_events.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                identity TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
                event_at INTEGER NOT NULL, payload TEXT NOT NULL)""")
            db.execute("CREATE TABLE IF NOT EXISTS sync_status (identity TEXT PRIMARY KEY, last_success INTEGER NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_identity_seq ON events(identity,seq)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def identity(config):
        return json.dumps([config["site_id"], config["hub_identifier"],
                           config["api_base_url"], config["device_code"]], separators=(",", ":"))

    def append(self, config, entity_id, source, state, event_at, attributes=None):
        event = {"event_id": str(uuid.uuid4()), "event_at": int(event_at),
                 "entity_id": entity_id, "source": source, "state": state}
        if attributes is not None:
            event["attributes"] = attributes
        payload = json.dumps(event, ensure_ascii=True, allow_nan=False)
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO events(identity,event_id,event_at,payload) VALUES(?,?,?,?)",
                       (self.identity(config), event["event_id"], event["event_at"], payload))
        return event["event_id"]

    def status(self, config):
        with closing(self.connect()) as db:
            count, oldest = db.execute("SELECT COUNT(*), MIN(event_at) FROM events WHERE identity=?", (self.identity(config),)).fetchone()
            row = db.execute("SELECT last_success FROM sync_status WHERE identity=?", (self.identity(config),)).fetchone()
        disk = shutil.disk_usage(self.path.parent)
        return {"pending_events": count, "oldest_event_at": oldest, "last_success": row[0] if row else None, "disk_free_bytes": disk.free, "disk_used_percent": round(100 * disk.used / disk.total, 2), "queue_bytes": sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())}

    def pending(self, config):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT payload FROM events WHERE identity=? ORDER BY seq LIMIT 500",
                              (self.identity(config),)).fetchall()
        events = []
        size = 32
        for (payload,) in rows:
            size += len(payload.encode("utf-8")) + 2
            if size > 1000000:
                if not events:
                    raise ValueError("Oversized queued event")
                break
            events.append(json.loads(payload))
        return events

    def flush_once(self, config, base_url):
        events = self.pending(config)
        if not events:
            return 0
        url = base_url + "/hub_batch_api.php"
        request = Request(url, data=json.dumps({"events": events}, allow_nan=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "application/json"},
                          method="POST")
        request.add_unredirected_header("X-API-Key", config["api_key"])
        with urlopen(request, timeout=30) as response:
            if response.geturl() != url:
                raise RuntimeError("Batch redirect refused")
            reply = json.load(response)
        expected = [event["event_id"] for event in events]
        if (not isinstance(reply, dict) or reply.get("success") is not True
                or reply.get("site_id") != config["site_id"]
                or reply.get("device_code") != config["device_code"]
                or reply.get("accepted") != expected):
            raise RuntimeError("Invalid batch acknowledgement")
        with closing(self.connect()) as db, db:
            db.executemany("DELETE FROM events WHERE identity=? AND event_id=?",
                           [(self.identity(config), event_id) for event_id in expected])
            db.execute("INSERT INTO sync_status(identity,last_success) VALUES(?,?) ON CONFLICT(identity) DO UPDATE SET last_success=excluded.last_success", (self.identity(config), int(time.time())))
        return len(expected)

"""Durable capture and authenticated batch transport for one hub data volume."""
import json
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MAX_BATCH_EVENTS = 500
MAX_BODY_BYTES = 1_000_000
MIB = 1024 * 1024
MAX_QUEUE_BYTES = 256 * MIB
MIN_FREE_BYTES = 128 * MIB
WARNING_FREE_BYTES = 256 * MIB


def post_json(base_url, config, endpoint, payload, *, timeout=10):
    url = f"{base_url}/{endpoint}"
    request = Request(url, data=json.dumps(payload, allow_nan=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Accept": "application/json"},
                      method="POST")
    request.add_unredirected_header("X-API-Key", config["api_key"])
    with urlopen(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise RuntimeError("Redirect refused")
        return json.load(response)


def check_identity(reply, config):
    if (not isinstance(reply, dict) or reply.get("success") is not True
            or type(reply.get("site_id")) is not int or reply["site_id"] != config["site_id"]
            or reply.get("device_code") != config["device_code"]):
        raise RuntimeError("Invalid NiuSmart acknowledgement")


class EventQueue:
    def __init__(self, path="/data/niu_batch_events.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE, event_at INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_identity_seq ON events(identity,seq);
                CREATE TABLE IF NOT EXISTS sync_status (
                    identity TEXT PRIMARY KEY, last_success INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS captured_states (
                    identity TEXT NOT NULL, source TEXT NOT NULL, entity_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, dropped INTEGER NOT NULL,
                    PRIMARY KEY(identity,source,entity_id)
                );
                CREATE TABLE IF NOT EXISTS disk_status (
                    identity TEXT PRIMARY KEY, level TEXT NOT NULL, rejected INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS disk_transitions (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL,
                    id TEXT NOT NULL UNIQUE, active INTEGER NOT NULL
                );
            """)

    def connect(self):
        new_file = not self.path.exists()
        db = sqlite3.connect(self.path, timeout=10)
        try:
            if new_file:
                db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def identity(config):
        return json.dumps([config["site_id"], config["hub_identifier"],
                           config["api_base_url"], config["device_code"]], separators=(",", ":"))

    def disk(self, db):
        usage = shutil.disk_usage(self.path.parent)
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free_pages = db.execute("PRAGMA freelist_count").fetchone()[0]
        size = (pages - free_pages) * db.execute("PRAGMA page_size").fetchone()[0]
        if usage.free < MIN_FREE_BYTES or size >= MAX_QUEUE_BYTES:
            level = "blocked"
        elif usage.free < WARNING_FREE_BYTES or size >= MAX_QUEUE_BYTES * 3 // 4:
            level = "warning"
        else:
            level = "ok"
        return level, usage, size

    @staticmethod
    def record_disk(db, identity, level, rejected=0):
        previous = db.execute("SELECT level FROM disk_status WHERE identity=?", (identity,)).fetchone()
        before = previous[0] if previous else "ok"
        if (before == "ok") != (level == "ok"):
            db.execute("INSERT INTO disk_transitions(identity,id,active) VALUES(?,?,?)",
                       (identity, str(uuid.uuid4()), int(level != "ok")))
        if before != level or rejected:
            db.execute("""INSERT INTO disk_status(identity,level,rejected) VALUES(?,?,?)
                ON CONFLICT(identity) DO UPDATE SET level=excluded.level,rejected=rejected+excluded.rejected""",
                       (identity, level, rejected))

    def append(self, config, entity_id, source, state, event_at, attributes=None, friendly_name=None):
        identity = self.identity(config)
        event = {"event_id": str(uuid.uuid4()), "event_at": int(event_at),
                 "entity_id": entity_id, "source": source, "state": state}
        if attributes:
            event["attributes"] = attributes
        if friendly_name:
            event["friendly_name"] = friendly_name.encode("utf-8")[:255].decode("utf-8", errors="ignore")
        payload = json.dumps(event, ensure_ascii=True, allow_nan=False)
        fingerprint = json.dumps([state, attributes or {}], sort_keys=True, allow_nan=False)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            level, _, _ = self.disk(db)
            old = db.execute("""SELECT fingerprint,dropped FROM captured_states
                WHERE identity=? AND source=? AND entity_id=?""", (identity, source, entity_id)).fetchone()
            if old == (fingerprint, 0) or (old == (fingerprint, 1) and level == "blocked"):
                self.record_disk(db, identity, level)
                return None
            blocked = level == "blocked"
            self.record_disk(db, identity, level, int(blocked))
            db.execute("""INSERT INTO captured_states(identity,source,entity_id,fingerprint,dropped)
                VALUES(?,?,?,?,?) ON CONFLICT(identity,source,entity_id)
                DO UPDATE SET fingerprint=excluded.fingerprint,dropped=excluded.dropped""",
                       (identity, source, entity_id, fingerprint, int(blocked)))
            if blocked:
                return None
            db.execute("INSERT INTO events(identity,event_id,event_at,payload) VALUES(?,?,?,?)",
                       (identity, event["event_id"], event["event_at"], payload))
        return event["event_id"]

    def status(self, config):
        identity = self.identity(config)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            level, usage, size = self.disk(db)
            self.record_disk(db, identity, level)
            count, oldest = db.execute("SELECT COUNT(*),MIN(event_at) FROM events WHERE identity=?", (identity,)).fetchone()
            row = db.execute("SELECT last_success FROM sync_status WHERE identity=?", (identity,)).fetchone()
            rejected = db.execute("SELECT rejected FROM disk_status WHERE identity=?", (identity,)).fetchone()
        return {"pending_events": count, "oldest_event_at": oldest,
                "last_success": row[0] if row else None, "disk_free_bytes": usage.free,
                "disk_used_percent": round(100 * usage.used / usage.total, 2),
                "queue_bytes": size, "disk_status": level, "rejected_events": rejected[0] if rejected else 0}

    def pending(self, config):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT payload FROM events WHERE identity=? ORDER BY seq LIMIT ?",
                              (self.identity(config), MAX_BATCH_EVENTS)).fetchall()
        events = []
        size = 32
        for (payload,) in rows:
            size += len(payload.encode("utf-8")) + 2
            if size > MAX_BODY_BYTES:
                if not events:
                    raise ValueError("Oversized queued event")
                break
            events.append(json.loads(payload))
        return events

    def flush_once(self, config, base_url):
        events = self.pending(config)
        if not events:
            return 0
        try:
            reply = post_json(base_url, config, "hub_batch_api.php", {"events": events}, timeout=30)
        except HTTPError as exc:
            with exc:
                error = json.load(exc) if exc.code == 409 else {}
            if not isinstance(error, dict) or error.get("error") != "entity_not_registered":
                raise
            event = next((item for item in events if (item["entity_id"], item["source"])
                          == (error.get("entity_id"), error.get("source"))), None)
            if event is None:
                raise RuntimeError("Invalid registration request") from exc
            source = event["source"]
            entity_id = event["entity_id"]
            registered = post_json(base_url, config, "device_discovery_api.php", {
                "entity_id": entity_id, "source": source, "platform": source,
                "friendly_name": event.get("friendly_name", entity_id),
            })
            check_identity(registered, config)
            if registered.get("entity_id") != entity_id or registered.get("source") != source:
                raise RuntimeError("Invalid registration acknowledgement")
            return None
        check_identity(reply, config)
        expected = [event["event_id"] for event in events]
        if reply.get("accepted") != expected:
            raise RuntimeError("Invalid batch acknowledgement")
        identity = self.identity(config)
        with closing(self.connect()) as db, db:
            db.executemany("DELETE FROM events WHERE identity=? AND event_id=?",
                           [(identity, event_id) for event_id in expected])
            db.execute("""INSERT INTO sync_status(identity,last_success) VALUES(?,?)
                ON CONFLICT(identity) DO UPDATE SET last_success=excluded.last_success""",
                       (identity, int(time.time())))
        with closing(self.connect()) as db:
            db.execute("PRAGMA incremental_vacuum(32)")
        return len(expected)

    def report(self, config, base_url, mode):
        status = self.status(config)
        identity = self.identity(config)
        with closing(self.connect()) as db:
            transitions = db.execute("""SELECT id,active FROM disk_transitions
                WHERE identity=? ORDER BY seq LIMIT 50""", (identity,)).fetchall()
        payload = {**status, "mode": mode, "disk_transitions": [
            {"id": event_id, "active": bool(active)} for event_id, active in transitions
        ]}
        reply = post_json(base_url, config, "hub_health_api.php", payload)
        check_identity(reply, config)
        if reply.get("accepted") != [event_id for event_id, _ in transitions]:
            raise RuntimeError("Invalid disk transition acknowledgement")
        if transitions:
            with closing(self.connect()) as db, db:
                db.executemany("DELETE FROM disk_transitions WHERE identity=? AND id=?",
                               [(identity, event_id) for event_id, _ in transitions])
        return status

"""Histórico incremental aislado del monitor. Python 3.8+, sin dependencias externas."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import sys
import time

DAY = 86400
RETENTION = 60 * DAY
MAX_BATCH = 128 * 1024
OUTBOX = """
CREATE TABLE IF NOT EXISTS stats_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE,
 created REAL NOT NULL, payload TEXT NOT NULL, bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS stats_dirty (mid INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS stats_unmonitored (qid TEXT PRIMARY KEY,created REAL NOT NULL);
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applied (token TEXT PRIMARY KEY,at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS applied_time ON applied(at);
CREATE TABLE IF NOT EXISTS users (
 account TEXT PRIMARY KEY,origin TEXT NOT NULL,label TEXT NOT NULL,
 ignored INTEGER NOT NULL DEFAULT 0,domains TEXT NOT NULL DEFAULT '[]',last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY,qid TEXT NOT NULL,account TEXT NOT NULL REFERENCES users(account),
 created REAL NOT NULL,accepted REAL,sender TEXT,expected INTEGER,
 removed INTEGER NOT NULL DEFAULT 0,rejected INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS messages_queue ON messages(qid,created DESC);
CREATE INDEX IF NOT EXISTS messages_time ON messages(created);
CREATE INDEX IF NOT EXISTS messages_accepted ON messages(accepted,account);
CREATE TABLE IF NOT EXISTS source_ids (mid INTEGER PRIMARY KEY,id TEXT NOT NULL,qid TEXT NOT NULL,account TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recipients (
 mid TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,address TEXT NOT NULL,
 destination TEXT NOT NULL DEFAULT 'unknown',delivery TEXT,at REAL NOT NULL,
 PRIMARY KEY(mid,address));
CREATE TABLE IF NOT EXISTS buckets (
 minute INTEGER NOT NULL,account TEXT NOT NULL REFERENCES users(account),destination TEXT NOT NULL,
 messages INTEGER NOT NULL,recipients INTEGER NOT NULL,sent INTEGER NOT NULL,
 deferred INTEGER NOT NULL,bounced INTEGER NOT NULL,
 PRIMARY KEY(minute,account,destination));
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY,at REAL NOT NULL,account TEXT NOT NULL REFERENCES users(account),
 kind TEXT NOT NULL,qid TEXT NOT NULL DEFAULT '',destination TEXT NOT NULL DEFAULT 'unknown',
 detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS events_time ON events(at,account);
CREATE TABLE IF NOT EXISTS active (
 kind TEXT NOT NULL,identity TEXT NOT NULL,account TEXT NOT NULL,PRIMARY KEY(kind,identity));
PRAGMA user_version=1;
"""


def private(path, directory=False):
    info = Path(path).lstat()
    import stat
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Ruta de estadísticas no privada: " + str(path))


def connect(path, readonly=False):
    path = Path(path).absolute()
    private(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        private(path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            private(sidecar)
    if readonly:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
        db.execute("PRAGMA query_only=ON")
    else:
        # Crear privado también cuando el llamador no tenga umask 077.
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(str(path), timeout=0.1)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA cache_size=-2048")
    return db


class Capture:
    """Un buzón en la transacción del cursor; el escritor estadístico nunca lo bloquea."""
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.rows, self.size = [], 0
        self.dirty = set()
        self.dropped = 0
        self.limit = config["analytics_outbox_mb"] * 1024 * 1024
        store.db.executescript(OUTBOX)
        store.db.execute("DELETE FROM stats_unmonitored WHERE created<?", (time.time() - RETENTION,))
        self.unmonitored = {r[0] for r in store.db.execute("SELECT qid FROM stats_unmonitored")}
        self.queued = store.db.execute("SELECT COALESCE(SUM(bytes),0) FROM stats_outbox").fetchone()[0]
        keys = ("routing_mode", "local_domains", "external_transports", "local_transports",
                "postfix_config", "postconf_bin", "postmap_bin", "command_timeout")
        self.settings = {k: sorted(config[k]) if isinstance(config[k], set) else config[k] for k in keys}
        self.settings["command_timeout"] = min(2, config["command_timeout"])
        self.settings["ignore"] = sorted(config["ignore"])
        store.db.commit()

    def add(self, record):
        # Los textos son solo registros de sobre/entrega; no se guardan cuerpos ni credenciales.
        length = len(str(record))
        if self.queued + self.size + length > self.limit:
            self.dropped += 1
            return
        self.rows.append(record)
        self.size += length
        if self.size >= MAX_BATCH:
            self.flush()

    def observe(self, when, service, qid, text, auth=None, pickup=None):
        # Solo reinterpretar tráfico que el monitor omite. El resto se sincroniza por SQL.
        unmonitored = ((auth and auth[1].casefold() in self.config["ignore"])
                       or (pickup and (not self.config["monitor_local"] or pickup[1] == "0"
                                       or "local:" + str(int(pickup[1])) in self.config["ignore"])))
        if qid != "NOQUEUE" and unmonitored:
            if qid not in self.unmonitored:
                self.unmonitored.add(qid)
                self.store.db.execute("INSERT OR REPLACE INTO stats_unmonitored VALUES (?,?)", (qid, when))
        elif (auth or pickup) and qid in self.unmonitored:
            self.unmonitored.discard(qid)
            self.store.db.execute("DELETE FROM stats_unmonitored WHERE qid=?", (qid,))
        if qid in self.unmonitored or qid == "NOQUEUE" or text.startswith(("reject:", "milter-reject:", "milter-discard:", "discard:")):
            if len(text) > 8192:
                self.dropped += 1
            self.add(["log", when, service, qid, text[:8192]])
        if service == "qmgr" and text == "removed" and qid in self.unmonitored:
            self.unmonitored.discard(qid)
            self.store.db.execute("DELETE FROM stats_unmonitored WHERE qid=?", (qid,))

    def flush(self, state=None):
        if not self.rows and state is None and not self.dropped:
            return
        if self.dropped:
            self.store.set("analytics_dropped", self.store.get("analytics_dropped", 0) + self.dropped)
        payload = json.dumps({"settings": self.settings, "records": self.rows, "state": state,
                              "dropped": self.dropped}, separators=(",", ":"), ensure_ascii=False)
        size = len(payload.encode("utf-8"))
        if self.queued + size <= self.limit or not self.rows:
            self.store.db.execute("INSERT INTO stats_outbox(token,created,payload,bytes) VALUES (?,?,?,?)",
                                  (secrets.token_hex(16), time.time(), payload, size))
            self.queued += size
        else:
            self.dropped += len(self.rows)
            self.store.set("analytics_dropped", self.store.get("analytics_dropped", 0) + self.dropped)
        self.rows, self.size, self.dropped = [], 0, 0

    def finish(self, now):
        if self.dirty:
            revision = time.time_ns()
            self.store.db.executemany("INSERT INTO stats_dirty SELECT id,? FROM messages WHERE id=? "
                                      "ON CONFLICT(mid) DO UPDATE SET revision=excluded.revision",
                                      [(revision, mid) for mid in self.dirty])
            self.dirty.clear()
        # Un histórico averiado no puede hacer crecer indefinidamente las marcas del monitor.
        pending = self.store.db.execute("SELECT COUNT(*) FROM stats_dirty").fetchone()[0]
        excess = pending - max(10000, self.limit // 1024)
        if excess > 0:
            self.store.db.execute("DELETE FROM stats_dirty WHERE mid IN (SELECT mid FROM stats_dirty ORDER BY mid LIMIT ?)", (excess,))
            self.store.set("analytics_dropped", self.store.get("analytics_dropped", 0) + excess)
        expired = self.store.db.execute("DELETE FROM stats_dirty WHERE mid IN (SELECT d.mid FROM stats_dirty d JOIN messages m "
                                       "ON m.id=d.mid WHERE m.created<? LIMIT 2000)", (now - RETENTION,)).rowcount
        if expired:
            self.store.set("analytics_dropped", self.store.get("analytics_dropped", 0) + expired)
        state = {"at": now, "blocks": [dict(r) for r in self.store.db.execute("SELECT * FROM blocks")],
                 "held": [dict(r) for r in self.store.db.execute("SELECT qid,account,held_at FROM held")],
                 "identities": [json.loads(r[0]) for r in self.store.db.execute(
                     "SELECT value FROM meta WHERE key LIKE 'local_identity_%'")],
                 "coverage": self.store.get("reader_notes", []),
                 "dropped": self.store.get("analytics_dropped", 0)}
        self.flush(state)


def value(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, data):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(data, ensure_ascii=False)))


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class History:
    def __init__(self, path, router_factory=None):
        self.path = Path(path)
        self.db = connect(path)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Versión de estadísticas incompatible")
        try:
            self.db.executescript(SCHEMA)
        except BaseException:
            self.db.close()
            raise
        self.router_factory = router_factory
        self.touched = {}
        self.settings = {}
        self.router = None
        self.route_budget = 0
        self.labels = {}
        self.source_signature = None
        self.db.create_function("message_key", 3, lambda qid, created, account: digest(qid, created, account))
        def delivery_time(line, fallback):
            if line:
                try:
                    import datetime
                    return datetime.datetime.fromisoformat(line.split(" ", 1)[0].replace("Z", "+00:00")).timestamp()
                except ValueError:
                    from outbound_spam import timestamp
                    return timestamp(line, fallback or time.time())
            return fallback or time.time()
        self.db.create_function("delivery_time", 2, delivery_time)

    def sync(self, source):
        """Copia de cambios mediante SQL nativo: no repetir el parser para tráfico monitorizado."""
        info = Path(source).stat()
        signature = (info.st_dev, info.st_ino)
        attached = any(r[1] == "source" for r in self.db.execute("PRAGMA database_list"))
        if attached and self.source_signature != signature:
            self.db.execute("DETACH DATABASE source")
            attached = False
        if not attached:
            self.db.execute("ATTACH DATABASE ? AS source", (Path(source).absolute().as_uri() + "?mode=ro",))
            self.source_signature = signature
        if not self.db.execute("SELECT 1 FROM source.sqlite_master WHERE name='stats_dirty'").fetchone():
            return []
        db = self.db
        db.execute("CREATE TEMP TABLE IF NOT EXISTS changed(mid INTEGER,revision INTEGER,id TEXT PRIMARY KEY)")
        db.execute("CREATE TEMP TABLE IF NOT EXISTS delta(minute INTEGER,account TEXT,destination TEXT,messages INTEGER,"
                   "recipients INTEGER,sent INTEGER,deferred INTEGER,bounced INTEGER)")
        with db:
            db.execute("DELETE FROM changed")
            db.execute("DELETE FROM delta")
            db.execute("INSERT INTO changed SELECT d.mid,d.revision,CASE WHEN s.id IS NOT NULL THEN s.id ELSE "
                       "m.account||'/'||m.qid||'/'||printf('%.6f',m.created) END FROM source.stats_dirty d "
                       "JOIN source.messages m ON m.id=d.mid LEFT JOIN source_ids s ON s.mid=m.id AND s.qid=m.qid "
                       "AND s.account=m.account AND ABS(s.created-m.created)<=2 WHERE m.created>=? ORDER BY d.mid LIMIT 4000", (time.time() - RETENTION,))
            pending = [(r[0], r[1]) for r in db.execute("SELECT mid,revision FROM changed")]
            if not pending:
                return []
            contribution = ("SELECT CAST(m.accepted/60 AS INTEGER)*60,m.account,COALESCE(r.destination,'unknown'),COUNT(DISTINCT m.id),COUNT(r.address),"
                            "COALESCE(SUM(r.delivery='sent'),0),COALESCE(SUM(r.delivery='deferred'),0),"
                            "COALESCE(SUM(r.delivery IN ('bounced','expired')),0) "
                            "FROM messages m JOIN changed c ON c.id=m.id LEFT JOIN recipients r ON r.mid=m.id "
                            "WHERE m.accepted IS NOT NULL AND m.rejected=0 GROUP BY 1,2,3")
            db.execute("INSERT INTO delta " + contribution)
            db.execute("UPDATE delta SET messages=-messages,recipients=-recipients,sent=-sent,deferred=-deferred,bounced=-bounced")
            db.execute("INSERT INTO users(account,origin,label,last_seen) SELECT m.account,CASE WHEN m.account LIKE 'local:%' THEN 'local' ELSE 'smtp' END,"
                       "m.account,MAX(m.created) FROM source.messages m JOIN changed c ON c.mid=m.id GROUP BY m.account "
                       "ON CONFLICT(account) DO UPDATE SET last_seen=MAX(last_seen,excluded.last_seen)")
            db.execute("INSERT INTO messages SELECT c.id,m.qid,m.account,m.created,m.accepted,m.sender,m.nrcpt,m.removed,0 "
                       "FROM source.messages m JOIN changed c ON c.mid=m.id WHERE 1 "
                       "ON CONFLICT(id) DO UPDATE SET accepted=excluded.accepted,sender=excluded.sender,expected=excluded.expected,removed=excluded.removed")
            db.execute("INSERT INTO source_ids SELECT c.mid,c.id,m.qid,m.account,m.created FROM changed c JOIN source.messages m ON m.id=c.mid WHERE 1 "
                       "ON CONFLICT(mid) DO UPDATE SET id=excluded.id,qid=excluded.qid,account=excluded.account,created=excluded.created")
            db.execute("DELETE FROM recipients WHERE mid IN (SELECT id FROM changed)")
            db.execute("INSERT INTO recipients SELECT c.id,r.address,CASE r.external WHEN 1 THEN 'remote' WHEN 0 THEN 'local' ELSE 'unknown' END,"
                       "r.delivery,delivery_time(r.log_line,m.accepted) FROM source.recipients r JOIN changed c ON c.mid=r.mid "
                       "JOIN source.messages m ON m.id=c.mid")
            db.execute("INSERT OR IGNORE INTO events SELECT r.mid||'/'||r.address||'/sent',r.at,m.account,'sent',m.qid,r.destination,'' "
                       "FROM recipients r JOIN changed c ON c.id=r.mid JOIN messages m ON m.id=r.mid WHERE r.delivery='sent'")
            db.execute("INSERT INTO delta " + contribution)
            db.execute("INSERT INTO buckets SELECT minute,account,destination,SUM(messages),SUM(recipients),SUM(sent),SUM(deferred),SUM(bounced) "
                       "FROM delta GROUP BY 1,2,3 HAVING SUM(messages)!=0 OR SUM(recipients)!=0 OR SUM(sent)!=0 OR SUM(deferred)!=0 OR SUM(bounced)!=0 "
                       "ON CONFLICT(minute,account,destination) DO UPDATE SET messages=messages+excluded.messages,recipients=recipients+excluded.recipients,"
                       "sent=sent+excluded.sent,deferred=deferred+excluded.deferred,bounced=bounced+excluded.bounced")
            put(db, "updated_at", time.time())
        return pending

    def user(self, account, at):
        local = account.startswith("local:")
        label = account
        if local:
            cached = self.labels.get(account)
            if cached and time.monotonic() - cached[0] < 300:
                label = cached[1]
            else:
                try:
                    import pwd
                    label = pwd.getpwuid(int(account[6:])).pw_name + " (" + account + ")"
                except (KeyError, ValueError):
                    pass
                if len(self.labels) >= 2048:
                    self.labels.clear()
                self.labels[account] = (time.monotonic(), label)
        self.db.execute("INSERT INTO users(account,origin,label,ignored,last_seen) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(account) DO UPDATE SET last_seen=MAX(last_seen,excluded.last_seen),ignored=excluded.ignored",
                        (account, "local" if local else "smtp", label, int(account in self.settings.get("ignore", [])), at))

    def event(self, identity, at, account, kind, qid="", destination="unknown", detail=""):
        self.user(account, at)
        self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                        (identity, at, account, kind, qid, destination, detail[:500]))

    def current(self, qid):
        return self.db.execute("SELECT * FROM messages WHERE qid=? ORDER BY created DESC,rowid DESC LIMIT 1", (qid,)).fetchone()

    def contribution(self, mid):
        m = self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not m or m["accepted"] is None or m["rejected"]:
            return {}
        result = {}
        for row in self.db.execute("SELECT destination,COUNT(*) n,SUM(delivery='sent') sent,"
                                   "SUM(delivery='deferred') deferred,SUM(delivery IN ('bounced','expired')) bounced "
                                   "FROM recipients WHERE mid=? GROUP BY destination", (mid,)):
            key = (int(m["accepted"] // 60) * 60, m["account"], row["destination"])
            result[key] = (1, row["n"], row["sent"] or 0, row["deferred"] or 0, row["bounced"] or 0)
        if not result:
            result[(int(m["accepted"] // 60) * 60, m["account"], "unknown")] = (1, 0, 0, 0, 0)
        return result

    def touch(self, mid):
        if mid not in self.touched:
            self.touched[mid] = self.contribution(mid)

    def destination(self, address, service=None, text="", sender=""):
        if service in self.settings.get("local_transports", []):
            return "local"
        if service in self.settings.get("external_transports", []) and not re.search(r"relay=[^\s]*\[(?:127\.|::1\])", text):
            return "remote"
        if self.settings.get("routing_mode") == "static":
            if "@" not in address:
                return "unknown"
            return "local" if address.rsplit("@", 1)[1].casefold() in self.settings["local_domains"] else "remote"
        if self.router_factory and self.route_budget > 0:
            self.route_budget -= 1
            try:
                if self.router is None:
                    self.router = self.router_factory(self.settings)
                return "remote" if self.router.external(address, sender) else "local"
            except (ValueError, RuntimeError):
                pass
        return "unknown"

    def log(self, at, service, qid, text):
        auth = re.search(r"(?:^|, )sasl_username=([^,\s]+)", text) if service == "smtpd" else None
        pickup = re.match(r"uid=(\d+) from=<(.*?)>", text) if service == "pickup" else None
        identity = digest(at, service, qid, text)
        if qid == "NOQUEUE":
            if auth and "reject:" in text and not auth[1].casefold().startswith("local:"):
                address = re.search(r"to=<(.*?)>", text)
                dest = self.destination(address[1]) if address else "unknown"
                self.event(identity, at, auth[1].casefold(), "rejected", destination=dest, detail=text)
            return
        if (auth and text.startswith("client=")) or pickup:
            account = "local:" + str(int(pickup[1])) if pickup else auth[1].casefold()
            if not pickup and account.startswith("local:"):
                return
            self.user(account, at)
            self.db.execute("INSERT OR IGNORE INTO messages(id,qid,account,created,sender) VALUES (?,?,?,?,?)",
                            (identity, qid, account, at, pickup[2] if pickup else None))
            return
        m = self.current(qid)
        if not m:
            return
        mid, account = m["id"], m["account"]
        self.touch(mid)
        if service == "qmgr":
            if text == "removed":
                self.db.execute("UPDATE messages SET removed=1 WHERE id=?", (mid,))
            else:
                found = re.search(r"from=<(.*?)>, size=\d+, nrcpt=(\d+)", text)
                if found:
                    self.db.execute("UPDATE messages SET accepted=COALESCE(accepted,?),sender=?,expected=? WHERE id=?",
                                    (at, found[1], int(found[2]), mid))
        elif service == "cleanup":
            if text.startswith(("reject:", "milter-reject:", "milter-discard:", "discard:")):
                self.db.execute("UPDATE messages SET rejected=1 WHERE id=?", (mid,))
                self.event(identity, at, account, "rejected", qid, detail=text)
        else:
            found = re.search(r"^to=<(.*?)>.*?status=(sent|deferred|bounced|expired)", text)
            if found:
                address, status = found.groups()
                local, sep, domain = address.rpartition("@")
                address = local + sep + domain.casefold() if sep else address
                old = self.db.execute("SELECT delivery FROM recipients WHERE mid=? AND address=?", (mid, address)).fetchone()
                dest = self.destination(address, service, text, m["sender"] or "")
                self.db.execute("INSERT INTO recipients VALUES (?,?,?,?,?) ON CONFLICT(mid,address) DO UPDATE SET "
                                "destination=excluded.destination,delivery=excluded.delivery,at=excluded.at WHERE excluded.at>=recipients.at",
                                (mid, address, dest, status, at))
                if status == "sent" and (not old or old[0] != "sent"):
                    self.event(digest(mid, address, "sent"), at, account, "sent", qid, dest)

    def snapshot(self, state):
        if not state:
            return
        now = state["at"]
        for identity in state["identities"]:
            account = "local:" + str(identity["uid"])
            self.db.execute("UPDATE users SET label=?,domains=? WHERE account=?",
                            (identity["user"] + " (" + account + ")", json.dumps(identity.get("domains") or []), account))
        active = {}
        for row in state["blocks"]:
            active[("blocked", row["account"])] = (row["account"], now, "")
        for row in state["held"]:
            active[("held", row["qid"])] = (row["account"], row["held_at"], row["qid"])
        old = {(r["kind"], r["identity"]): r["account"] for r in self.db.execute("SELECT * FROM active")}
        for key, (account, at, qid) in active.items():
            if key not in old:
                self.event(digest(self.token, key, "start"), at, account, key[0], qid,
                           "remote" if key[0] == "held" else "unknown")
        for key, account in old.items():
            if key not in active:
                self.event(digest(self.token, key, "end"), now, account,
                           "unblocked" if key[0] == "blocked" else "hold_ended", key[1] if key[0] == "held" else "")
        self.db.execute("DELETE FROM active")
        self.db.executemany("INSERT INTO active VALUES (?,?,?)", [(k[0], k[1], v[0]) for k, v in active.items()])
        put(self.db, "coverage", state["coverage"])
        put(self.db, "dropped", state["dropped"])
        put(self.db, "analysed_until", now)

    def apply(self, token, payload, now=None):
        now = time.time() if now is None else now
        if self.db.execute("SELECT 1 FROM applied WHERE token=?", (token,)).fetchone():
            return False
        data = json.loads(payload)
        self.settings, self.router, self.route_budget = data["settings"], None, 8
        self.touched, self.token = {}, token
        with self.db:
            # La condición ignorada corresponde a la configuración de la última pasada exportada.
            previous = value(self.db, "ignore", None)
            if previous != self.settings["ignore"]:
                self.db.execute("UPDATE users SET ignored=0")
                self.db.executemany("UPDATE users SET ignored=1 WHERE account=?", [(a,) for a in self.settings["ignore"]])
                put(self.db, "ignore", self.settings["ignore"])
            for record in data["records"]:
                if record[1] < now - RETENTION:
                    continue
                if record[0] == "log":
                    self.log(*record[1:])
                elif record[0] == "recipient":
                    _, at, qid, created, accepted, account, address, external = record
                    m = self.current(qid)
                    if m and m["account"] == account and abs(m["created"] - created) < 2:
                        self.touch(m["id"])
                        self.db.execute("UPDATE messages SET accepted=COALESCE(accepted,?) WHERE id=?", (accepted, m["id"]))
                        self.db.execute("INSERT INTO recipients VALUES (?,?,?,?,?) ON CONFLICT(mid,address) DO UPDATE SET "
                                        "destination=excluded.destination", (m["id"], address, "remote" if external else "local", None, at))
                elif record[0] == "seed":
                    _, at, qid, created, account, sender = record
                    m = self.current(qid)
                    if not m or m["account"] != account or abs(m["created"] - created) > 2:
                        self.user(account, at)
                        mid = digest("seed", qid, created, account)
                        self.db.execute("INSERT OR IGNORE INTO messages(id,qid,account,created,sender) VALUES (?,?,?,?,?)",
                                        (mid, qid, account, created, sender))
                        m = self.current(qid)
                    self.touch(m["id"])
                    self.db.execute("UPDATE messages SET accepted=COALESCE(accepted,?),sender=? WHERE id=?", (at, sender, m["id"]))
            for mid, before in self.touched.items():
                after = self.contribution(mid)
                for key in before.keys() | after.keys():
                    delta = tuple(b - a for a, b in zip(before.get(key, (0,) * 5), after.get(key, (0,) * 5)))
                    if not any(delta):
                        continue
                    self.db.execute("INSERT INTO buckets VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(minute,account,destination) "
                                    "DO UPDATE SET messages=messages+excluded.messages,recipients=recipients+excluded.recipients,"
                                    "sent=sent+excluded.sent,deferred=deferred+excluded.deferred,bounced=bounced+excluded.bounced", key + delta)
            self.snapshot(data.get("state"))
            if data.get("dropped"):
                put(self.db, "loss_detected_at", now)
            self.db.execute("INSERT INTO applied VALUES (?,?)", (token, now))
            put(self.db, "updated_at", now)
            if value(self.db, "started_at") is None:
                put(self.db, "started_at", now)
        return True

    def maintain(self, now=None):
        """Mantenimiento espaciado, con borrados acotados; nunca VACUUM completo."""
        now = time.time() if now is None else now
        if now - value(self.db, "maintenance_at", 0) < 300:
            return
        floor = now - RETENTION
        with self.db:
            for table, column in (("messages", "created"), ("events", "at"), ("applied", "at"), ("buckets", "minute")):
                self.db.execute("DELETE FROM %s WHERE rowid IN (SELECT rowid FROM %s WHERE %s<? LIMIT 2000)" %
                                (table, table, column), (floor,))
            self.db.execute("DELETE FROM source_ids WHERE mid IN (SELECT mid FROM source_ids WHERE created<? LIMIT 2000)", (floor,))
            self.db.execute("DELETE FROM users WHERE account IN (SELECT account FROM users WHERE last_seen<? LIMIT 100) "
                            "AND NOT EXISTS(SELECT 1 FROM messages WHERE messages.account=users.account) "
                            "AND NOT EXISTS(SELECT 1 FROM buckets WHERE buckets.account=users.account) "
                            "AND NOT EXISTS(SELECT 1 FROM events WHERE events.account=users.account) "
                            "AND NOT EXISTS(SELECT 1 FROM active WHERE active.account=users.account)", (floor,))
            put(self.db, "maintenance_at", now)
        if now - value(self.db, "checked_at", 0) > DAY:
            if [r[0] for r in self.db.execute("PRAGMA quick_check")] != ["ok"]:
                raise sqlite3.DatabaseError("Falló quick_check del histórico")
            if self.db.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.DatabaseError("Falló foreign_key_check del histórico")
            with self.db:
                put(self.db, "checked_at", now)
        self.db.execute("PRAGMA main.wal_checkpoint(PASSIVE)")
        if now - value(self.db, "backup_at", 0) > DAY:
            self.backup(now)

    def backup(self, now):
        target = self.path.with_name("analytics-backup-%d.sqlite3" % now)
        with contextlib.closing(connect(target)) as backup:
            self.db.backup(backup, pages=128, sleep=0.01)
            if [r[0] for r in backup.execute("PRAGMA quick_check")] != ["ok"]:
                raise sqlite3.DatabaseError("Copia estadística no válida")
            backup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with self.db:
            put(self.db, "backup_at", now)
        for old in sorted(self.path.parent.glob("analytics-backup-*.sqlite3"), reverse=True)[2:]:
            old.unlink()


def restore(directory, backup):
    """Restauración explícita, con trabajador parado; conserva el fichero dañado para diagnóstico."""
    import fcntl
    import shutil
    directory, backup = Path(directory).absolute(), Path(backup).absolute()
    private(directory, directory=True)
    private(backup)
    with (directory / "analytics.lock").open("a") as lock:
        os.chmod(str(directory / "analytics.lock"), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with contextlib.closing(connect(backup, readonly=True)) as db:
            if [r[0] for r in db.execute("PRAGMA quick_check")] != ["ok"] or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Copia dañada; no se restaura")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Copia con esquema incompatible")
            for table in ("users", "messages", "recipients", "buckets", "events", "applied", "meta"):
                db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone()
        path = directory / "analytics.sqlite3"
        if backup == path:
            raise ValueError("Seleccione una copia, no la base activa")
        quarantine = directory / ("analytics-damaged-%d-%s" % (time.time(), secrets.token_hex(4)))
        quarantine.mkdir(mode=0o700)
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(path) + suffix)
            if source.exists() or source.is_symlink():
                private(source)
                source.rename(quarantine / source.name)
        shutil.copyfile(backup, path)
        path.chmod(0o600)
        with contextlib.closing(connect(path)) as db:
            with db:
                put(db, "restored_at", time.time())
                put(db, "loss_detected_at", time.time())
        return str(quarantine)


def verify(path):
    """Comprobar solamente la base estadística, nunca el estado operativo adjunto."""
    with contextlib.closing(connect(path, readonly=True)) as db:
        if [r[0] for r in db.execute("PRAGMA main.quick_check")] != ["ok"]:
            raise sqlite3.DatabaseError("Integridad estadística incorrecta")
        if db.execute("PRAGMA main.foreign_key_check").fetchone():
            raise sqlite3.DatabaseError("Referencias estadísticas incorrectas")
        for table in ("users", "messages", "recipients", "buckets", "events", "applied", "meta"):
            db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone()
        try:
            for row in db.execute("SELECT value FROM meta"):
                json.loads(row[0])
        except (ValueError, TypeError) as exc:
            raise sqlite3.DatabaseError("Metadatos estadísticos incorrectos") from exc


def recover_history(path, router_factory=None):
    """El llamador posee analytics.lock. Aislar daños, restaurar copia o empezar un histórico nuevo."""
    path = Path(path)
    root = path.parent
    recovery = root / ("analytics-damaged-%d-%s" % (time.time(), secrets.token_hex(4)))
    recovery.mkdir(mode=0o700)
    for suffix in ("", "-wal", "-shm"):
        item = Path(str(path) + suffix)
        if item.exists() or item.is_symlink():
            # Un enlace no se sigue ni se modifica: se aísla el propio enlace.
            item.rename(recovery / item.name)
    chosen = None
    for backup in sorted(root.glob("analytics-backup-*.sqlite3"), reverse=True):
        try:
            verify(backup)
            with contextlib.closing(connect(backup, readonly=True)) as saved:
                with contextlib.closing(connect(path)) as destination:
                    saved.backup(destination, pages=128, sleep=0.01)
            chosen = backup.name
            break
        except (OSError, ValueError, sqlite3.Error):
            continue
    history = History(path, router_factory)
    with history.db:
        put(history.db, "restored_at", time.time())
        put(history.db, "loss_detected_at", time.time())
        put(history.db, "recovery", {"backup": chosen, "quarantine": recovery.name,
                                     "note": "Copia restaurada" if chosen else "Histórico nuevo; no había copia válida"})
    return history


def open_history(path, router_factory=None):
    from state_recovery import corruption
    try:
        if Path(path).exists():
            verify(path)
        return History(path, router_factory)
    except sqlite3.DatabaseError as exc:
        # No confundir falta de espacio, bloqueos o incompatibilidad con corrupción.
        if corruption(exc) or "incorrect" in str(exc) or "no such table" in str(exc):
            return recover_history(path, router_factory)
        raise


def drain(directory, history, limit=8):
    """Leer sin bloqueo operativo; confirmar el lote antes de intentar retirarlo del buzón."""
    directory = Path(directory)
    source = directory / "state.sqlite3"
    if not source.exists():
        return 0
    pending = history.sync(source)
    with contextlib.closing(connect(source, readonly=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='stats_outbox'").fetchone():
            return 0
        rows = db.execute("SELECT id,token,payload FROM stats_outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
    for row in rows:
        history.apply(row["token"], row["payload"])
    if rows or pending:
        # busy_timeout corto: si el monitor está escribiendo, se reintentará sin frenarlo.
        try:
            with contextlib.closing(sqlite3.connect(str(source), timeout=0)) as db:
                with db:
                    db.executemany("DELETE FROM stats_dirty WHERE mid=? AND revision=?", pending)
                    db.executemany("DELETE FROM stats_outbox WHERE id=? AND token=?", [(r["id"], r["token"]) for r in rows])
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc):
                raise
    return len(rows) + len(pending)


def worker(directory):
    import fcntl
    import runtime
    # Mantener pequeño el proceso en reposo: cargar Postfix solo si una ruta lo necesita.
    def factory(config):
        from outbound_spam import Router, Commands
        return Router(config, Commands(config))
    def atomic_write(path, text):
        temporary = path.with_name("." + path.name + "-" + secrets.token_hex(4))
        fd = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.replace(str(temporary), str(path))
        finally:
            if temporary.exists():
                temporary.unlink()
    directory = Path(directory)
    private(directory, directory=True)
    stopped = [False]
    def stop(signum, frame):
        stopped[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (directory / "analytics.lock").open("a") as lock:
        os.chmod(str(directory / "analytics.lock"), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with contextlib.suppress(OSError):
            os.nice(10)
        path = directory / "analytics.sqlite3"
        history = None
        last_work = {"last_work_at": None, "last_work_duration_seconds": None,
                     "last_work_cpu_seconds": None, "last_work_items": 0}
        try:
            while not stopped[0]:
                started = time.monotonic()
                cpu_started = time.process_time()
                try:
                    if history is None:
                        history = open_history(path, factory)
                    count = drain(directory, history, limit=4)
                    history.maintain()
                    duration = round(time.monotonic() - started, 6)
                    if count:
                        last_work = {"last_work_at": time.time(), "last_work_duration_seconds": duration,
                                     "last_work_cpu_seconds": round(time.process_time() - cpu_started, 6),
                                     "last_work_items": count}
                    atomic_write(directory / "analytics-status.json", json.dumps({
                        "pid": os.getpid(), "heartbeat": time.time(), "ok": True,
                        "updated_at": value(history.db, "updated_at"),
                        "analysed_until": value(history.db, "analysed_until"),
                        "duration_seconds": duration, "processed_items": count, **last_work}))
                except (OSError, ValueError, sqlite3.Error) as exc:
                    atomic_write(directory / "analytics-status.json", json.dumps({
                        "pid": os.getpid(), "heartbeat": time.time(), "ok": False, "error": str(exc), **last_work}))
                    runtime.audit("estadisticas_error", error=str(exc))
                    if history is not None:
                        history.db.close()
                        history = None
                    count = 0
                # Sin sondeo continuo: 5 segundos en reposo, pequeños descansos con backlog.
                until = time.monotonic() + (0.5 if count else 5)
                while not stopped[0] and time.monotonic() < until:
                    time.sleep(0.2)
        finally:
            if history is not None:
                history.db.close()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--restore":
        print(restore(sys.argv[2], sys.argv[3]))
    else:
        worker(sys.argv[1])

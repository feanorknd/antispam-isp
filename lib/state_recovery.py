"""Copias SQLite consistentes y diario independiente de operaciones críticas."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

TABLES = ("incidents", "actions", "held", "blocks", "acknowledgements", "notice_state", "queue_ops")
META = ("guard_installed", "guard_restart_pending", "local_guard_installed", "recovery_pause")
PREFIXES = ("hold_suppressed_", "resolution_", "local_identity_", "local_queue_")


class CorruptDatabase(ValueError):
    pass


def corruption(exc):
    code = getattr(exc, "sqlite_errorcode", 0) & 255
    return isinstance(exc, CorruptDatabase) or code in (11, 26) or any(
        text in str(exc).lower() for text in ("database disk image is malformed", "file is not a database", "malformed database"))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sealed(value):
    return json.dumps({"data": value, "sha256": hashlib.sha256(canonical(value)).hexdigest()}, ensure_ascii=False)


def private(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Archivo de recuperación no privado: " + str(path))


def read_sealed(path):
    private(path)
    record = json.loads(Path(path).read_text())
    if hashlib.sha256(canonical(record["data"])).hexdigest() != record["sha256"]:
        raise ValueError("Checksum de recuperación incorrecto")
    return record["data"]


def check_database(path, required=(), protected=True, deep=False):
    path = Path(path)
    if protected:
        private(path)
    if path.stat().st_size < 100:
        raise CorruptDatabase("Archivo SQLite vacío o truncado")
    try:
        with contextlib.closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = [r[0] for r in db.execute("PRAGMA quick_check")]
            if rows != ["ok"]:
                raise CorruptDatabase("Comprobación SQLite incorrecta")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if set(required) - tables:
                raise ValueError("SQLite sin el esquema esperado")
            if "meta" in required:
                if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3, 4):
                    raise ValueError("Versión SQLite de aplicación no compatible")
            checks = [("meta", "value")] if "meta" in required else []
            if deep:
                checks += [("messages", "samples"), ("incidents", "snapshot"), ("actions", "payload")]
            for table, column in checks:
                if table in tables:
                    for row in db.execute("SELECT " + column + " FROM " + table):
                        try:
                            json.loads(row[0])
                        except (ValueError, TypeError):
                            raise CorruptDatabase("Datos JSON dañados en SQLite")
            if "meta" in required:
                mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
                if mode and json.loads(mode[0]) not in ("live", "offline"):
                    raise CorruptDatabase("Modo de estado SQLite dañado")
    except sqlite3.DatabaseError as exc:
        if corruption(exc):
            raise CorruptDatabase("SQLite dañado") from exc
        raise


class Recovery:
    def __init__(self, directory, config, write_atomic):
        self.directory, self.c, self.write = Path(directory), config, write_atomic
        self.root = self.directory / "recovery"
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Directorio de recuperación no privado")
        self.manifest = None
        self.pending, self.committed = [], []
        self.in_transaction = False
        self.note = None
        self.failed = False

    def quarantine(self, paths, reason):
        folder = self.root / ("damaged-%d-%s" % (time.time(), secrets.token_hex(4)))
        folder.mkdir(mode=0o700)
        for path in paths:
            path = Path(path)
            if path.exists():
                if path.is_symlink():
                    raise ValueError("No se mueve un enlace de recuperación")
                os.replace(str(path), str(folder / path.name))
        self.write(folder / "reason.json", json.dumps({"at": time.time(), "reason": reason}))
        return folder

    def read_critical(self):
        candidates = []
        for name in ("manifest.json", "manifest-mirror.json"):
            try:
                item = read_sealed(self.root / name)
                if not re.fullmatch(r"[a-f0-9]{24}", item["epoch"]):
                    raise ValueError("Epoch inválido")
                candidates.append(item)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not candidates:
            raise ValueError("Sin manifiesto crítico verificable")
        manifest = max(candidates, key=lambda x: (x["generation"], x["sequence"]))
        epoch = manifest["epoch"]
        snapshots = []
        for suffix in ("snapshot", "mirror"):
            try:
                item = read_sealed(self.root / (epoch + "." + suffix))
                if item["epoch"] == epoch and hashlib.sha256(canonical(item)).hexdigest() == manifest["base_hash"]:
                    snapshots.append(item)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not snapshots:
            raise ValueError("Sin instantánea crítica verificable")
        journal = self.root / (epoch + ".journal")
        private(journal)
        sequence, previous, transactions = 0, manifest["base_hash"], []
        with journal.open("rb") as stream:
            for raw in stream:
                try:
                    item = json.loads(raw)
                    record = item["data"]
                    digest = hashlib.sha256(canonical(record)).hexdigest()
                    if item["sha256"] != digest or record["previous"] != previous or record["sequence"] != sequence + 1:
                        raise ValueError("Cadena crítica incorrecta")
                    if not isinstance(record["sql"], list) or not all(self.is_critical(sql) for sql in record["sql"]):
                        raise ValueError("Operación crítica no reconocida")
                    sequence, previous = record["sequence"], digest
                    transactions.append(record["sql"])
                except (ValueError, KeyError, TypeError):
                    if sequence < manifest["sequence"]:
                        raise ValueError("Diario crítico dañado")
                    # Cola interrumpida antes de publicar: no pudo autorizar un efecto externo.
                    break
        if sequence < manifest["sequence"]:
            raise ValueError("Diario crítico truncado")
        if sequence == manifest["sequence"] and previous != manifest["tail_hash"]:
            raise ValueError("Final crítico no verificable")
        return snapshots[0], transactions, manifest, sequence

    @staticmethod
    def is_critical(sql):
        match = re.match(r"\s*(?:INSERT(?: OR \w+)? INTO|UPDATE|DELETE FROM)\s+([A-Za-z_][A-Za-z0-9_]*)\b", sql, re.I)
        if not match:
            return False
        table = match.group(1).lower()
        if table in TABLES:
            return True
        if table == "meta":
            key = re.search(r"VALUES\s*\(\s*'([^']+)'", sql, re.I)
            return bool(key and (key[1] in META or key[1].startswith(PREFIXES)))
        return False

    def trace(self, sql):
        verb = sql.strip().upper()
        if verb.startswith("BEGIN"):
            self.pending, self.in_transaction = [], True
        elif verb == "ROLLBACK":
            self.pending, self.in_transaction = [], False
        elif verb == "COMMIT":
            if self.pending:
                self.committed.append(self.pending)
            self.pending, self.in_transaction = [], False
        elif self.is_critical(sql):
            if self.in_transaction:
                self.pending.append(sql)
            else:
                self.committed.append([sql])

    def restore_critical(self, store, saved):
        snapshot, transactions, _, _ = saved
        db = store.db
        with db:
            for table in ("queue_ops", "held", "blocks", "actions", "incidents", "acknowledgements", "notice_state"):
                db.execute("DELETE FROM " + table)
            for table in TABLES:
                data = snapshot["tables"].get(table)
                if not data:
                    continue
                actual = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
                columns = data["columns"]
                if not columns or set(columns) - actual or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c) for c in columns):
                    raise ValueError("Columnas críticas no compatibles")
                db.executemany("INSERT INTO %s(%s) VALUES (%s)" % (table, ",".join(columns), ",".join("?" for _ in columns)), data["rows"])
            for row in db.execute("SELECT key FROM meta").fetchall():
                if row[0] in META or row[0].startswith(PREFIXES):
                    db.execute("DELETE FROM meta WHERE key=?", (row[0],))
            db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", snapshot["meta"])
            for statements in transactions:
                for sql in statements:
                    db.execute(sql)

    @staticmethod
    def payload(store):
        tables = {}
        for table in TABLES:
            cursor = store.db.execute("SELECT * FROM " + table)
            tables[table] = {"columns": [c[0] for c in cursor.description], "rows": sorted([list(r) for r in cursor], key=canonical)}
        metadata = sorted([list(r) for r in store.db.execute("SELECT key,value FROM meta") if r[0] in META or r[0].startswith(PREFIXES)])
        return tables, metadata

    def expected_payload(self, store, critical):
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            for table in ("meta",) + TABLES:
                ddl = store.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                db.execute(ddl)
            holder = type("CriticalState", (), {"db": db})()
            self.restore_critical(holder, critical)
            return self.payload(holder)

    def checkpoint(self, store, generation=0):
        epoch = secrets.token_hex(12)
        tables, metadata = self.payload(store)
        snapshot = {"format": 1, "epoch": epoch, "tables": tables, "meta": metadata}
        digest = hashlib.sha256(canonical(snapshot)).hexdigest()
        for suffix in ("snapshot", "mirror"):
            self.write(self.root / (epoch + "." + suffix), sealed(snapshot))
        self.write(self.root / (epoch + ".journal"), "")
        self.manifest = {"format": 1, "epoch": epoch, "generation": generation + 1,
                         "sequence": 0, "base_hash": digest, "tail_hash": digest,
                         "traffic_hash": self.traffic_hash(store.db)}
        self.write_manifests()
        store.set("critical_epoch", epoch)
        store.set("critical_sequence", 0)
        store.db.commit()
        # Conservar la generación anterior; los dañados se guardan en quarantine.
        snapshots = sorted(self.root.glob("*.snapshot"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in snapshots[2:]:
            for suffix in ("snapshot", "mirror", "journal"):
                candidate = old.with_suffix("." + suffix)
                if candidate.exists():
                    candidate.unlink()

    def write_manifests(self):
        for name in ("manifest.json", "manifest-mirror.json"):
            self.write(self.root / name, sealed(self.manifest))

    @staticmethod
    def traffic_hash(db):
        fields = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('cursor','retired')"))
        return hashlib.sha256(canonical(fields)).hexdigest()

    def attach(self, store, critical=None, restored=False):
        generation = 0
        if critical is not None:
            _, _, manifest, sequence = critical
            generation = manifest["generation"]
            different = restored or store.get("critical_epoch") != manifest["epoch"] or store.get("critical_sequence", -1) < sequence
            if not different:
                different = canonical(self.payload(store)) != canonical(self.expected_payload(store, critical))
            if different:
                self.restore_critical(store, critical)
                if not self.note:
                    self.note = "Estado crítico reconciliado con el diario independiente"
        self.checkpoint(store, generation)
        store.recovery = self
        store.db.set_trace_callback(self.trace)

    def capture(self, store):
        # persist_critical ha confirmado SQLite. Publicar el diario antes del efecto externo.
        if self.failed:
            raise ValueError("Registro crítico no disponible; no se autorizan más efectos en esta pasada")
        try:
            self._capture(store)
        except Exception:
            self.failed = True
            raise

    def _capture(self, store):
        while self.committed:
            statements = self.committed[0]
            record = {"sequence": self.manifest["sequence"] + 1, "previous": self.manifest["tail_hash"], "sql": statements}
            digest = hashlib.sha256(canonical(record)).hexdigest()
            path = self.root / (self.manifest["epoch"] + ".journal")
            private(path)
            with path.open("ab") as stream:
                stream.write((sealed(record) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            self.manifest.update(sequence=record["sequence"], tail_hash=digest)
            self.write_manifests()
            self.committed.pop(0)
        store.set("critical_epoch", self.manifest["epoch"])
        store.set("critical_sequence", self.manifest["sequence"])
        store.db.commit()
        traffic = self.traffic_hash(store.db)
        if traffic != self.manifest.get("traffic_hash"):
            self.manifest["traffic_hash"] = traffic
            self.write_manifests()

    def open(self, path, factory):
        path = Path(path)
        critical = None
        critical_error = False
        try:
            critical = self.read_critical()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            critical_error = any((self.root / n).exists() for n in ("manifest.json", "manifest-mirror.json"))
        restored, fresh = False, False
        backups = sorted(self.root.glob("backup-*.sqlite3"), reverse=True)
        known = critical is not None or critical_error or (self.directory / "status.json").exists() or bool(backups)
        if path.exists():
            try:
                check_database(path, ("meta", "messages"))
                if critical is not None and critical[2].get("traffic_hash"):
                    with contextlib.closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as verify:
                        if self.traffic_hash(verify) != critical[2]["traffic_hash"]:
                            raise CorruptDatabase("Cursor SQLite distinto del punto de control verificado")
            except Exception as exc:
                logical_damage = known and str(exc) == "SQLite sin el esquema esperado"
                if not corruption(exc) and not logical_damage:
                    raise
                self.quarantine([path, Path(str(path) + "-wal"), Path(str(path) + "-shm")], str(exc))
                for backup in sorted(self.root.glob("backup-*.sqlite3"), reverse=True):
                    try:
                        check_database(backup, ("meta", "messages"), deep=True)
                        with contextlib.closing(sqlite3.connect(str(backup))) as source, contextlib.closing(sqlite3.connect(str(path))) as dest:
                            source.backup(dest)
                        os.chmod(str(path), 0o600)
                        restored = True
                        break
                    except (sqlite3.Error, ValueError):
                        if path.exists():
                            path.unlink()
                fresh = not restored
                self.note = "SQLite recuperado desde copia verificada" if restored else "SQLite reconstruido; recuperando cobertura del log"
        elif known:
            for backup in backups:
                try:
                    check_database(backup, ("meta", "messages"), deep=True)
                    with contextlib.closing(sqlite3.connect(str(backup))) as source, contextlib.closing(sqlite3.connect(str(path))) as dest:
                        source.backup(dest)
                    os.chmod(str(path), 0o600)
                    restored = True
                    break
                except (sqlite3.Error, ValueError, OSError):
                    if path.exists():
                        path.unlink()
            fresh = not restored
            self.note = "SQLite ausente recuperado desde copia" if restored else "SQLite ausente reconstruido; recuperando cobertura del log"
        store = factory(path)
        if critical_error:
            self.quarantine(list(self.root.glob("manifest*.json")) + list(self.root.glob("*.snapshot")) +
                            list(self.root.glob("*.mirror")) + list(self.root.glob("*.journal")), "Registro crítico dañado")
            if not (restored or fresh):
                self.note = "Registro crítico reconstruido desde SQLite verificado"
        if restored or fresh:
            if critical is None:
                store.set("recovery_pause", "Sin historial crítico verificable; análisis activo, automatismos suspendidos")
            if fresh:
                store.set("warm_until", time.time() + self.c.max_period)
            if fresh or store.get("cursor") is None:
                store.set("recovery_from_start", True)
            store.db.commit()
        self.attach(store, critical, restored=restored or fresh)
        return store

    def backup(self, store, force=False):
        backups = sorted(self.root.glob("backup-*.sqlite3"), reverse=True)
        if not force and backups and time.time() - backups[0].stat().st_mtime < self.c["backup_interval"]:
            return
        store.persist_critical()
        target = self.root / ("backup-%020d-%s.sqlite3" % (time.time_ns(), secrets.token_hex(3)))
        try:
            with contextlib.closing(sqlite3.connect(str(target))) as dest:
                store.db.backup(dest)
            os.chmod(str(target), 0o600)
            check_database(target, ("meta", "messages"), deep=True)
            with contextlib.closing(sqlite3.connect(str(target))) as verify:
                if verify.execute("PRAGMA foreign_key_check").fetchone():
                    raise CorruptDatabase("Copia con referencias SQLite inconsistentes")
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
            fd = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception:
            if target.exists():
                target.unlink()
            raise
        for old in sorted(self.root.glob("backup-*.sqlite3"), reverse=True)[self.c["backup_copies"]:]:
            old.unlink()

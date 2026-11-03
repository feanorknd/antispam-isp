#!/usr/bin/env python3
"""Control de abuso SMTP saliente. Compatible con Python >= 3.8, sin pip."""
import argparse
import base64
import collections
import contextlib
import datetime as dt
import email.message
import email.utils
import fcntl
import glob
import grp
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import sqlite3
import ssl
import stat
import subprocess
import sys
import time

from postfix_settings import restart_postfix, MonitorError, LocalGuard, Websites, is_local, unix_identity, domain_belongs
import runtime
from state_recovery import Recovery, check_database, corruption

VERSION = "1.3.2"
MIN_NOTICE_INTERVAL = 6 * 3600
QUEUE_ID = re.compile(r"^[A-Za-z0-9]{5,32}$")
TAG = re.compile(r"postfix(?:/[^ /\[]+)?/(smtpd|pickup|qmgr|cleanup|smtp|relay|lmtp|local|virtual|pipe|error|discard)\[\d+\]: ([A-Za-z0-9]+): (.*)")
# Admite también transports con syslog_name personalizado: postfix/nombre[PID].
CUSTOM_TAG = re.compile(r"postfix/([^ /\[]+)\[\d+\]: ([A-Za-z0-9]+): (.*)")
SMTP_SESSION = re.compile(r"^(?:\S+|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(postfix(?:/[^ /\[]+)?/smtpd\[\d+\]): (.*)$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, qid TEXT NOT NULL, account TEXT NOT NULL,
 created REAL NOT NULL, accepted REAL, removed INTEGER NOT NULL DEFAULT 0,
 nrcpt INTEGER, size INTEGER, ip TEXT, message_id TEXT, subject TEXT,
 auth_line TEXT NOT NULL, queue_line TEXT, samples TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS messages_qid ON messages(qid, removed);
CREATE INDEX IF NOT EXISTS messages_time ON messages(accepted, account);
CREATE INDEX IF NOT EXISTS messages_active ON messages(removed,id);
CREATE TABLE IF NOT EXISTS smtp_sessions (connection TEXT PRIMARY KEY,generation REAL NOT NULL);
CREATE TABLE IF NOT EXISTS smtp_pending (
 mid INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
 connection TEXT NOT NULL,generation REAL NOT NULL);
CREATE INDEX IF NOT EXISTS smtp_pending_session ON smtp_pending(connection,generation);
CREATE TABLE IF NOT EXISTS recipients (
 mid INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 address TEXT NOT NULL, external INTEGER, delivery TEXT, log_line TEXT,
 PRIMARY KEY(mid,address));
CREATE TABLE IF NOT EXISTS incidents (
 id INTEGER PRIMARY KEY, account TEXT NOT NULL, opened REAL NOT NULL,
 event_time REAL NOT NULL, peak INTEGER NOT NULL, maximum INTEGER NOT NULL,
 period INTEGER NOT NULL, snapshot TEXT NOT NULL, acked REAL, last_notice REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident ON incidents(account) WHERE acked IS NULL;
CREATE TABLE IF NOT EXISTS actions (
 id INTEGER PRIMARY KEY, incident INTEGER NOT NULL REFERENCES incidents(id),
 kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', payload TEXT NOT NULL DEFAULT '{}',
 attempts INTEGER NOT NULL DEFAULT 0, attempted REAL, error TEXT,
 UNIQUE(incident,kind));
CREATE TABLE IF NOT EXISTS held (
 qid TEXT PRIMARY KEY, account TEXT NOT NULL, fingerprint TEXT NOT NULL,
 incident INTEGER NOT NULL REFERENCES incidents(id), held_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS blocks (account TEXT PRIMARY KEY, incident INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS acknowledgements (account TEXT PRIMARY KEY, cutoff REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notice_state (account TEXT PRIMARY KEY,last_attempt REAL,last_success REAL);
CREATE TABLE IF NOT EXISTS queue_ops (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, qid TEXT NOT NULL, fingerprint TEXT NOT NULL,
 operation TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,error TEXT);
"""


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    temp = path.with_name("." + path.name + "." + secrets.token_hex(8))
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(temp), str(path))
        directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp.exists():
            temp.unlink()


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & 0o077:
        raise MonitorError("Directorio de estado inseguro (requiere propietario ejecutor y 0700): " + str(path))
    return path


def private_file(path):
    if Path(path).exists() or Path(path).is_symlink():
        s = Path(path).lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & 0o077:
            raise MonitorError("Archivo de estado inseguro: " + str(path))


class Config:
    def __init__(self, values):
        self.v = dict(values)
        self.v.setdefault("foreign_sender_divisor", 4)
        self.v.setdefault("postfix_outbound_delay", "auto")
        self.v.setdefault("postfix_headroom", 10)
        self.v.setdefault("postfix_client_message_limit", 0)
        self.v.setdefault("guard_route_addresses", [])
        self.v.setdefault("postfix_destination_delay", "10s")
        self.v.setdefault("run_interval", 60)
        self.v.setdefault("lock_wait", 120)
        self.v.setdefault("service_unit", "/etc/systemd/system/outbound-spam.service")
        self.v.setdefault("backup_interval", 300)
        self.v.setdefault("integrity_interval", 300)
        self.v.setdefault("backup_copies", 3)
        self.v.setdefault("status_http_host", "127.0.0.1")
        self.v.setdefault("status_http_port", 23456)
        self.v.setdefault("monitor_local", True)
        self.v.setdefault("block_local", True)
        if self.v.get("systemctl_bin") == "/usr/bin/systemctl" and not os.path.exists("/usr/bin/systemctl") and os.path.exists("/bin/systemctl"):
            self.v["systemctl_bin"] = "/bin/systemctl"
        integers = ("max_sent", "time_period", "max_read_bytes", "command_timeout", "stale_after",
                    "rotation_grace", "retention_days", "max_action_age", "reminder_interval",
                    "sample_count", "smtp_port", "ispconfig_server_id", "foreign_sender_divisor", "postfix_headroom",
                    "run_interval", "lock_wait", "backup_interval", "integrity_interval", "backup_copies", "status_http_port")
        for k in integers:
            try:
                self.v[k] = int(self.v[k])
                if self.v[k] <= 0:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise MonitorError("Configuración: entero positivo requerido: " + k)
        import ipaddress
        try:
            ipaddress.IPv4Address(self.v["status_http_host"])
        except (ValueError, TypeError):
            raise MonitorError("status_http_host debe ser una dirección IPv4")
        if self.v["status_http_port"] > 65535:
            raise MonitorError("status_http_port debe estar entre 1 y 65535")
        for k in ("notify", "block_smtp", "hold_queue", "reset_password", "monitor_local", "block_local"):
            if str(self.v.get(k)).lower() not in ("true", "false"):
                raise MonitorError("Configuración: booleano true/false requerido: " + k)
            self.v[k] = str(self.v[k]).lower() == "true"
        if self.v.get("routing_mode") not in ("auto", "static"):
            raise MonitorError("routing_mode debe ser auto o static")
        if self.v.get("initial_position") not in ("start", "end"):
            raise MonitorError("initial_position debe ser start o end")
        self.v["ignore"] = {x.casefold() for x in self.v.get("ignore", [])}
        self.v["local_domains"] = {x.casefold() for x in self.v.get("local_domains", [])}
        limits = {}
        for account, value in self.v.get("custom_limits", {}).items():
            try:
                maximum, period = map(int, value.split(":"))
                if min(maximum, period) <= 0:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise MonitorError("Límite inválido para " + account + ": esperado máximo:segundos")
            limits[account.casefold()] = (maximum, period)
        self.v["custom_limits"] = limits
        self.v["reminder_interval"] = max(MIN_NOTICE_INTERVAL, self.v["reminder_interval"])
        try:
            self.v["postfix_client_message_limit"] = int(self.v["postfix_client_message_limit"])
            if self.v["postfix_client_message_limit"] < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise MonitorError("postfix_client_message_limit debe ser un entero >= 0")
        if not re.fullmatch(r"auto|\d+[smhdw]?", str(self.v["postfix_outbound_delay"])):
            raise MonitorError("postfix_outbound_delay debe ser auto o una duración Postfix")
        if not re.fullmatch(r"\d+[smhdw]?", str(self.v["postfix_destination_delay"])):
            raise MonitorError("postfix_destination_delay debe ser una duración Postfix")
        self.v["stale_after"] = max(self.v["stale_after"], self.v["run_interval"] + 120)
        if self.v["retention_days"] * 86400 < self.max_period + self.v["max_action_age"]:
            raise MonitorError("retention_days debe cubrir la ventana máxima y max_action_age")
        for key in ("alert_from",):
            self.check_mailbox(self.v.get(key, ""))
        for address in self.v.get("report", []):
            self.check_mailbox(address)
        if self.v["notify"] and not self.v.get("report"):
            raise MonitorError("report no puede estar vacío si notify=true")
        for key in ("log_file", "state_dir", "postfix_config", "guard_map", "monit_config",
                    "monit_enabled", "monit_script", "service_unit"):
            if not os.path.isabs(self.v.get(key, "")):
                raise MonitorError(key + " debe ser una ruta absoluta")
        if self.v.get("smtp_security") not in ("plain", "starttls", "tls"):
            raise MonitorError("smtp_security inválido")
        if self.v.get("alert_transport") not in ("smtp", "sendmail"):
            raise MonitorError("alert_transport inválido")

    @staticmethod
    def check_mailbox(address):
        if not re.fullmatch(r"[^\s<>\x00-\x1f\x7f]+@[^\s<>\x00-\x1f\x7f]+", address) or address.startswith("-"):
            raise MonitorError("Dirección de notificación inválida: " + repr(address))

    def __getitem__(self, key):
        return self.v[key]

    def limit(self, account):
        return self.v["custom_limits"].get(account, (self.v["max_sent"], self.v["time_period"]))

    @property
    def max_period(self):
        return max([self.v["time_period"]] + [x[1] for x in self.v["custom_limits"].values()])

    @classmethod
    def from_fd(cls, fd):
        with os.fdopen(fd, "rb") as f:
            return cls.from_bytes(f.read())

    @classmethod
    def from_bytes(cls, data):
        parts = iter(data.decode("utf-8").split("\0")[:-1])
        values = {}
        try:
            for key in parts:
                value = next(parts)
                if key in ("report", "ignore", "local_domains", "external_transports", "local_transports", "guard_route_addresses"):
                    value = [next(parts) for _ in range(int(value))]
                elif key == "custom_limits":
                    value = {next(parts): next(parts) for _ in range(int(value))}
                values[key] = value
        except (StopIteration, ValueError):
            raise MonitorError("Configuración Bash incompleta")
        return cls(values)


class Commands:
    def __init__(self, config):
        self.c = config

    def run(self, binary, args=(), data=None, ok=(0,)):
        command = [self.c[binary + "_bin"]] + list(args)
        env = dict(os.environ, LC_ALL="C")
        try:
            p = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=self.c["command_timeout"], env=env, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            raise MonitorError(binary + ": " + str(e))
        if p.returncode not in ok or (p.returncode != 0 and p.stderr.strip()):
            # No devolver SQL, contraseñas, hashes ni argumentos en errores públicos.
            detail = "" if binary in ("mysql", "openssl") else ": " + p.stderr.strip()[:300]
            raise MonitorError("%s terminó con código %s%s" % (binary, p.returncode, detail))
        return p.stdout

    def pc(self, *args):
        return self.run("postconf", ["-c", self.c["postfix_config"]] + list(args)).strip()


def mysql_query(config, commands, sql):
    database = config["ispconfig_database"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise MonitorError("Nombre de base ISPConfig inválido")
    args = ["--protocol=socket", "--user=root", "--batch", "--skip-column-names", "--raw"]
    if config["mysql_socket"]:
        args.append("--socket=" + config["mysql_socket"])
    return commands.run("mysql", args + [database], data=sql).strip()


class Store:
    def __init__(self, path):
        self.recovery = None
        private_file(path)
        self.path = Path(path)
        self.db = sqlite3.connect(str(path), timeout=5)
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        os.chmod(str(self.path), 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4):
            raise MonitorError("Versión de base de datos no compatible")
        self.db.executescript(SCHEMA)
        additions = {"messages": {"sender": "TEXT", "foreign_sender": "INTEGER NOT NULL DEFAULT 0",
                                   "local_identity": "TEXT", "local_error": "TEXT", "envelope_key": "TEXT"},
                     "incidents": {"base_maximum": "INTEGER", "reason": "TEXT NOT NULL DEFAULT 'volume'",
                                   "foreign_recipients": "INTEGER NOT NULL DEFAULT 0"}}
        for table, fields in additions.items():
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(" + table + ")")}
            for column, definition in fields.items():
                if column not in columns:
                    self.db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, definition))
        if version < 2:
            for row in self.db.execute("SELECT id,account,queue_line FROM messages WHERE queue_line IS NOT NULL").fetchall():
                sender = re.search(r"from=<(.*?)>", row["queue_line"])
                if sender:
                    self.sender(row["id"], row["account"], sender.group(1))
            self.db.execute("UPDATE incidents SET base_maximum=maximum WHERE base_maximum IS NULL")
        # También cubre incidentes cerrados y entregas inciertas de versiones anteriores.
        if version < 2:
            self.db.execute("INSERT OR IGNORE INTO notice_state SELECT i.account,MAX(COALESCE(a.attempted,i.last_notice)),"
                            "MAX(i.last_notice) FROM incidents i LEFT JOIN actions a ON a.incident=i.id AND a.kind='notify' GROUP BY i.account")
        self.db.execute("PRAGMA user_version=4")
        self.db.commit()

    def persist_critical(self):
        self.db.commit()
        if self.recovery is not None:
            self.recovery.capture(self)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def active(self, qid):
        return self.db.execute("SELECT * FROM messages WHERE qid=? AND removed=0 ORDER BY id DESC LIMIT 1", (qid,)).fetchone()

    def sender(self, mid, account, sender):
        a, b = mail_domain(account), mail_domain(sender)
        foreign = int(bool(a and b and a != b))
        if is_local(account):
            row = self.db.execute("SELECT local_identity FROM messages WHERE id=?", (mid,)).fetchone()
            identity = json.loads(row[0]) if row and row[0] else {}
            domains = identity.get("domains")
            own = domain_belongs(b, domains)
            foreign = int(bool(b and domains is not None and not own))
        self.db.execute("UPDATE messages SET sender=?,foreign_sender=? WHERE id=?", (sender, foreign, mid))

    def notice_due(self, account, now, interval):
        row = self.db.execute("SELECT last_attempt FROM notice_state WHERE account=?", (account,)).fetchone()
        return row is None or row[0] is None or now - row[0] >= max(MIN_NOTICE_INTERVAL, interval)

    def recipient(self, mid, address, external=None, delivery=None, line=None):
        # El dominio se normaliza; la parte local se conserva para no fusionar destinatarios.
        local, sep, domain = address.rpartition("@")
        address = local + sep + domain.casefold() if sep else address
        self.db.execute("INSERT OR IGNORE INTO recipients(mid,address) VALUES (?,?)", (mid, address))
        self.db.execute("UPDATE recipients SET external=COALESCE(?,external), delivery=COALESCE(?,delivery), "
                        "log_line=COALESCE(?,log_line) WHERE mid=? AND address=?",
                        (external, delivery, line, mid, address))


def mail_domain(address):
    if not address or "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].rstrip(".").casefold()
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def timestamp(line, reference):
    token = line.split(" ", 1)[0]
    try:
        return dt.datetime.fromisoformat(token.replace("Z", "+00:00")).timestamp()
    except ValueError:
        # Syslog clásico sin año: elegir el año cercano al reloj de referencia.
        start = line[:15]
        year = dt.datetime.fromtimestamp(reference).year
        choices = []
        for y in (year - 1, year, year + 1):
            try:
                choices.append(time.mktime(time.strptime("%s %s" % (y, start), "%Y %b %d %H:%M:%S")))
            except ValueError:
                pass
        if not choices:
            raise MonitorError("Fecha Postfix no reconocida: " + line[:60])
        return min(choices, key=lambda x: abs(x - reference))


class Parser:
    def __init__(self, store, config, now, realtime=False):
        self.s, self.c, self.now = store, config, now
        self.realtime = realtime
        self.last_time = 0
        self.first_time = None
        self.lines = 0

    def session(self, line):
        match = SMTP_SESSION.match(line)
        if not match:
            return None
        host, tag, text = match.groups()
        key = host + "/" + tag
        if text.startswith("connect from "):
            self.s.db.execute("INSERT OR REPLACE INTO smtp_sessions VALUES (?,?)", (key, timestamp(line, self.now)))
        elif text.startswith("disconnect from "):
            # El resumen completo permite demostrar que no se aceptó DATA/BDAT.
            # No cerrar si hubo un DATA correcto: qmgr puede registrarse con retraso.
            counters = [int(x) for x in re.findall(r"(?<!\S)(?:data|bdat)=(\d+)(?:/\d+)?(?=\s|$)", text)]
            if "commands=" in text and not any(counters):
                self.s.db.execute("UPDATE messages SET removed=1 WHERE accepted IS NULL AND id IN "
                                  "(SELECT p.mid FROM smtp_pending p JOIN smtp_sessions s "
                                  "ON p.connection=s.connection AND p.generation=s.generation WHERE s.connection=?)", (key,))
            self.s.db.execute("DELETE FROM smtp_pending WHERE connection=?", (key,))
            self.s.db.execute("DELETE FROM smtp_sessions WHERE connection=?", (key,))
        return key

    def pending(self, mid, connection, when):
        if connection is None:
            return
        self.s.db.execute("INSERT OR IGNORE INTO smtp_sessions VALUES (?,?)", (connection, when))
        self.s.db.execute("INSERT OR REPLACE INTO smtp_pending SELECT ?,connection,generation FROM smtp_sessions WHERE connection=?", (mid, connection))

    def feed(self, line):
        self.lines += 1
        if "postfix/" not in line:
            return
        connection = self.session(line)
        match = TAG.search(line) or CUSTOM_TAG.search(line)
        if not match:
            return
        service, qid, text = match.groups()
        if not QUEUE_ID.fullmatch(qid):
            return
        when = timestamp(line, self.now)
        self.last_time = max(self.last_time, when)
        self.first_time = when if self.first_time is None else min(self.first_time, when)
        if when > (max(self.now, time.time()) if self.realtime else self.now) + 60:
            raise MonitorError("El log contiene fechas futuras; revise el reloj o use --now en replay")
        auth = re.search(r"(?:^|, )sasl_username=([^,\s]+)", text)
        pickup = re.fullmatch(r"uid=(\d+) from=<(.*?)>(?: orig_id=[A-Za-z0-9]+)?", text) if service == "pickup" else None
        if pickup and self.c["monitor_local"]:
            account = "local:" + str(int(pickup.group(1)))
            if account in self.c["ignore"]:
                return
            current = self.s.active(qid)
            if current and current["auth_line"] == line:
                return
            if current:
                self.s.db.execute("UPDATE messages SET removed=1 WHERE id=?", (current["id"],))
            created = self.s.db.execute("INSERT INTO messages(qid,account,created,ip,auth_line) VALUES (?,?,?,?,?)",
                                        (qid, account, when, "local", line))
            self.s.sender(created.lastrowid, account, pickup.group(2))
            return
        if service == "smtpd" and text.startswith("client=") and auth:
            account = auth.group(1).casefold()
            if is_local(account):
                raise MonitorError("Identidad SASL en el espacio reservado local:UID")
            if account in self.c["ignore"]:
                return
            current = self.s.active(qid)
            if current and current["auth_line"] == line:
                return
            if current and current["auth_line"] == "Bootstrap de sobre SASL" and current["account"] == account and abs(current["created"] - when) <= 2:
                ip = re.search(r"client=.*?\[([^\]]+)\]", text)
                self.s.db.execute("UPDATE messages SET auth_line=?,ip=?,created=? WHERE id=?",
                                  (line, ip.group(1) if ip else "?", when, current["id"]))
                self.pending(current["id"], connection, when)
                return
            if current:
                self.s.db.execute("UPDATE messages SET removed=1 WHERE id=?", (current["id"],))
            ip = re.search(r"client=.*?\[([^\]]+)\]", text)
            created = self.s.db.execute("INSERT INTO messages(qid,account,created,ip,auth_line) VALUES (?,?,?,?,?)",
                                       (qid, account, when, ip.group(1) if ip else "?", line))
            self.pending(created.lastrowid, connection, when)
            return
        message = self.s.active(qid)
        if not message:
            return
        mid = message["id"]
        if service == "qmgr":
            if text == "removed":
                self.s.db.execute("UPDATE messages SET removed=1 WHERE id=?", (mid,))
            else:
                m = re.search(r"size=(\d+), nrcpt=(\d+)", text)
                if m and message["queue_line"] is None:
                    sender = re.search(r"from=<(.*?)>", text)
                    if sender:
                        self.s.sender(mid, message["account"], sender.group(1))
                    self.s.db.execute("UPDATE messages SET accepted=?,size=?,nrcpt=?,queue_line=? WHERE id=?",
                                      (when, int(m.group(1)), int(m.group(2)), line, mid))
                    self.s.db.execute("DELETE FROM smtp_pending WHERE mid=?", (mid,))
        elif service == "cleanup":
            if text.startswith(("milter-reject:", "milter-discard:", "reject:", "discard:")):
                # Tener un ID y AUTH no implica que se haya aceptado DATA.
                self.s.db.execute("UPDATE messages SET removed=1,accepted=NULL WHERE id=?", (mid,))
                self.s.db.execute("DELETE FROM recipients WHERE mid=?", (mid,))
                self.s.db.execute("DELETE FROM smtp_pending WHERE mid=?", (mid,))
            m = re.search(r"message-id=<(.*?)>", text)
            if m:
                self.s.db.execute("UPDATE messages SET message_id=? WHERE id=?", (m.group(1), mid))
            m = re.search(r"(?:info|hold): header Subject: (.*?) from ", text)
            if m:
                self.s.db.execute("UPDATE messages SET subject=? WHERE id=?", (m.group(1), mid))
        else:
            m = re.search(r"^to=<(.*?)>.*?status=(sent|deferred|bounced|expired)", text)
            if m:
                external = None
                if service in self.c["external_transports"]:
                    external = 1
                    relay = re.search(r"relay=[^\s]*\[([^\]]+)\]", text)
                    if relay:
                        try:
                            if ipaddress.ip_address(relay.group(1)).is_loopback:
                                # SMTP también se usa para filtros locales/reinyección.
                                # Clasificar su destinatario por ruta final, no por protocolo.
                                external = None
                        except ValueError:
                            pass
                elif service in self.c["local_transports"]:
                    external = 0
                self.s.recipient(mid, m.group(1), external, m.group(2), line)
        samples = json.loads(message["samples"])
        if len(samples) < 8:
            samples.append(line[:4096])
            self.s.db.execute("UPDATE messages SET samples=? WHERE id=?", (json.dumps(samples), mid))


class LogReader:
    """Cursor binario + ancla. Los cursores y eventos se confirman juntos en SQLite."""
    def __init__(self, store, config, parser, now):
        self.s, self.c, self.parser, self.now = store, config, parser, now
        self.bytes = 0
        self.backlog = False
        self.notes = []

    @staticmethod
    def open(path):
        return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")

    @staticmethod
    def anchor(f, offset):
        f.seek(max(0, offset - 128))
        return base64.b64encode(f.read(min(offset, 128))).decode("ascii")

    def matches(self, path, cursor):
        try:
            with self.open(path) as f:
                return self.anchor(f, cursor["offset"]) == cursor["anchor"] and self.prefix(f, cursor["offset"]) == cursor["prefix"]
        except (OSError, EOFError):
            return False

    @staticmethod
    def prefix(f, offset):
        f.seek(0)
        return base64.b64encode(f.read(min(offset, 128))).decode("ascii")

    def locate(self, cursor):
        candidates = [cursor["path"]] + sorted(glob.glob(self.c["log_file"] + "*"))
        # Primero inode original; después copia comprimida/copytruncate con las mismas anclas.
        for identity in (True, False):
            for path in dict.fromkeys(candidates):
                try:
                    st = os.stat(path)
                    same = (st.st_dev, st.st_ino) == (cursor["dev"], cursor["ino"])
                    if not stat.S_ISREG(st.st_mode) or same != identity:
                        continue
                    if self.matches(path, cursor):
                        return path
                except OSError:
                    continue
        return None

    def consume(self, cursor, path):
        with self.open(path) as f:
            boundary = None if str(path).endswith(".gz") else os.fstat(f.fileno()).st_size
            f.seek(cursor["offset"])
            while self.bytes < self.c["max_read_bytes"]:
                start = f.tell()
                if boundary is not None and start >= boundary:
                    break
                limit = 1048577 if boundary is None else min(1048577, boundary - start)
                raw = f.readline(limit)
                if not raw:
                    break
                if len(raw) > 1048576:
                    raise MonitorError("Línea de log mayor de 1 MiB; no se avanza el cursor")
                if not raw.endswith(b"\n"):
                    f.seek(start)
                    break
                self.parser.feed(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                self.bytes += len(raw)
                cursor["offset"] = f.tell()
            if self.bytes >= self.c["max_read_bytes"] and ((boundary is not None and cursor["offset"] < boundary) or (boundary is None and f.read(1))):
                self.backlog = True
            # El prefijo se limita a bytes ya consumidos: un fichero inicialmente corto crece.
            f.seek(0)
            cursor["prefix"] = base64.b64encode(f.read(min(cursor["offset"], 128))).decode("ascii")
            cursor["anchor"] = self.anchor(f, cursor["offset"])
        cursor["path"] = str(path)

    def run(self, from_start=False):
        target = self.c["log_file"]
        st = os.stat(target)
        if not stat.S_ISREG(st.st_mode):
            raise MonitorError("mail.log no es un fichero regular")
        cursor = self.s.get("cursor")
        first = cursor is None
        # Releer únicamente incrementos tardíos del inode retirado durante unos minutos.
        retired = []
        for old in self.s.get("retired", []):
            path = self.locate(old)
            if path:
                self.consume(old, path)
                if self.backlog or (not str(path).endswith(".gz") and self.now - old["retired_at"] < self.c["rotation_grace"]):
                    retired.append(old)
        if cursor:
            path = self.locate(cursor)
            same = (st.st_dev, st.st_ino) == (cursor["dev"], cursor["ino"])
            if path is None:
                self.notes.append("No se ha recuperado el tramo anterior del log (rotación/truncado)")
                self.s.set("warm_until", self.now + self.c.max_period)
                cursor = None
            else:
                self.consume(cursor, path)
                if self.backlog:
                    self.s.set("cursor", cursor)
                    self.s.set("retired", retired)
                    return first
                # También se detecta copytruncate si el inode coincide pero el contenido no.
                if not same or not self.matches(target, cursor):
                    if not str(path).endswith(".gz"):
                        cursor["retired_at"] = self.now
                        retired.append(cursor)
                    cursor = None
        if cursor is None:
            offset = st.st_size if first and not from_start and self.c["initial_position"] == "end" else 0
            cursor = {"dev": st.st_dev, "ino": st.st_ino, "offset": offset, "path": target}
            with open(target, "rb") as f:
                f.seek(0)
                cursor["prefix"] = base64.b64encode(f.read(min(offset, 128))).decode("ascii")
                cursor["anchor"] = self.anchor(f, offset)
            if offset:
                self.s.set("warm_until", self.now + self.c.max_period)
            self.consume(cursor, target)
        self.s.set("cursor", cursor)
        self.s.set("retired", retired)
        return first


class Router:
    PARAMETERS = ("mydestination", "myhostname", "virtual_alias_maps", "virtual_mailbox_domains", "relay_domains", "transport_maps",
                  "local_transport", "virtual_transport", "relay_transport", "default_transport",
                  "sender_dependent_default_transport_maps", "recipient_delimiter")

    def __init__(self, config, commands):
        self.c, self.cmd = config, commands
        self.cache = {}
        self.parameters = None

    def load(self):
        if self.parameters is None:
            output = self.cmd.pc("-x", *self.PARAMETERS)
            self.parameters = dict(line.split(" = ", 1) if " = " in line else (line.split("=", 1)[0].strip(), "")
                                   for line in output.splitlines() if "=" in line)
            if set(self.PARAMETERS) - set(self.parameters):
                raise MonitorError("No se ha podido leer la configuración de enrutamiento Postfix")

    def lookup(self, tables, keys):
        table_list = re.split(r"[\s,]+", tables.strip()) if tables.strip() else []
        for key in keys:
            for table in table_list:
                cache_key = (table, key)
                if cache_key not in self.cache:
                    if ":" not in table or any(x in table for x in "{}$"):
                        raise MonitorError("Mapa Postfix no soportado para clasificar: " + table)
                    native = table[6:] if table.startswith("proxy:") else table
                    result = self.cmd.run("postmap", ["-c", self.c["postfix_config"], "-q", key, native], ok=(0, 1))
                    self.cache[cache_key] = result.strip()
                if self.cache[cache_key]:
                    return self.cache[cache_key]
        return ""

    def domain_match(self, value, domain):
        for item in re.split(r"[\s,]+", value.strip()):
            if not item:
                continue
            if item.startswith(("!", "/")) or "{" in item:
                raise MonitorError("Lista de dominios compleja: utilice routing_mode=static o simplifique la clasificación")
            if ":" in item:
                if self.lookup(item, [domain]):
                    return True
            elif item.casefold() == domain or (item.startswith(".") and domain.endswith(item.casefold())):
                return True
        return False

    def external(self, address, sender=""):
        key = ("route", address, sender)
        if key in self.cache:
            return self.cache[key]
        if "@" not in address:
            raise MonitorError("Destinatario sin dominio en el sobre: " + address)
        local, domain = address.rsplit("@", 1)
        domain = domain.casefold()
        if self.c["routing_mode"] == "static":
            result = int(domain not in self.c["local_domains"])
        else:
            self.load()
            p = self.parameters
            keys = [address]
            for delimiter in p["recipient_delimiter"]:
                if delimiter in local:
                    keys.append(local.split(delimiter, 1)[0] + "@" + domain)
                    break
            keys.append(domain)
            labels = domain.split(".")
            keys.extend("." + ".".join(labels[i:]) for i in range(1, len(labels)))
            keys.append("*")
            override = self.lookup(p["transport_maps"], keys)
            transport = override.split(":", 1)[0] if override else ""
            if not transport:
                if self.domain_match(p["mydestination"], domain):
                    transport = p["local_transport"].split(":", 1)[0]
                elif self.domain_match(p["virtual_mailbox_domains"], domain):
                    transport = p["virtual_transport"].split(":", 1)[0]
                elif self.domain_match(p["relay_domains"], domain):
                    transport = p["relay_transport"].split(":", 1)[0]
                else:
                    dependent = self.lookup(p["sender_dependent_default_transport_maps"], [sender, "@" + sender.rsplit("@", 1)[-1]]) if sender else ""
                    transport = (dependent or p["default_transport"]).split(":", 1)[0]
            if transport in self.c["external_transports"]:
                result = 1
            elif transport in self.c["local_transports"]:
                result = 0
            else:
                raise MonitorError("Transporte sin clasificar: " + transport)
        self.cache[key] = result
        return result


class Envelope:
    def __init__(self, qid, text):
        self.qid = qid
        self.account = None
        self.arrival = None
        self.size = None
        self.sender = ""
        self.create_time = ""
        self.queue = None
        self.recipients = []
        self.pending_recipients = []
        # postcat muestra cola/ID o cola/H/.../ID según hash_queue_names/depth.
        # La cola es siempre el primer componente; exigir el ID completo evita
        # aceptar prefijos de otro mensaje o cabeceras de otras secciones.
        header = re.compile(r"\*\*\* ENVELOPE RECORDS (\w+)/(?:[A-Za-z0-9]/)*" + re.escape(qid) + r" \*\*\*")
        for line in text.splitlines():
            match = header.fullmatch(line)
            if match:
                self.queue = match.group(1)
            if line.startswith("named_attribute: sasl_username="):
                self.account = line.split("=", 1)[1].strip().casefold()
                if is_local(self.account):
                    raise MonitorError("Sobre SASL en el espacio reservado local:UID")
            elif line.startswith("message_arrival_time: "):
                value = line.split(": ", 1)[1]
                try:
                    self.arrival = time.mktime(time.strptime(value, "%a %b %d %H:%M:%S %Y"))
                except ValueError:
                    raise MonitorError("Fecha del sobre no reconocida")
            elif line.startswith("create_time: "):
                self.create_time = line.split(": ", 1)[1]
            elif line.startswith("message_size: "):
                self.size = int(line.split()[1])
            elif line.startswith("sender: "):
                self.sender = line[len("sender: "):]
            elif line.startswith(("recipient: ", "done_recipient: ")):
                self.recipients.append(line.split(": ", 1)[1])
                if line.startswith("recipient: "):
                    self.pending_recipients.append(line.split(": ", 1)[1])

    @property
    def fingerprint(self):
        if self.account is None or self.arrival is None or self.size is None or not self.queue:
            raise MonitorError("Sobre incompleto o sin identidad verificable para " + self.qid)
        data = [self.qid, self.account, self.arrival, self.size, self.create_time]
        return hashlib.sha256(json.dumps(data).encode("utf-8")).hexdigest()


def local_envelope_key(env):
    return [env.arrival, env.size, env.create_time, env.sender]


def preserve_local_queue(store, env):
    if is_local(env.account):
        store.set("local_queue_" + env.qid, {"account": env.account, "key": local_envelope_key(env)})


def resolve_local_identities(store, config, commands, now, offline=False):
    issues = []
    if not config["monitor_local"]:
        return issues
    websites = Websites(config, lambda sql: mysql_query(config, commands, sql))
    identities = {}
    for message in store.db.execute("SELECT * FROM messages WHERE account LIKE 'local:%' AND "
                                    "(removed=0 OR accepted>=?)", (now - config.max_period,)).fetchall():
        account = message["account"]
        if message["local_identity"] and not message["local_error"]:
            continue
        try:
            if offline:
                raise MonitorError("Dominios de web no consultados en replay offline: " + account)
            if account not in identities:
                identity = unix_identity(account.split(":", 1)[1])
                # Los avisos root y el reencolado interno no son envíos de una web.
                domains = None if identity["uid"] == 0 or identity["user"] == "postfix" else websites.domains(identity["user"])
                identity["domains"] = domains
                identities[account] = identity
            identity = identities[account]
            if identity["domains"] is None:
                store.db.execute("DELETE FROM messages WHERE id=?", (message["id"],))
                continue
            old = store.get("local_identity_" + account)
            if old and any(old[k] != identity[k] for k in ("uid", "user", "home")):
                raise MonitorError("Identidad UNIX sustituida para " + account + "; requiere revisión")
            store.set("local_identity_" + account, identity)
            store.db.execute("UPDATE messages SET local_identity=?,local_error=NULL WHERE id=?",
                             (json.dumps(identity), message["id"]))
            store.sender(message["id"], account, message["sender"] or "")
        except MonitorError as exc:
            store.db.execute("UPDATE messages SET local_error=? WHERE id=?", (str(exc), message["id"]))
            if message["accepted"] is not None:
                issues.append(str(exc))
    return list(dict.fromkeys(issues))


class Queue:
    def __init__(self, config, commands, store=None):
        self.c, self.cmd, self.store = config, commands, store

    def listing(self):
        output = self.cmd.run("postqueue", ["-c", self.c["postfix_config"], "-j"])
        entries = {}
        for line in output.splitlines():
            item = json.loads(line)
            if not QUEUE_ID.fullmatch(item["queue_id"]):
                raise MonitorError("ID inválido en postqueue")
            entries[item["queue_id"]] = item
        return entries

    def envelope(self, qid):
        if not QUEUE_ID.fullmatch(qid):
            raise MonitorError("ID de cola inválido")
        env = Envelope(qid, self.cmd.run("postcat", ["-c", self.c["postfix_config"], "-eq", qid]))
        if env.account is None and self.store is not None:
            # UID solo desde pickup; jamás desde Received/From aportados por clientes.
            message = self.store.active(qid)
            binding = self.store.get("local_queue_" + qid)
            if message and is_local(message["account"]):
                binding = json.loads(message["envelope_key"]) if message["envelope_key"] else None
                if (binding is None and env.arrival is not None and abs(env.arrival - message["created"]) <= 2
                        and env.sender == message["sender"] and env.create_time and env.size is not None
                        and (env.size == message["size"] or (message["size"] is None and env.queue == "hold"))):
                    binding = {"account": message["account"], "key": local_envelope_key(env)}
                    self.store.db.execute("UPDATE messages SET envelope_key=? WHERE id=?", (json.dumps(binding), message["id"]))
            if binding and local_envelope_key(env) == binding["key"]:
                env.account = binding["account"]
        return env

    def action(self, option, qid, queue=None):
        if option not in ("-h", "-H", "-d", "-r") or not QUEUE_ID.fullmatch(qid):
            raise MonitorError("Operación de cola inválida")
        args = ["-c", self.c["postfix_config"], option, qid]
        if queue:
            args.append(queue)
        self.cmd.run("postsuper", args)


def resolve_recipients(store, config, router, queue=None, bootstrap=False):
    issues = []
    envelope_cache = {}
    if bootstrap and queue:
        for qid, item in queue.listing().items():
            if store.active(qid):
                continue
            try:
                env = queue.envelope(qid)
            except MonitorError:
                # Puede desaparecer entre postqueue y postcat; volverá por el log si sigue activo.
                continue
            if env.account and env.account not in config["ignore"] and env.arrival is not None:
                envelope_cache[qid] = env
                store.db.execute("INSERT INTO messages(qid,account,created,accepted,size,auth_line) VALUES (?,?,?,?,?,?)",
                                 (qid, env.account, env.arrival, env.arrival, env.size, "Bootstrap de sobre SASL"))
    rows = store.db.execute("SELECT m.*, (SELECT COUNT(*) FROM recipients r WHERE r.mid=m.id) AS known "
                            "FROM messages m WHERE removed=0").fetchall()
    for m in rows:
        if m["removed"] == 0 and queue and (m["nrcpt"] is None or m["known"] < m["nrcpt"] or m["sender"] is None):
            try:
                env = envelope_cache.get(m["qid"]) or queue.envelope(m["qid"])
                if is_local(m["account"]) and env.account is None:
                    # Primera vinculación a un sobre finalizado. No usar cabeceras.
                    if (env.arrival is not None and abs(env.arrival - m["created"]) <= 2
                            and m["size"] is not None and env.size == m["size"]
                            and env.sender == m["sender"] and env.create_time and not m["envelope_key"]):
                        binding = {"account": m["account"], "key": local_envelope_key(env)}
                        store.db.execute("UPDATE messages SET envelope_key=? WHERE id=?", (json.dumps(binding), m["id"]))
                        env.account = m["account"]
                if env.account != m["account"] or env.arrival is None or abs(env.arrival - m["created"]) > 2:
                    raise MonitorError("Identidad del sobre no coincide con el log: " + m["qid"])
                if env.queue not in ("incoming", "active", "deferred", "hold"):
                    raise MonitorError("Cola no reconocida para " + m["qid"])
                self_sender = env.sender
                store.sender(m["id"], m["account"], self_sender)
                for address in env.recipients:
                    store.recipient(m["id"], address, router.external(address, env.sender))
                if m["nrcpt"] is None:
                    total = store.db.execute("SELECT COUNT(*) FROM recipients WHERE mid=?", (m["id"],)).fetchone()[0]
                    store.db.execute("UPDATE messages SET nrcpt=? WHERE id=?", (total, m["id"]))
                if m["accepted"] is None:
                    store.db.execute("UPDATE messages SET accepted=?,size=? WHERE id=?", (env.arrival, env.size, m["id"]))
            except MonitorError as e:
                # AUTH/ID no equivale a aceptación. DATA puede seguir en curso o abortarse.
                # La falta de sobre sí es un problema cuando qmgr ya confirmó aceptación.
                if m["accepted"] is not None:
                    issues.append(str(e))
    for r in store.db.execute("SELECT r.mid,r.address FROM recipients r WHERE external IS NULL").fetchall():
        try:
            store.recipient(r["mid"], r["address"], router.external(r["address"]))
        except MonitorError as e:
            issues.append(str(e))
    return issues


def snapshot(store, account, when, period, limit):
    messages = []
    for m in store.db.execute("SELECT DISTINCT m.* FROM messages m JOIN recipients r ON r.mid=m.id "
                              "WHERE m.account=? AND m.accepted>? AND m.accepted<=? AND r.external=1 "
                              "ORDER BY m.accepted DESC LIMIT ?", (account, when - period, when, limit)):
        entry = dict(m)
        entry["samples"] = json.loads(entry["samples"])
        entry["recipients"] = [dict(r) for r in store.db.execute("SELECT address,external,delivery,log_line FROM recipients WHERE mid=?", (m["id"],))]
        messages.append(entry)
    return messages


def evaluate(store, config, now, offline=False):
    floor = 0 if offline else now - config.max_period - config["max_action_age"]
    groups = collections.defaultdict(list)
    resolving = {key[0][len("resolution_"):] for key in store.db.execute("SELECT key FROM meta WHERE key LIKE 'resolution_%'")
                 if store.get(key[0], {}).get("phase") in ("releasing", "unblocking")}
    for row in store.db.execute("SELECT m.account,m.accepted,m.foreign_sender,COUNT(*) AS n FROM messages m JOIN recipients r ON r.mid=m.id "
                                "WHERE r.external=1 AND m.accepted>=? AND m.accepted<=? GROUP BY m.id ORDER BY m.accepted,m.id", (floor, now)):
        if row["account"] not in config["ignore"] and row["account"] not in resolving:
            groups[row["account"]].append((row["accepted"], row["n"], row["foreign_sender"]))
    current, opened = {}, []
    for account, events in groups.items():
        base_maximum, period = config.limit(account)
        cutoff = store.db.execute("SELECT cutoff FROM acknowledgements WHERE account=?", (account,)).fetchone()
        cutoff = cutoff[0] if cutoff else -1
        window, total, foreign_total = collections.deque(), 0, 0
        best = None
        for when, count, foreign in events:
            window.append((when, count, foreign))
            total += count
            foreign_total += count * foreign
            while window and window[0][0] <= when - period:
                _, expired_count, expired_foreign = window.popleft()
                total -= expired_count
                foreign_total -= expired_count * expired_foreign
            maximum = max(1, base_maximum // config["foreign_sender_divisor"]) if foreign_total else base_maximum
            candidate = (total, maximum, when, foreign_total)
            if when > cutoff and (offline or when >= now - config["max_action_age"]) and total > maximum:
                if best is None or total * best[1] > best[0] * maximum:
                    best = candidate
        current_count = sum(n for t, n, _ in events if t > now - period)
        current_foreign = sum(n * f for t, n, f in events if t > now - period)
        effective = max(1, base_maximum // config["foreign_sender_divisor"]) if current_foreign else base_maximum
        # Tras una parada, una cuota aún excedida es actual aunque el último envío sea antiguo.
        if current_count > effective and events[-1][0] > cutoff:
            if best is None or current_count * best[1] > best[0] * effective:
                best = (current_count, effective, now, current_foreign)
        current[account] = {"external_recipients": current_count,
                            "max_sent": effective, "base_max_sent": base_maximum,
                            "foreign_sender_recipients": current_foreign, "time_period": period}
        if best is None:
            continue
        peak, maximum, peak_time, foreign_count = best
        reason = "foreign_sender_domain" if foreign_count else "volume"
        incident = store.db.execute("SELECT * FROM incidents WHERE account=? AND acked IS NULL", (account,)).fetchone()
        evidence = json.dumps(snapshot(store, account, peak_time, period, config["sample_count"]), ensure_ascii=False)
        if incident is None:
            cur = store.db.execute("INSERT INTO incidents(account,opened,event_time,peak,maximum,period,snapshot,base_maximum,reason,foreign_recipients) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (account, now, peak_time, peak, maximum, period, evidence, base_maximum, reason, foreign_count))
            iid = cur.lastrowid
            opened.append(iid)
            if not offline:
                for kind in ("block_smtp", "hold_queue", "reset_password", "notify"):
                    if config[kind] and (not is_local(account) or kind not in ("block_smtp", "reset_password")):
                        store.db.execute("INSERT OR IGNORE INTO actions(incident,kind) VALUES (?,?)", (iid, kind))
                if is_local(account) and config["block_local"]:
                    store.db.execute("INSERT OR IGNORE INTO actions(incident,kind) VALUES (?,'block_local')", (iid,))
        elif peak * incident["maximum"] > incident["peak"] * maximum:
            store.db.execute("UPDATE incidents SET peak=?,maximum=?,event_time=?,snapshot=?,base_maximum=?,reason=?,foreign_recipients=? WHERE id=?",
                             (peak, maximum, peak_time, evidence, base_maximum, reason, foreign_count, incident["id"]))
    return current, opened


class Guard:
    CLASS = "outbound_spam_remote"
    def __init__(self, config, commands):
        self.c, self.cmd = config, commands

    @property
    def restriction(self):
        # proxymap evita accesos desde el chroot SMTP; SQLite consulta en cada transacción.
        return "check_sasl_access proxy:sqlite:" + self.c["guard_map"] + ".cf"

    @property
    def database(self):
        return Path(self.c["guard_map"] + ".sqlite3")

    @property
    def source(self):
        return Path(self.c["guard_map"] + ".cf")

    @property
    def recipient_source(self):
        return Path(self.c["guard_map"] + "-recipient.cf")

    def permissions(self):
        for path in (self.database, self.source, self.recipient_source):
            if os.geteuid() == 0:
                os.chown(str(path), 0, grp.getgrnam("postfix").gr_gid)
                os.chmod(str(path), 0o640)
            else:
                os.chmod(str(path), 0o600)

    def initialize(self):
        if "sqlite" not in self.cmd.pc("-m").splitlines():
            raise MonitorError("Instale el paquete postfix-sqlite para el bloqueo SASL sin caché hash")
        if re.search(r"[\s,{}\"'\\]", self.c["guard_map"]):
            raise MonitorError("Ruta guard_map no válida para una restricción Postfix")
        with contextlib.closing(sqlite3.connect(str(self.database), timeout=5)) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS blocked (account TEXT PRIMARY KEY, action TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS routes (kind TEXT,key TEXT,external INTEGER,PRIMARY KEY(kind,key))")
                db.execute("CREATE TABLE IF NOT EXISTS route_meta (key TEXT PRIMARY KEY,value TEXT)")
        atomic_write(self.source, "# Gestionado por outbound-spam\ndbpath = %s\nquery = SELECT action FROM blocked WHERE account = lower('%%s')\n" % self.database)
        # Una clase con una sola consulta devuelve DUNNO para locales y continúa las reglas
        # preexistentes. No utiliza OK/permit, que podrían omitir controles de destinatario.
        query = """WITH input AS (SELECT lower('%s') AS address), parts AS (SELECT address,substr(address,instr(address,'@')+1) AS domain FROM input) SELECT CASE COALESCE((SELECT external FROM routes,parts WHERE kind='address' AND (key=address OR (substr(key,instr(key,'@'))='@'||domain AND substr(address,1,instr(key,'@')-1)=substr(key,1,instr(key,'@')-1) AND instr(COALESCE((SELECT value FROM route_meta WHERE key='delimiter'),'+'),substr(address,instr(key,'@'),1))>0)) ORDER BY length(key) DESC LIMIT 1),(SELECT external FROM routes,parts WHERE kind='domain' AND key=domain LIMIT 1),(SELECT external FROM routes,parts WHERE kind='suffix' AND substr(domain,-length(key))=key ORDER BY length(key) DESC LIMIT 1),(SELECT external FROM routes WHERE kind='default' LIMIT 1),1) WHEN 0 THEN 'DUNNO' ELSE '451 4.7.1 Envio externo suspendido temporalmente; contacte con el administrador' END FROM parts"""
        atomic_write(self.recipient_source, "# Gestionado por outbound-spam\ndbpath = %s\nquery = %s\n" % (self.database, query))
        self.permissions()
        self.refresh_routes(force=True)

    def refresh_routes(self, force=False):
        with contextlib.closing(sqlite3.connect(str(self.database), timeout=5)) as db:
            last = db.execute("SELECT value FROM route_meta WHERE key='updated'").fetchone()
            if not force and last and time.time() - float(last[0]) < 300:
                return
        router = Router(self.c, self.cmd)
        domains = set(self.c["local_domains"]) | {"localhost", "localhost.localdomain"}
        addresses = set(self.c["guard_route_addresses"])
        transports = set()
        delimiter = "+"
        if self.c["routing_mode"] == "auto":
            router.load()
            delimiter = router.parameters["recipient_delimiter"]
            domains.add(router.parameters["myhostname"])
            for item in re.split(r"[\s,]+", router.parameters["mydestination"]):
                if item and ":" not in item and not item.startswith(("!", "/")):
                    domains.add(item)
            if not self.c["local_domains"]:
                server = self.c["ispconfig_server_id"]
                domains.update(mysql_query(self.c, self.cmd, "SELECT domain FROM mail_domain WHERE active='y' AND server_id=%d;" % server).splitlines())
                addresses.update(mysql_query(self.c, self.cmd, "SELECT source FROM mail_forwarding WHERE active='y' AND server_id=%d;" % server).splitlines())
                addresses.update(mysql_query(self.c, self.cmd, "SELECT email FROM mail_user WHERE server_id=%d;" % server).splitlines())
                transports.update(mysql_query(self.c, self.cmd, "SELECT domain FROM mail_transport WHERE active='y' AND server_id=%d;" % server).splitlines())

        def external(address, visited=None):
            if self.c["routing_mode"] == "static":
                return int(mail_domain(address) not in domains)
            visited = set() if visited is None else visited
            if address.casefold() in visited or len(visited) >= 30:
                raise MonitorError("Bucle de alias al preparar el bloqueo: " + address)
            visited = visited | {address.casefold()}
            local, domain = address.rsplit("@", 1)
            keys = [address]
            for char in delimiter:
                if char in local:
                    keys.append(local.split(char, 1)[0] + "@" + domain)
                    break
            keys.append("@" + domain)
            aliases = router.lookup(router.parameters["virtual_alias_maps"], keys)
            if aliases:
                targets = [x for x in re.split(r"[\s,]+", aliases) if x]
                results = []
                for target in targets:
                    if target.startswith("@"):
                        target = local + target
                    results.append(router.external(target) if target.casefold() == address.casefold() else external(target, visited))
                return int(any(results))
            return router.external(address)

        rules = {}
        for entry in domains | transports | addresses:
            entry = entry.strip().casefold()
            if not entry:
                continue
            if entry.startswith("@"):
                kind, key, probe = "domain", entry[1:], "outbound-spam-probe" + entry
            elif "@" in entry:
                kind, key, probe = "address", entry, entry
            elif entry == "*":
                kind, key, probe = "default", "*", "outbound-spam-probe@unlisted.invalid"
            elif entry.startswith("."):
                kind, key, probe = "suffix", entry, "outbound-spam-probe@sub" + entry
            else:
                kind, key, probe = "domain", entry, "outbound-spam-probe@" + entry
            rules[(kind, key)] = external(probe)
        with contextlib.closing(sqlite3.connect(str(self.database), timeout=5)) as db:
            with db:
                db.execute("DELETE FROM routes")
                db.executemany("INSERT INTO routes VALUES (?,?,?)", [(kind, key, value) for (kind, key), value in rules.items()])
                db.execute("INSERT OR REPLACE INTO route_meta VALUES ('delimiter',?)", (delimiter,))
                db.execute("INSERT OR REPLACE INTO route_meta VALUES ('updated',?)", (str(time.time()),))

    def verify(self):
        text = self.cmd.pc("-h", "smtpd_recipient_restrictions")
        if not text.lstrip().startswith(self.restriction):
            raise MonitorError("Instale primero --install-postfix-guard; la comprobación SASL debe ser la primera")
        proxy = self.cmd.pc("-h", "proxy_read_maps")
        if not any(x in proxy for x in ("$smtpd_recipient_restrictions", "${smtpd_recipient_restrictions}", self.restriction.split(" ", 1)[1])):
            raise MonitorError("proxy_read_maps debe incluir $smtpd_recipient_restrictions")
        if "$" + self.CLASS not in proxy and "${" + self.CLASS + "}" not in proxy and "proxy:sqlite:" + str(self.recipient_source) not in proxy:
            raise MonitorError("proxy_read_maps debe incluir $" + self.CLASS)
        if self.CLASS not in self.cmd.pc("-h", "smtpd_restriction_classes").replace(",", " ").split():
            raise MonitorError("Falta la clase de bloqueo remoto")
        if self.cmd.pc("-h", self.CLASS) != "check_recipient_access proxy:sqlite:" + str(self.recipient_source):
            raise MonitorError("Clase de bloqueo remoto incorrecta")
        if not self.source.is_file() or not self.database.is_file() or not self.recipient_source.is_file():
            raise MonitorError("Faltan los archivos SQLite del bloqueo SMTP")
        # Una sobrescritura por servicio podría eludir la restricción global.
        output = self.cmd.pc("-P")
        for line in output.splitlines():
            if "/smtpd_recipient_restrictions" in line:
                value = line.split("=", 1)[1].strip()
                if not value.startswith(self.restriction):
                    raise MonitorError("master.cf sobrescribe smtpd_recipient_restrictions: " + line.split("=", 1)[0])

    def publish(self, accounts):
        accounts = {a for a in accounts if not is_local(a)}
        self.verify()
        self.refresh_routes(force=True)
        for account in accounts:
            if not account or re.search(r"[\s\x00-\x1f\x7f]", account):
                raise MonitorError("Cuenta no representable en mapa SASL: " + repr(account))
        # Mantener el inode: los lectores SQLite persistentes ven el COMMIT inmediatamente.
        with contextlib.closing(sqlite3.connect(str(self.database), timeout=5)) as db:
            with db:
                db.execute("DELETE FROM blocked")
                db.executemany("INSERT INTO blocked VALUES (?,?)", [(a, self.CLASS) for a in sorted(accounts)])
        for account in accounts:
            result = self.cmd.run("postmap", ["-c", self.c["postfix_config"], "-q", account, "sqlite:" + str(self.source)])
            if result.strip() != self.CLASS:
                raise MonitorError("No se verifica el bloqueo SASL de " + account)


def local_block_users(store, validate=False):
    users = set()
    for row in store.db.execute("SELECT account FROM blocks WHERE account LIKE 'local:%'"):
        identity = store.get("local_identity_" + row[0])
        if not identity:
            raise MonitorError("Falta la identidad UNIX duradera de " + row[0])
        if validate:
            current = unix_identity(identity["uid"])
            if any(current[k] != identity[k] for k in ("uid", "user", "home")):
                raise MonitorError("Identidad UNIX sustituida: " + row[0])
        users.add(identity["user"])
    return users


def publish_blocks(store, config, commands, account):
    if is_local(account):
        LocalGuard(config, commands, atomic_write).publish(local_block_users(store))
    else:
        Guard(config, commands).publish({r[0] for r in store.db.execute("SELECT account FROM blocks")})


def repair_local_guard(store, config, commands):
    guard = LocalGuard(config, commands, atomic_write)
    if store.get("recovery_pause") or not guard.backup.exists():
        return
    known = local_block_users(store)
    saved = guard.record()
    if commands.pc("-h", "authorized_submit_users") != saved["installed"]:
        raise MonitorError("authorized_submit_users cambió; se conserva el mapa local")
    damaged = False
    try:
        published = guard.users()
    except MonitorError:
        damaged = True
        if not guard.source.is_file() or not guard.source.read_text().startswith("# Gestionado por outbound-spam"):
            raise MonitorError("Mapa local dañado sin fuente gestionada verificable")
        published = {line.split()[0] for line in guard.source.read_text().splitlines() if line and not line.startswith("#")}
    resolving = set()
    for row in store.db.execute("SELECT key FROM meta WHERE key LIKE 'resolution_local:%'"):
        state = store.get(row[0], {})
        if state.get("phase") in ("releasing", "unblocking"):
            identity = store.get("local_identity_" + state["account"], {})
            if identity.get("user"):
                resolving.add(identity["user"])
    if published - known - resolving:
        store.set("recovery_pause", "Postfix conserva bloqueos locales sin historial privado; automatismos suspendidos")
        store.persist_critical()
        return
    if published != known or damaged:
        guard.publish(known, verify=False)


class Actions:
    def __init__(self, store, config, commands, now):
        self.s, self.c, self.cmd, self.now = store, config, commands, now
        self.queue, self.guard = Queue(config, commands, store), Guard(config, commands)

    def block(self, incident):
        if is_local(incident["account"]):
            identity = self.s.get("local_identity_" + incident["account"])
            if not identity or any(unix_identity(identity["uid"])[k] != identity[k] for k in ("uid", "user", "home")):
                raise MonitorError("Identidad UNIX ausente o sustituida: " + incident["account"])
        # Intención duradera: si se interrumpe la publicación, el siguiente run la reintenta.
        self.s.db.execute("INSERT OR REPLACE INTO blocks VALUES (?,?)", (incident["account"], incident["id"]))
        self.s.persist_critical()
        publish_blocks(self.s, self.c, self.cmd, incident["account"])

    def hold(self, incident):
        entries = self.queue.listing()
        router = Router(self.c, self.cmd)
        for qid, item in entries.items():
            existing = self.s.db.execute("SELECT * FROM held WHERE qid=?", (qid,)).fetchone()
            if item["queue_name"] == "hold" and not existing:
                # No apropiarse de retenciones previas de otros administradores/herramientas.
                continue
            env = self.queue.envelope(qid)
            if env.account != incident["account"]:
                continue
            # HOLD es por mensaje entero. Esperar a que terminen sus entregas locales;
            # no suspenderlas por una cuenta detectada enviando al exterior.
            if not env.pending_recipients or any(not router.external(a, env.sender) for a in env.pending_recipients):
                continue
            fingerprint = env.fingerprint
            if existing and existing["fingerprint"] != fingerprint:
                raise MonitorError("ID de cola reutilizado: " + qid)
            if existing and item["queue_name"] == "hold":
                continue
            self.s.db.execute("INSERT OR IGNORE INTO held VALUES (?,?,?,?,?)",
                               (qid, incident["account"], fingerprint, incident["id"], self.now))
            preserve_local_queue(self.s, env)
            self.s.persist_critical()
            if item["queue_name"] != "hold":
                self.queue.action("-h", qid)
            confirmed = self.queue.envelope(qid)
            if confirmed.queue != "hold" or confirmed.fingerprint != fingerprint:
                raise MonitorError("No se confirma HOLD para " + qid)

    def mysql(self, sql):
        return mysql_query(self.c, self.cmd, sql)

    @staticmethod
    def sql_text(text):
        return "CONVERT(0x" + text.encode("utf-8").hex() + " USING utf8mb4)"

    def password(self, incident, action):
        payload = json.loads(action["payload"])
        account = self.sql_text(incident["account"])
        server = self.c["ispconfig_server_id"]
        rows = self.mysql("SELECT mailuser_id,HEX(password) FROM mail_user WHERE email=%s AND server_id=%d;" % (account, server)).splitlines()
        if len(rows) != 1:
            raise MonitorError("ISPConfig: se esperaba exactamente una cuenta en mail_user")
        uid, encoded = rows[0].split("\t")
        if not uid.isdigit():
            raise MonitorError("mailuser_id no válido")
        old = bytes.fromhex(encoded).decode("ascii")
        if not payload:
            prefix = "{SHA512-CRYPT}" if old.startswith("{SHA512-CRYPT}") else ""
            if not old.startswith(prefix + "$6$"):
                raise MonitorError("Hash ISPConfig no compatible: se admite SHA512-CRYPT ($6$)")
            random_password = secrets.token_urlsafe(48)
            hashed = self.cmd.run("openssl", ["passwd", "-6", "-stdin"], data=random_password + "\n").strip()
            if not re.fullmatch(r"\$6\$[^\s]+", hashed):
                raise MonitorError("OpenSSL no ha generado un SHA512-CRYPT válido")
            length = self.mysql("SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
                                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='mail_user' AND COLUMN_NAME='password';")
            if not length.isdigit() or int(length) < len(prefix + hashed):
                raise MonitorError("La columna mail_user.password no admite el hash completo")
            payload = {"uid": uid, "old": old, "new": prefix + hashed}
            self.s.db.execute("UPDATE actions SET payload=? WHERE id=?", (json.dumps(payload), action["id"]))
            self.s.persist_critical()
        if uid != payload["uid"]:
            raise MonitorError("Ha cambiado la identidad mailuser_id")
        if old == payload["old"]:
            sql = ("UPDATE mail_user SET password=%s WHERE mailuser_id=%s AND email=%s AND server_id=%d "
                   "AND BINARY password=BINARY %s; SELECT ROW_COUNT();") % (
                       self.sql_text(payload["new"]), uid, account, server, self.sql_text(payload["old"]))
            if self.mysql(sql) != "1":
                raise MonitorError("ISPConfig: actualización concurrente, no se cambia la contraseña")
        elif old != payload["new"]:
            raise MonitorError("ISPConfig: la contraseña fue modificada por otro proceso")
        check = self.mysql("SELECT HEX(password) FROM mail_user WHERE mailuser_id=%s;" % uid)
        if check.casefold() != payload["new"].encode("ascii").hex():
            raise MonitorError("No se verifica el cambio de contraseña")
        self.cmd.run("doveadm", ["auth", "cache", "flush", incident["account"]])

    def report(self, incident):
        actions = self.s.db.execute("SELECT kind,state,error FROM actions WHERE incident=?", (incident["id"],)).fetchall()
        lines = ["Incidente de abuso SMTP saliente #%d" % incident["id"],
                 "Servidor: " + os.uname().nodename,
                 ("Identidad local: " if is_local(incident["account"]) else "Cuenta SASL: ") + incident["account"],
                 "Destinatarios externos en la ventana: %d (limite %d / %d segundos)" % (incident["peak"], incident["maximum"], incident["period"]),
                 "Fin de ventana (hora local): " + runtime.human_timestamp(incident["event_time"]),
                 "Se cuentan destinatarios aceptados, incluidos pendientes; no reintentos.",
                 "Limite base: %d; motivo: %s; externos con dominio remitente distinto: %d" %
                 (incident["base_maximum"] or incident["maximum"], incident["reason"], incident["foreign_recipients"]),
                 "Informe exclusivo de esta cuenta; separacion minima entre intentos: 6 horas.", "", "Acciones:"]
        lines.extend("- %s: %s%s" % (a["kind"], a["state"], " — " + a["error"] if a["error"] else "") for a in actions)
        if is_local(incident["account"]):
            lines += ["Web/UID: " + json.dumps(self.s.get("local_identity_" + incident["account"], {}), ensure_ascii=False),
                      "El bloqueo local impide sendmail/postdrop a cualquier destino, también local."]
        held = self.s.db.execute("SELECT COUNT(*) FROM held WHERE account=?", (incident["account"],)).fetchone()[0]
        lines += ["Mensajes registrados como retenidos: %d" % held, "", "Muestras de mensajes:"]
        for m in json.loads(incident["snapshot"]):
            lines += ["", "ID %s | IP %s | Message-ID %s" % (m["qid"], m["ip"], m["message_id"]),
                      "Remitente del sobre: %s; referencia de dominio: %s" %
                      (m.get("sender"), json.loads(m["local_identity"])["domains"] if m.get("local_identity") else mail_domain(incident["account"])),
                      "Asunto (si fue registrado): " + (m["subject"] or "no disponible"), m["auth_line"]]
            if m["queue_line"]:
                lines.append(m["queue_line"])
            for r in m["recipients"]:
                if r["external"] == 1:
                    lines.append("Externo: %s — estado observado: %s" % (r["address"], r["delivery"] or "pendiente/no observado"))
                    if r["log_line"]:
                        lines.append(r["log_line"])
            lines.extend(m["samples"])
        return "\n".join(lines) + "\n"

    def notify(self, incident):
        msg = email.message.EmailMessage()
        msg["From"] = self.c["alert_from"]
        msg["To"] = ", ".join(self.c["report"])
        msg["Subject"] = "[Outbound spam #%d] %s" % (incident["id"], incident["account"])
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = "<outbound-spam-%d-%s@%s>" % (incident["id"], int(self.now), self.c["alert_from"].rsplit("@", 1)[1])
        msg.set_content(self.report(incident))
        if self.c["alert_transport"] == "sendmail":
            self.cmd.run("sendmail", ["-i", "-f", self.c["alert_from"], "--"] + list(self.c["report"]), data=msg.as_string())
        else:
            context = ssl.create_default_context()
            cls = smtplib.SMTP_SSL if self.c["smtp_security"] == "tls" else smtplib.SMTP
            kwargs = {"timeout": self.c["command_timeout"]}
            if cls is smtplib.SMTP_SSL:
                kwargs["context"] = context
            with cls(self.c["smtp_host"], self.c["smtp_port"], **kwargs) as smtp:
                if self.c["smtp_security"] == "starttls":
                    smtp.starttls(context=context)
                if self.c["smtp_user"]:
                    smtp.login(self.c["smtp_user"], self.c["smtp_password"])
                refused = smtp.send_message(msg)
                if refused:
                    raise MonitorError("El SMTP de informes ha rechazado destinatarios")

    def process(self):
        if self.s.get("recovery_pause"):
            return
        if self.c["block_smtp"] and self.s.db.execute("SELECT 1 FROM blocks WHERE account NOT LIKE 'local:%' LIMIT 1").fetchone():
            try:
                self.guard.verify()
                self.guard.refresh_routes()
            except MonitorError as e:
                if corruption(e):
                    self.s.set("guard_checked", 0)
                self.s.db.execute("UPDATE actions SET state='failed',error=? WHERE kind='block_smtp' "
                                  "AND incident IN (SELECT id FROM incidents WHERE acked IS NULL)", (str(e),))
        # Reintentar HOLD mientras la cuenta esté en incidente: puede llegar correo entre runs.
        for i in self.s.db.execute("SELECT * FROM incidents WHERE acked IS NULL").fetchall():
            for kind in ("hold_queue",):
                if self.c[kind] and not self.s.get("hold_suppressed_" + str(i["id"]), False):
                    self.s.db.execute("INSERT OR IGNORE INTO actions(incident,kind) VALUES (?,?)", (i["id"], kind))
                    self.s.db.execute("UPDATE actions SET state='pending' WHERE incident=? AND kind=? AND state='done'", (i["id"], kind))
            if self.c["notify"]:
                self.s.db.execute("INSERT OR IGNORE INTO actions(incident,kind) VALUES (?,'notify')", (i["id"],))
            if self.c["notify"] and self.s.notice_due(i["account"], self.now, self.c["reminder_interval"]):
                self.s.db.execute("UPDATE actions SET state='pending' WHERE incident=? AND kind='notify'", (i["id"],))
        self.s.persist_critical()
        pending = self.s.db.execute("SELECT a.* FROM actions a JOIN incidents i ON i.id=a.incident "
                                    "WHERE a.state!='done' AND i.acked IS NULL ORDER BY "
                                    "CASE a.kind WHEN 'block_smtp' THEN 0 WHEN 'block_local' THEN 0 WHEN 'hold_queue' THEN 1 WHEN 'reset_password' THEN 2 ELSE 3 END,a.id").fetchall()
        for a in pending:
            if not self.c[a["kind"]]:
                continue
            if a["kind"] == "hold_queue" and self.s.get("hold_suppressed_" + str(a["incident"]), False):
                self.s.db.execute("UPDATE actions SET state='done',error=NULL WHERE id=?", (a["id"],))
                self.s.persist_critical()
                continue
            incident = self.s.db.execute("SELECT * FROM incidents WHERE id=?", (a["incident"],)).fetchone()
            if a["kind"] == "notify" and not self.s.notice_due(incident["account"], self.now, self.c["reminder_interval"]):
                continue
            try:
                held_before = self.s.db.execute("SELECT COUNT(*) FROM held WHERE incident=?", (incident["id"],)).fetchone()[0]
                self.s.db.execute("UPDATE actions SET attempts=attempts+1,attempted=? WHERE id=?", (self.now, a["id"]))
                self.s.persist_critical()
                if a["kind"] in ("block_smtp", "block_local"):
                    self.block(incident)
                elif a["kind"] == "hold_queue":
                    self.hold(incident)
                elif a["kind"] == "reset_password":
                    self.password(incident, a)
                else:
                    # Reservar antes del efecto externo: tampoco duplicar tras un envío incierto.
                    self.s.db.execute("INSERT INTO notice_state(account,last_attempt) VALUES (?,?) "
                                      "ON CONFLICT(account) DO UPDATE SET last_attempt=excluded.last_attempt",
                                      (incident["account"], self.now))
                    self.s.persist_critical()
                    self.notify(incident)
                    self.s.db.execute("UPDATE incidents SET last_notice=? WHERE id=?", (self.now, incident["id"]))
                    self.s.db.execute("UPDATE notice_state SET last_success=? WHERE account=?", (self.now, incident["account"]))
                self.s.db.execute("UPDATE actions SET state='done',error=NULL WHERE id=?", (a["id"],))
                if a["kind"] == "reset_password":
                    # No conservar hashes anteriores una vez confirmada la acción y la caché.
                    self.s.db.execute("UPDATE actions SET payload='{}' WHERE id=?", (a["id"],))
                held_after = self.s.db.execute("SELECT COUNT(*) FROM held WHERE incident=?", (incident["id"],)).fetchone()[0]
                if a["kind"] != "hold_queue" or held_after != held_before:
                    runtime.audit("accion_completada", incidente=incident["id"], cuenta=incident["account"],
                                  accion=a["kind"], retenidos_nuevos=held_after - held_before)
            except (MonitorError, OSError, smtplib.SMTPException, ValueError) as e:
                if corruption(e):
                    self.s.set("guard_checked", 0)
                self.s.db.execute("UPDATE actions SET state='failed',error=? WHERE id=?", (str(e)[:500], a["id"]))
                runtime.audit("accion_fallida", incidente=incident["id"], cuenta=incident["account"], accion=a["kind"], error=str(e))
            self.s.persist_critical()
        reports = private_directory(Path(self.c["state_dir"]) / "reports")
        for incident in self.s.db.execute("SELECT * FROM incidents WHERE acked IS NULL").fetchall():
            atomic_write(reports / ("incident-%d.txt" % incident["id"]), self.report(incident))


def coverage_issues(store, config, now):
    issues = []
    if store.get("recovery_pause"):
        issues.append(store.get("recovery_pause"))
    issues.extend(r[0] for r in store.db.execute("SELECT DISTINCT local_error FROM messages WHERE local_error IS NOT NULL AND accepted>=?", (now - config.max_period,)))
    for m in store.db.execute("SELECT m.qid,m.nrcpt,COUNT(r.address) AS known,SUM(CASE WHEN r.external IS NULL THEN 1 ELSE 0 END) AS unknown "
                              "FROM messages m LEFT JOIN recipients r ON r.mid=m.id WHERE m.accepted>=? "
                              "GROUP BY m.id HAVING (m.nrcpt IS NOT NULL AND COUNT(r.address)<m.nrcpt) OR "
                              "SUM(CASE WHEN r.external IS NULL THEN 1 ELSE 0 END)>0",
                              (now - config.max_period,)):
        issues.append("destinatarios/aceptación incompletos para " + m["qid"])
    issues.extend("acción %s fallida: %s" % (r[0], r[1]) for r in store.db.execute("SELECT a.kind,a.error FROM actions a JOIN incidents i ON i.id=a.incident WHERE state='failed' AND i.acked IS NULL"))
    issues.extend("operación de cola pendiente: " + (r[0] or "sin detalle") for r in store.db.execute("SELECT error FROM queue_ops WHERE state='failed'"))
    if store.db.execute("SELECT 1 FROM meta WHERE key LIKE 'resolution_%' AND value LIKE '%releasing%' LIMIT 1").fetchone():
        issues.append("resolución de cuenta pendiente")
    return list(dict.fromkeys(issues))


def summarize(store, config, now, current, issues):
    incidents = [dict(r) for r in store.db.execute("SELECT id,account,peak,maximum,period FROM incidents WHERE acked IS NULL")]
    held = store.db.execute("SELECT COUNT(*) FROM held").fetchone()[0]
    blocks = [r[0] for r in store.db.execute("SELECT account FROM blocks")]
    code = 2 if issues else (1 if incidents or held or blocks else 0)
    until = store.get("warm_until", 0)
    warming = until > now
    local_until = runtime.human_timestamp(until) if warming else None
    utc_until = dt.datetime.fromtimestamp(until, dt.timezone.utc).isoformat() if warming else None
    info = ["ventana en calentamiento hasta " + local_until + " (hora local)"] if warming else []
    message = "Spam Status: %s; incidentes=%d; retenidos=%d; bloqueadas=%d" % (
        "ERROR" if code == 2 else "ALERTA" if code == 1 else "CALENTAMIENTO" if warming else "OK", len(incidents), held, len(blocks))
    if incidents:
        message += "; " + ", ".join("%s: %d/%d en %ds" % (i["account"], i["peak"], i["maximum"], i["period"]) for i in incidents[:5])
    if issues:
        message += "; " + "; ".join(issues[:3])
    if info:
        message += "; " + "; ".join(info)
    return {"version": VERSION, "last_success": now, "stale_after": config["stale_after"],
            "last_success_local": runtime.human_timestamp(now), "analysis_at_local": runtime.human_timestamp(now),
            "code": code, "message": message, "incidents": incidents, "held": held,
            "blocked": blocks, "accounts": current, "issues": issues, "run_interval": config["run_interval"],
            "warming_up": warming, "warm_until_utc": utc_until, "warm_until_local": local_until, "info": info}


def require_root():
    if os.geteuid() != 0:
        raise MonitorError("Esta operación requiere root")


def install_guard(config, commands, store):
    guard = Guard(config, commands)
    main = Path(config["postfix_config"]) / "main.cf"
    backup = Path(config["state_dir"]) / "postfix-guard-backup.json"
    names = ("smtpd_sender_restrictions", "smtpd_recipient_restrictions", "smtpd_restriction_classes", "proxy_read_maps", guard.CLASS)
    actual = {key: commands.pc("-h", key) for key in names}
    if actual["smtpd_recipient_restrictions"].startswith(guard.restriction) and guard.recipient_source.exists():
        guard.verify()
        guard.refresh_routes(force=True)
        return "La integración Postfix ya estaba instalada"
    legacy = None
    old = dict(actual)
    old_files = {}
    old_blocks = []
    source_stat = None
    if backup.exists():
        legacy = json.loads(backup.read_text())
        expected = guard.restriction + (", " + legacy.get("restriction", "") if legacy.get("restriction") else "")
        if "parameters" in legacy or actual["smtpd_sender_restrictions"] != expected or guard.recipient_source.exists():
            raise MonitorError("El guard existente necesita revisión; no coincide con una instalación anterior conocida")
        old["smtpd_sender_restrictions"] = legacy["restriction"]
        if actual["proxy_read_maps"] != legacy["proxy_installed"]:
            raise MonitorError("proxy_read_maps cambió después de la instalación anterior")
        old["proxy_read_maps"] = legacy["proxy"]
        old_files[str(guard.source)] = guard.source.read_text()
        source_stat = guard.source.stat()
        with contextlib.closing(sqlite3.connect(str(guard.database))) as db:
            old_blocks = db.execute("SELECT account,action FROM blocked").fetchall()
    elif guard.source.exists() or guard.database.exists() or guard.recipient_source.exists():
        raise MonitorError("Ya existen archivos del guard sin respaldo de instalación")
    if actual[guard.CLASS] or guard.CLASS in actual["smtpd_restriction_classes"].replace(",", " ").split():
        raise MonitorError("La clase outbound_spam_remote ya existe y no pertenece a esta instalación")
    # Rechazar sobrescrituras por servicio antes de tocar main.cf.
    for line in commands.pc("-P").splitlines():
        if "/smtpd_recipient_restrictions" in line:
            raise MonitorError("Existe sobrescritura en master.cf: " + line.split("=", 1)[0])
    original = main.read_text(encoding="utf-8")
    new = dict(old)
    new["smtpd_recipient_restrictions"] = guard.restriction + (", " + old["smtpd_recipient_restrictions"] if old["smtpd_recipient_restrictions"] else "")
    new["smtpd_restriction_classes"] = (old["smtpd_restriction_classes"] + ", " if old["smtpd_restriction_classes"] else "") + guard.CLASS
    new[guard.CLASS] = "check_recipient_access proxy:sqlite:" + str(guard.recipient_source)
    for variable in ("smtpd_recipient_restrictions", guard.CLASS):
        if "$" + variable not in new["proxy_read_maps"] and "${" + variable + "}" not in new["proxy_read_maps"]:
            new["proxy_read_maps"] += " $" + variable
    record = {"version": 2, "parameters": old, "installed": new, "main_cf": original}
    atomic_write(backup, json.dumps(record, ensure_ascii=False))
    restart_attempted = False
    try:
        guard.initialize()
        for key, value in new.items():
            commands.pc("-e", key + "=" + value)
        commands.run("postfix", ["-c", config["postfix_config"], "check"])
        guard.verify()
        guard.publish({row[0] for row in store.db.execute("SELECT account FROM blocks")} | {row[0] for row in old_blocks})
        restart_attempted = True
        restart_postfix(config, commands)
    except Exception:
        for key, value in actual.items():
            commands.pc("-e", key + "=" + value)
        if legacy:
            atomic_write(guard.source, old_files[str(guard.source)], stat.S_IMODE(source_stat.st_mode))
            if os.geteuid() == 0:
                os.chown(str(guard.source), source_stat.st_uid, source_stat.st_gid)
            with contextlib.closing(sqlite3.connect(str(guard.database))) as db:
                with db:
                    db.execute("DELETE FROM blocked")
                    db.executemany("INSERT INTO blocked VALUES (?,?)", old_blocks)
            if guard.recipient_source.exists():
                guard.recipient_source.unlink()
            atomic_write(backup, json.dumps(legacy))
        else:
            for file in (guard.source, guard.database, guard.recipient_source):
                if file.exists():
                    file.unlink()
            backup.rename(str(backup) + ".failed-" + str(int(time.time())))
        if restart_attempted:
            try:
                if Path(config["postfix_config"]).resolve() == Path("/etc/postfix").resolve():
                    restart_postfix(config, commands)
                else:
                    try:
                        commands.run("postfix", ["-c", config["postfix_config"], "stop"])
                    except MonitorError:
                        pass
                    commands.run("postfix", ["-c", config["postfix_config"], "start"])
            except MonitorError as restore_error:
                raise MonitorError("Configuración restaurada; revisar el arranque de Postfix y " + str(backup)) from restore_error
        raise
    store.set("guard_installed", True)
    store.persist_critical()
    return "Integración Postfix remota instalada: locales continúan con sus controles originales; instancia reiniciada"


def uninstall_guard(config, commands, store):
    if store.db.execute("SELECT COUNT(*) FROM blocks WHERE account NOT LIKE 'local:%'").fetchone()[0]:
        raise MonitorError("Desbloquee las cuentas antes de retirar la integración")
    backup = Path(config["state_dir"]) / "postfix-guard-backup.json"
    saved = json.loads(backup.read_text(encoding="utf-8"))
    if "parameters" not in saved:
        raise MonitorError("Actualice primero la integración anterior con --install-postfix-guard")
    for key, expected in saved["installed"].items():
        if commands.pc("-h", key) != expected:
            raise MonitorError("Ha cambiado " + key + "; retirada manual necesaria")
    for key, value in saved["parameters"].items():
        commands.pc("-e", key + "=" + value)
    commands.run("postfix", ["-c", config["postfix_config"], "check"])
    restart_postfix(config, commands)
    for path in (Guard(config, commands).source, Guard(config, commands).database, Guard(config, commands).recipient_source):
        path.unlink()
    backup.rename(str(backup) + ".removed-" + str(int(time.time())))
    store.set("guard_installed", False)
    store.persist_critical()
    return "Integración Postfix retirada"


def install_monit(config, commands, base):
    target = Path(config["monit_config"])
    enabled = Path(config["monit_enabled"])
    script = Path(config["monit_script"])
    for p in (target.parent, enabled.parent, script.parent):
        if not p.is_dir():
            raise MonitorError("No existe el directorio Monit: " + str(p))
    for p in (target, script):
        if p.exists():
            if "outbound-spam" not in p.read_text(encoding="utf-8"):
                raise MonitorError("Archivo ajeno al instalador: " + str(p))
    if enabled.is_symlink() and enabled.resolve() != target.resolve():
        raise MonitorError("Enlace Monit ocupado por otra configuración")
    if enabled.exists() and not enabled.is_symlink():
        raise MonitorError("La ruta conf-enabled no es un enlace gestionado")
    for value in (str(script), config["state_dir"]):
        if re.search(r"[\s\"'\\]", value):
            raise MonitorError("Las rutas Monit no pueden contener espacios ni comillas")
    content = '# outbound-spam: generado por --install-monit\ncheck program Spam_Status with path "%s %s/status.json"\n    with timeout 10 seconds\n    group mail\n    if status != 0 then alert\n' % (script, config["state_dir"])
    old_target = target.read_text() if target.exists() else None
    old_script = script.read_text() if script.exists() else None
    had_link = enabled.is_symlink()
    try:
        atomic_write(script, (base / "outbound-spam-status.sh").read_text(), 0o700)
        atomic_write(target, content)
        if not had_link:
            enabled.symlink_to(target)
        commands.run("monit", ["-t"])
        commands.run("systemctl", ["enable", "monit"])
        commands.run("systemctl", ["restart", "monit"])
    except Exception:
        if not had_link and enabled.is_symlink():
            enabled.unlink()
        for path, old, mode in ((target, old_target, 0o600), (script, old_script, 0o700)):
            if old is None:
                if path.exists():
                    path.unlink()
            else:
                atomic_write(path, old, mode)
        raise
    return "Spam_Status instalado, Monit habilitado y reiniciado"


def uninstall_monit(config, commands):
    target, enabled, script = (Path(config[k]) for k in ("monit_config", "monit_enabled", "monit_script"))
    if not target.exists() and not enabled.is_symlink():
        return "Monitor no instalado"
    if not target.is_file() or "# outbound-spam" not in target.read_text():
        raise MonitorError("La configuración Monit no pertenece a outbound-spam")
    if enabled.is_symlink() and enabled.resolve() != target.resolve():
        raise MonitorError("Enlace Monit ajeno")
    if enabled.exists() and not enabled.is_symlink():
        raise MonitorError("conf-enabled no es un enlace gestionado")
    previous = target.read_text()
    had_link = enabled.is_symlink()
    previous_script = script.read_text() if script.exists() else None
    if previous_script and "outbound-spam" not in previous_script:
        raise MonitorError("El comprobador no pertenece a outbound-spam")
    try:
        if had_link:
            enabled.unlink()
        target.unlink()
        if script.exists():
            script.unlink()
        commands.run("monit", ["-t"])
        commands.run("systemctl", ["restart", "monit"])
    except Exception:
        atomic_write(target, previous)
        if previous_script is not None:
            atomic_write(script, previous_script, 0o700)
        if had_link and not enabled.is_symlink():
            enabled.symlink_to(target)
        raise
    return "Spam_Status retirado; Monit y la protección de correo siguen disponibles"


def postfix_seconds(value):
    match = re.fullmatch(r"(\d+)([smhdw]?)", value.strip())
    if not match:
        raise MonitorError("Duración Postfix no reconocida: " + value)
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def reconcile_held(store, config, commands):
    if not store.db.execute("SELECT 1 FROM held LIMIT 1").fetchone():
        return
    entries = Queue(config, commands).listing()
    # Solo se olvida la propiedad de IDs ausentes; jamás se opera sobre su sustituto.
    for row in store.db.execute("SELECT qid FROM held").fetchall():
        if row["qid"] not in entries:
            store.db.execute("DELETE FROM held WHERE qid=?", (row["qid"],))
    store.persist_critical()


def repair_guard(store, config, commands, recovery):
    guard = Guard(config, commands)
    if store.get("recovery_pause"):
        return
    if not store.get("guard_installed") and not guard.source.exists():
        return
    active = commands.pc("-h", "smtpd_recipient_restrictions").startswith(guard.restriction)
    if not active:
        return
    if guard.source.exists() and "# Gestionado por outbound-spam" not in guard.source.read_text():
        raise MonitorError("Mapa ajeno a outbound-spam; no se repara automáticamente")
    checked = False
    try:
        if not guard.database.exists():
            raise ValueError("Mapa SQLite ausente")
        if time.time() - store.get("guard_checked", 0) >= config["integrity_interval"]:
            check_database(guard.database, ("blocked", "routes", "route_meta"), protected=False)
            checked = True
        with contextlib.closing(sqlite3.connect(guard.database.absolute().as_uri() + "?mode=ro", uri=True)) as db:
            published = {r[0] for r in db.execute("SELECT account FROM blocked")}
        known = {r[0] for r in store.db.execute("SELECT account FROM blocks") if not is_local(r[0])}
        resolving = {row[0][len("resolution_"):] for row in store.db.execute("SELECT key FROM meta WHERE key LIKE 'resolution_%'")
                     if store.get(row[0], {}).get("phase") in ("releasing", "unblocking")}
        if published - known - resolving:
            store.set("recovery_pause", "Postfix conserva bloqueos sin historial privado; análisis activo, automatismos suspendidos")
            store.persist_critical()
            return
    except Exception as exc:
        if guard.database.exists() and not corruption(exc) and str(exc) != "SQLite sin el esquema esperado":
            raise
        # Construir y validar fuera de servicio. Un fallo de SQL/rutas no debe publicar
        # una tabla vacía que suprima bloqueos existentes.
        temp_config = object.__new__(Config)
        temp_config.v = dict(config.v, guard_map=str(recovery.root / ("guard-build-" + secrets.token_hex(8))))
        candidate = Guard(temp_config, commands)
        try:
            candidate.initialize()
            accounts = {r[0] for r in store.db.execute("SELECT account FROM blocks") if not is_local(r[0])}
            with contextlib.closing(sqlite3.connect(str(candidate.database))) as db:
                with db:
                    db.executemany("INSERT INTO blocked VALUES (?,?)", [(account, guard.CLASS) for account in accounts])
            check_database(candidate.database, ("blocked", "routes", "route_meta"), protected=False)
            for account in accounts:
                result = commands.run("postmap", ["-c", config["postfix_config"], "-q", account, "sqlite:" + str(candidate.source)])
                if result.strip() != guard.CLASS:
                    raise MonitorError("No se verifica el mapa candidato")
            store.set("guard_restart_pending", True)
            store.persist_critical()
            recovery.quarantine([guard.database, Path(str(guard.database) + "-wal"), Path(str(guard.database) + "-shm")], "Mapa Postfix dañado/ausente")
            os.replace(str(candidate.database), str(guard.database))
            atomic_write(guard.source, candidate.source.read_text().replace(str(candidate.database), str(guard.database)))
            atomic_write(guard.recipient_source, candidate.recipient_source.read_text().replace(str(candidate.database), str(guard.database)))
            guard.permissions()
        finally:
            for path in (candidate.database, candidate.source, candidate.recipient_source):
                if path.exists():
                    path.unlink()
        recovery.note = "Mapa SQLite de Postfix reconstruido; bloqueos conservados e instancia reiniciada"
        checked = True
    if store.get("guard_restart_pending"):
        restart_postfix(config, commands)
        store.set("guard_restart_pending", False)
        store.persist_critical()
    if checked:
        store.set("guard_checked", time.time())
        store.db.commit()


def admin_incident(store, config, account, now):
    row = store.db.execute("SELECT * FROM incidents WHERE account=? AND acked IS NULL", (account,)).fetchone()
    if row:
        return row
    maximum, period = config.limit(account)
    cur = store.db.execute("INSERT INTO incidents(account,opened,event_time,peak,maximum,period,snapshot,base_maximum,reason) VALUES (?,?,?,?,?,?,?,?,?)",
                           (account, now, now, 0, maximum, period, "[]", maximum, "manual"))
    store.persist_critical()
    return store.db.execute("SELECT * FROM incidents WHERE id=?", (cur.lastrowid,)).fetchone()


def finish_queue_op(store, config, commands, item):
    queue = Queue(config, commands, store)
    entries = queue.listing()
    if item["qid"] not in entries:
        store.db.execute("DELETE FROM held WHERE qid=? AND fingerprint=?", (item["qid"], item["fingerprint"]))
        store.db.execute("UPDATE queue_ops SET state='done',error=NULL WHERE id=?", (item["id"],))
        store.persist_critical()
        return
    env = queue.envelope(item["qid"])
    if env.account != item["account"] or env.fingerprint != item["fingerprint"]:
        raise MonitorError("Identidad de cola sustituida: " + item["qid"])
    operation = item["operation"]
    if operation == "release_auto":
        age = min(postfix_seconds(commands.pc("-h", name)) for name in ("maximal_queue_lifetime", "bounce_queue_lifetime")) / 2
        operation = "requeue" if time.time() - env.arrival >= age else "release"
    if operation == "purge":
        router = Router(config, commands)
        if not env.pending_recipients or any(not router.external(r, env.sender) for r in env.pending_recipients):
            raise MonitorError("Se conservan entregas locales pendientes de " + item["qid"])
        if env.queue != "hold":
            queue.action("-h", item["qid"])
            env = queue.envelope(item["qid"])
            if env.queue != "hold" or env.fingerprint != item["fingerprint"]:
                raise MonitorError("No se confirma HOLD antes del borrado: " + item["qid"])
        queue.action("-d", item["qid"], "hold")
    elif operation in ("delete", "release", "requeue"):
        if env.queue == "hold":
            queue.action({"delete": "-d", "release": "-H", "requeue": "-r"}[operation], item["qid"], "hold")
        elif operation == "delete":
            raise MonitorError("El mensaje ya no está en HOLD: " + item["qid"])
    else:
        raise MonitorError("Operación persistida desconocida")
    store.db.execute("DELETE FROM held WHERE qid=? AND fingerprint=?", (item["qid"], item["fingerprint"]))
    store.db.execute("UPDATE queue_ops SET state='done',error=NULL WHERE id=?", (item["id"],))
    store.persist_critical()
    runtime.audit("operacion_cola_completada", cuenta=item["account"], cola=item["qid"], accion=operation)


def queue_operation(store, config, commands, account, env, operation, now):
    preserve_local_queue(store, env)
    existing = store.db.execute("SELECT * FROM queue_ops WHERE account=? AND qid=? AND fingerprint=? AND operation=? AND state IN ('pending','failed') ORDER BY created LIMIT 1",
                                (account, env.qid, env.fingerprint, operation)).fetchone()
    if existing is None:
        ident = secrets.token_hex(16)
        store.db.execute("INSERT INTO queue_ops VALUES (?,?,?,?,?,'pending',?,NULL)",
                         (ident, account, env.qid, env.fingerprint, operation, now))
        store.persist_critical()
        existing = store.db.execute("SELECT * FROM queue_ops WHERE id=?", (ident,)).fetchone()
    try:
        finish_queue_op(store, config, commands, existing)
    except MonitorError as exc:
        store.db.execute("UPDATE queue_ops SET state='failed',error=? WHERE id=?", (str(exc), existing["id"]))
        store.persist_critical()
        raise


def reconcile_queue_ops(store, config, commands):
    if store.get("recovery_pause"):
        return
    for item in store.db.execute("SELECT * FROM queue_ops WHERE state IN ('pending','failed') ORDER BY created").fetchall():
        try:
            finish_queue_op(store, config, commands, item)
        except MonitorError as exc:
            store.db.execute("UPDATE queue_ops SET state='failed',error=? WHERE id=?", (str(exc), item["id"]))
            store.persist_critical()
    for key in [r[0] for r in store.db.execute("SELECT key FROM meta WHERE key LIKE 'resolution_%'")]:
        state = store.get(key)
        if state and state.get("phase") == "releasing":
            finalize_resolution(store, config, commands, state["account"], time.time())
        elif state and state.get("phase") == "unblocking":
            publish_blocks(store, config, commands, state["account"])
            store.set(key, {"account": state["account"], "phase": "done"})
            store.persist_critical()


def finalize_resolution(store, config, commands, account, now):
    if store.db.execute("SELECT 1 FROM held WHERE account=?", (account,)).fetchone():
        return False
    if store.db.execute("SELECT 1 FROM queue_ops WHERE account=? AND state IN ('pending','failed')", (account,)).fetchone():
        return False
    state = store.get("resolution_" + account)
    # La intención de desbloqueo queda fuera de la tabla blocks antes de publicar.
    # Si se interrumpe, la marca releasing obliga a volver a publicar el conjunto.
    store.db.execute("DELETE FROM blocks WHERE account=?", (account,))
    store.persist_critical()
    if state.get("had_block"):
        publish_blocks(store, config, commands, account)
    store.db.execute("INSERT OR REPLACE INTO acknowledgements VALUES (?,?)", (account, now))
    store.set("resolution_" + account, {"account": account, "phase": "done", "at": now})
    store.persist_critical()
    return True


def manage(store, config, commands, args, now):
    queue = Queue(config, commands, store)
    account = (args.list_held or args.release_held or args.delete_held or args.unblock or args.ack or
               getattr(args, "purge_user_queue", None) or getattr(args, "resolve_user", None) or getattr(args, "block_user", None))
    account = account.casefold()
    if is_local(account):
        account = "local:" + str(int(account.split(":", 1)[1]))
    if store.get("recovery_pause") and any((getattr(args, "resolve_user", None), getattr(args, "block_user", None), args.unblock)):
        raise MonitorError("Historial crítico incompleto: conserve las restricciones y restaure una copia verificada antes de resolver/bloquear/desbloquear")
    if getattr(args, "block_user", None):
        if is_local(account) and not store.get("local_identity_" + account):
            identity = unix_identity(account.split(":", 1)[1])
            identity["domains"] = Websites(config, lambda sql: mysql_query(config, commands, sql)).domains(identity["user"])
            if identity["domains"] is None:
                raise MonitorError("El usuario UNIX no corresponde a una web de esta instancia ISPConfig")
            store.set("local_identity_" + account, identity)
        incident = admin_incident(store, config, account, now)
        Actions(store, config, commands, now).block(incident)
        return ("Inyección sendmail/postdrop suspendida para " if is_local(account) else "Bloqueo remoto publicado para ") + account
    if getattr(args, "purge_user_queue", None):
        errors, count, skipped = [], 0, 0
        router = Router(config, commands)
        for qid in queue.listing():
            try:
                env = queue.envelope(qid)
                if env.account != account:
                    continue
                if not env.pending_recipients or any(not router.external(r, env.sender) for r in env.pending_recipients):
                    skipped += 1
                    continue
                queue_operation(store, config, commands, account, env, "purge", now)
                count += 1
            except MonitorError as exc:
                errors.append(str(exc))
        if errors:
            raise MonitorError("Eliminados %d; locales/mixtos conservados %d; errores: %s" % (count, skipped, "; ".join(errors)))
        return "Eliminados %d salientes de %s; locales/mixtos conservados %d; el bloqueo remoto se conserva" % (count, account, skipped)
    if getattr(args, "resolve_user", None):
        previous = store.get("resolution_" + account, {})
        had_block = previous.get("had_block", False) if previous.get("phase") == "releasing" else False
        had_block = had_block or bool(store.db.execute("SELECT 1 FROM blocks WHERE account=?", (account,)).fetchone())
        store.set("resolution_" + account, {"account": account, "phase": "releasing", "had_block": had_block})
        store.db.execute("UPDATE incidents SET acked=? WHERE account=? AND acked IS NULL", (now, account))
        store.db.execute("INSERT OR REPLACE INTO acknowledgements VALUES (?,?)", (account, now))
        # Incluir HOLD realizado por una purga interrumpida antes del borrado, aunque aún
        # no hubiera entrada en held. La intención de liberar queda duradera antes de cancelar.
        candidates = {(r[0], r[1]) for r in store.db.execute("SELECT qid,fingerprint FROM held WHERE account=?", (account,))}
        candidates.update((r[0], r[1]) for r in store.db.execute("SELECT qid,fingerprint FROM queue_ops WHERE account=? AND state IN ('pending','failed')", (account,)))
        for qid, fingerprint in candidates:
            exists = store.db.execute("SELECT 1 FROM queue_ops WHERE account=? AND qid=? AND fingerprint=? AND operation='release_auto' AND state IN ('pending','failed')", (account, qid, fingerprint)).fetchone()
            if not exists:
                store.db.execute("INSERT INTO queue_ops VALUES (?,?,?,?,?,'pending',?,NULL)",
                                 (secrets.token_hex(16), account, qid, fingerprint, "release_auto", now))
        store.db.execute("UPDATE queue_ops SET state='cancelled' WHERE account=? AND operation!='release_auto' AND state IN ('pending','failed')", (account,))
        store.persist_critical()
        errors = []
        for item in store.db.execute("SELECT * FROM queue_ops WHERE account=? AND operation='release_auto' AND state IN ('pending','failed')", (account,)).fetchall():
            try:
                finish_queue_op(store, config, commands, item)
            except MonitorError as exc:
                store.db.execute("UPDATE queue_ops SET state='failed',error=? WHERE id=?", (str(exc), item["id"]))
                store.persist_critical()
                errors.append(str(exc))
        if errors or not finalize_resolution(store, config, commands, account, now):
            raise MonitorError("Resolución pendiente; se conserva la suspensión remota: " + "; ".join(errors))
        return "Falso positivo resuelto: retenciones propias liberadas, bloqueo remoto retirado e incidente reconocido. Revise límite/excepción si continúa el mismo volumen."
    if args.list_held:
        return json.dumps(runtime.readable_dates([dict(r) for r in store.db.execute("SELECT qid,held_at,incident FROM held WHERE account=?", (account,))]), indent=2)
    if args.ack:
        store.db.execute("UPDATE incidents SET acked=? WHERE account=? AND acked IS NULL", (now, account))
        store.db.execute("INSERT OR REPLACE INTO acknowledgements VALUES (?,?)", (account, now))
        store.persist_critical()
        return "Incidente reconocido; no se liberan mensajes ni se desbloquea la cuenta"
    if args.unblock:
        if not store.db.execute("SELECT 1 FROM blocks WHERE account=?", (account,)).fetchone() and store.get("resolution_" + account, {}).get("phase") != "unblocking":
            raise MonitorError("La cuenta no está registrada como bloqueada")
        store.set("resolution_" + account, {"account": account, "phase": "unblocking"})
        store.db.execute("DELETE FROM blocks WHERE account=?", (account,))
        # Impedir que un bloqueo pendiente se vuelva a ejecutar tras una orden manual.
        store.db.execute("UPDATE actions SET state='done',error=NULL WHERE kind IN ('block_smtp','block_local') AND incident IN "
                         "(SELECT id FROM incidents WHERE account=? AND acked IS NULL)", (account,))
        store.persist_critical()
        publish_blocks(store, config, commands, account)
        store.set("resolution_" + account, {"account": account, "phase": "done"})
        store.persist_critical()
        return "Cuenta desbloqueada; se conserva la retención y el incidente"
    if args.release_held or args.delete_held:
        # Una decisión manual desactiva la repetición de HOLD para el incidente abierto.
        incident_ids = [r[0] for r in store.db.execute("SELECT id FROM incidents WHERE account=? AND acked IS NULL", (account,))]
        for iid in incident_ids:
            store.set("hold_suppressed_" + str(iid), True)
        store.persist_critical()
        errors, count, requeued = [], 0, 0
        requeue_age = None
        if args.release_held:
            requeue_age = min(postfix_seconds(commands.pc("-h", name)) for name in
                              ("maximal_queue_lifetime", "bounce_queue_lifetime")) / 2
        for item in store.db.execute("SELECT * FROM held WHERE account=?", (account,)).fetchall():
            try:
                env = queue.envelope(item["qid"])
                if env.queue != "hold" or env.account != account or env.fingerprint != item["fingerprint"]:
                    raise MonitorError("Identidad/HOLD no coincide para " + item["qid"])
                option = "-d"
                if args.release_held:
                    option = "-r" if now - env.arrival >= requeue_age else "-H"
                    requeued += int(option == "-r")
                queue_operation(store, config, commands, account, env, {"-d": "delete", "-r": "requeue", "-H": "release"}[option], now)
                count += 1
            except MonitorError as e:
                errors.append(str(e))
        if errors:
            raise MonitorError("Procesados %d; errores: %s" % (count, "; ".join(errors)))
        return "%d mensajes %s (%d reencolados por antigüedad); se mantiene el bloqueo SMTP" % (count, "liberados" if args.release_held else "eliminados", requeued)


def cli():
    guide = """GUÍA RÁPIDA DE INCIDENCIAS
  Revisar una alerta y sus retenciones:
    outbound-spam.sh --status
    outbound-spam.sh --list-held cuenta@example.org

  Cuenta comprometida: suspender sus nuevos envíos externos y purgar lo pendiente:
    outbound-spam.sh --block-user cuenta@example.org
    outbound-spam.sh --purge-user-queue cuenta@example.org
  La purga BORRA mensajes; conserva los locales/mixtos con locales pendientes.

  Falso positivo: ajustar antes el límite/excepción en la configuración y resolver:
    outbound-spam.sh --set-custom-limit cuenta@example.org 2000 900
    outbound-spam.sh --resolve-user cuenta@example.org
  Libera las retenciones propias, reconoce el incidente y retira el bloqueo remoto.

  Límites y excepciones por cuenta, guardados en outbound-spam.conf:
    outbound-spam.sh --set-custom-limit boletin@ejemplo.com 2000 900
      Hasta 2000 destinatarios externos en 900 segundos; sustituye su límite anterior
      y retira la cuenta de ignore si estaba ignorada.
    outbound-spam.sh --set-ignore boletin@ejemplo.com
      Ignora la cuenta y elimina su límite particular; repetirlo no crea duplicados.
    outbound-spam.sh --set-custom-limit boletin@ejemplo.com 500 3600
      Vuelve a supervisarla con 500 destinatarios externos por hora.
  Requieren una dirección válida o local:UID y enteros positivos; conservan las demás cuentas.
  Se aplican en la siguiente pasada, sin reiniciar. No desbloquean ni liberan HOLD.

  Decidir por separado el destino de mensajes retenidos:
    outbound-spam.sh --release-held cuenta@example.org   # liberar, sin desbloquear
    outbound-spam.sh --delete-held cuenta@example.org    # eliminar, irreversible
    outbound-spam.sh --unblock cuenta@example.org        # permitir externos, sin liberar
    outbound-spam.sh --ack cuenta@example.org            # reconocer, sin liberar/desbloquear

  Instalar el servicio/monitor o actualizar el programa:
    outbound-spam.sh --install-service
    outbound-spam.sh --install-monit
    outbound-spam.sh --upgrade

  Webs PHP/sendmail, identificadas automáticamente por UID e ISPConfig:
    outbound-spam.sh --inspect-local-user web6 --sender ventas@ejemplo.com
    outbound-spam.sh --set-custom-limit local:5009 300 3600
    outbound-spam.sh --resolve-user local:5009
  La cuota cuenta externos. Su bloqueo impide toda inyección sendmail/postdrop,
  también a buzones locales; SMTP sin AUTH a localhost requiere otro control.

  Los avisos de correo tienen mínimo 6 horas por cuenta. Detección y contención
  siguen en cada pasada. El calentamiento es informativo y no hace fallar Monit.
  Fechas de presentación: hora local del servidor. Estado e inventario: no borrar.
"""
    launcher = Path(os.environ.get("OUTBOUND_SPAM_BASE", Path(__file__).resolve().parent.parent)) / "outbound-spam.sh"
    guide = guide.replace("outbound-spam.sh --", str(launcher) + " --")
    p = argparse.ArgumentParser(prog="outbound-spam.sh", description="Control de spam saliente SMTP AUTH y webs PHP/sendmail: log incremental y cola Postfix.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=guide)
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--config-fd", type=int, help=argparse.SUPPRESS)
    p.add_argument("--config", "-c", "--config-path", dest="config_path", default=str(Path(os.environ.get("OUTBOUND_SPAM_BASE", Path(__file__).resolve().parent.parent)) / "outbound-spam.conf"), help="Archivo Bash privado (0600); se recarga y conserva al actualizar")
    p.add_argument("--state-dir", help="Ruta de estado de esta instancia; en replay debe ser distinta de la operativa")
    p.add_argument("--now", type=float, help="Reloj UNIX de referencia; solo replay")
    p.add_argument("--local-domain", action="append", default=[], help="Dominio local adicional; solo replay")
    p.add_argument("--from-start", action="store_true", help="Leer desde principio al crear el cursor")
    p.add_argument("--quiet", action="store_true", help="No imprimir resumen si se ejecuta correctamente")
    p.add_argument("--max-sent", type=int, help="Máximo de destinatarios externos por cuenta; sin otra operación guarda/aplica ajustes")
    p.add_argument("--time-period", type=int, help="Ventana de detección en segundos")
    p.add_argument("--outbound-delay", help="Freno global SMTP: auto, 0s, 1s, 4s...; solo configuración")
    p.add_argument("--postfix-client-limit", type=int, help="Límite de recepción por IP; 0 preserva local/NAT")
    p.add_argument("--destination-delay", help="Retraso por destino, simultáneo al freno por transporte")
    p.add_argument("--lock-wait", type=float, help="Segundos para esperar una operación activa")
    p.add_argument("--sender", help="Remitente del sobre a comprobar; solo con --inspect-local-user")
    p.add_argument("--upgrade-lock-fd", type=int, help=argparse.SUPPRESS)
    choices = p.add_mutually_exclusive_group()
    choices.add_argument("--yes", action="store_true", help="Aceptar creación del bloque Postfix sin pregunta")
    choices.add_argument("--skip-postfix", action="store_true", help="Guardar límites sin modificar/reiniciar Postfix")
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--set-limits", action="store_true", help="Guardar límites, ajustar main.cf y reiniciar su instancia")
    mode.add_argument("--set-custom-limit", nargs=3, metavar=("CUENTA", "MAXIMO", "SEGUNDOS"),
                      help="Guardar límite particular de destinatarios externos; sustituir el anterior y retirar de ignore")
    mode.add_argument("--set-ignore", metavar="CUENTA", help="Guardar cuenta ignorada y eliminar su límite particular")
    mode.add_argument("--configure-postfix", action="store_true", help="Instalar/actualizar el bloque Ratelimits coordinado")
    mode.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--daemon", action="store_true", help="Proceso del servicio systemd; espera entre pasadas completas")
    mode.add_argument("--upgrade", action="store_true", help="Descargar/verificar el release estable; conservar config/estado y actualizar unidad propia intacta")
    mode.add_argument("--prepare-upgrade", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--install-service", action="store_true", help="Crear, habilitar e iniciar la unidad systemd")
    mode.add_argument("--uninstall-service", action="store_true", help="Detener y retirar la unidad, conservando datos, bloqueos y HOLD")
    mode.add_argument("--replay", metavar="LOG", help="Análisis offline aislado, sin órdenes Postfix/SQL ni notificaciones")
    mode.add_argument("--status", action="store_true", help="Mostrar estado y fechas locales: código 0 sano/calentamiento, 1 alerta, 2 error")
    mode.add_argument("--doctor", action="store_true", help="Comprobar dependencias y configuración, sin acciones de contención")
    mode.add_argument("--inspect-local-user", metavar="UID_O_USUARIO", help="Consultar identidad UNIX y dominios ISPConfig de una web, sin enviar ni bloquear")
    mode.add_argument("--install-local-guard", action="store_true", help="Instalar ACL hash de sendmail/postdrop y recargar Postfix")
    mode.add_argument("--uninstall-local-guard", action="store_true", help="Restaurar authorized_submit_users; requiere webs desbloqueadas")
    mode.add_argument("--install-monit", action="store_true", help="Instalar Spam_Status, validar y reiniciar Monit")
    mode.add_argument("--uninstall-monit", action="store_true", help="Retirar solo el monitor Spam_Status y su comprobador")
    mode.add_argument("--install-postfix-guard", action="store_true", help="Instalar/migrar el bloqueo por SASL/destinatario; validar y reiniciar Postfix")
    mode.add_argument("--uninstall-postfix-guard", action="store_true", help="Restaurar restricciones anteriores; requiere no tener cuentas bloqueadas")
    operations = {
        "list-held": "Listar los mensajes que el monitor tiene registrados como HOLD de la cuenta",
        "release-held": "Liberar/reencolar retenciones propias; conservar bloqueo e incidente",
        "delete-held": "Borrar irreversiblemente las retenciones propias verificadas",
        "unblock": "Retirar el bloqueo externo; no libera HOLD ni reconoce el incidente",
        "ack": "Reconocer el incidente; no desbloquea ni libera mensajes",
        "purge-user-queue": "Borrar salida externa verificada en active/deferred/hold; no bloquea nuevos envíos",
        "resolve-user": "Resolver falso positivo: cancelar acciones, liberar HOLD propio, reconocer y desbloquear",
        "block-user": "Suspender nuevos destinatarios externos de la cuenta; requiere guard instalado",
    }
    for option, explanation in operations.items():
        mode.add_argument("--" + option, metavar="CUENTA", help=explanation)
    args = p.parse_args()
    if args.sender is not None and not args.inspect_local_user:
        p.error("--sender requiere --inspect-local-user")
    selected = any(getattr(args, k) for k in ("inspect_local_user", "install_local_guard", "uninstall_local_guard", "run", "daemon", "upgrade", "prepare_upgrade", "install_service", "uninstall_service", "replay", "status", "doctor", "install_monit", "uninstall_monit", "install_postfix_guard",
                   "uninstall_postfix_guard", "list_held", "release_held", "delete_held", "unblock", "ack", "set_limits", "configure_postfix", "purge_user_queue", "resolve_user", "block_user", "set_custom_limit", "set_ignore"))
    if not selected:
        if args.max_sent is not None or args.time_period is not None:
            args.set_limits = True
        else:
            p.error("Seleccione una operación; consulte --help")
    if (args.yes or args.skip_postfix or args.outbound_delay is not None or args.destination_delay is not None or args.postfix_client_limit is not None) and not (args.set_limits or args.configure_postfix):
        p.error("Las opciones administrativas de Postfix requieren --set-limits o --configure-postfix")
    if args.upgrade_lock_fd is not None and not args.prepare_upgrade:
        p.error("Descriptor interno de actualización fuera de preparación")
    if args.set_custom_limit or args.set_ignore:
        if args.max_sent is not None or args.time_period is not None or args.state_dir or args.config_fd is not None or args.from_start:
            p.error("Los límites/excepciones por cuenta usan --config y no admiten límites generales, --state-dir ni --from-start")
        from postfix_settings import account_address, account_limit
        try:
            if args.set_custom_limit:
                account, maximum, period = args.set_custom_limit
                args.set_custom_limit = (account_address(account), account_limit(maximum), account_limit(period))
            else:
                args.set_ignore = account_address(args.set_ignore)
        except ValueError as exc:
            p.error(str(exc))
    return p, args


def _main(parser, args):
    os.umask(0o077)
    if args.run and os.environ.get("OUTBOUND_SPAM_SERVICE") != "1":
        raise MonitorError("La ejecución operativa requiere el servicio --daemon")
    base = Path(os.environ.get("OUTBOUND_SPAM_BASE", Path(__file__).resolve().parent.parent))
    code_base = Path(os.environ.get("OUTBOUND_SPAM_CODE", base))
    config_warning = None
    def parse_profile(data):
        try:
            return Config.from_bytes(data)
        except MonitorError as exc:
            raise ValueError(str(exc)) from exc
    if args.config_fd is not None:
        config = Config.from_fd(args.config_fd)
    else:
        cache = private_directory(args.state_dir) / "outbound-spam.conf.last-good.json" if args.replay and args.state_dir else None
        config, config_warning = runtime.load_config(args.config_path, code_base, parse_profile, atomic_write, cache_path=cache)
    args.config_path = str(Path(args.config_path).absolute())
    if config_warning and not args.replay:
        runtime.audit("configuracion_advertencia", detalle=config_warning)
    if config_warning and any((args.set_limits, args.configure_postfix, args.install_service, args.install_monit, args.install_postfix_guard, args.install_local_guard, args.set_custom_limit, args.set_ignore)):
        raise MonitorError(config_warning + "; corrija el archivo antes de modificar la instalación")
    for option, key in ((args.max_sent, "max_sent"), (args.time_period, "time_period")):
        if option is not None:
            if option <= 0:
                parser.error(key + " debe ser positivo")
            config.v[key] = option
    if config.max_period + config["max_action_age"] > config["retention_days"] * 86400:
        parser.error("La retención no cubre el periodo solicitado")
    if args.outbound_delay is not None:
        if not re.fullmatch(r"auto|\d+[smhdw]?", args.outbound_delay):
            parser.error("--outbound-delay inválido")
        config.v["postfix_outbound_delay"] = args.outbound_delay
    if args.postfix_client_limit is not None:
        if args.postfix_client_limit < 0:
            parser.error("--postfix-client-limit debe ser >=0")
        config.v["postfix_client_message_limit"] = args.postfix_client_limit
    if args.destination_delay is not None:
        if not re.fullmatch(r"\d+[smhdw]?", args.destination_delay):
            parser.error("--destination-delay inválido")
        config.v["postfix_destination_delay"] = args.destination_delay
    if args.lock_wait is not None and args.lock_wait < 0:
        parser.error("--lock-wait debe ser >= 0")
    if args.now is not None and not args.replay:
        parser.error("--now solo es válido con --replay")
    if args.local_domain and not args.replay:
        parser.error("--local-domain solo es válido con --replay")
    if args.replay:
        if not args.state_dir or Path(args.state_dir).resolve() == Path(config["state_dir"]).resolve():
            parser.error("--replay requiere --state-dir distinto del estado operativo")
        config.v["log_file"] = str(Path(args.replay).resolve())
        config.v["routing_mode"] = "static"
        config.v["local_domains"].update(x.casefold() for x in args.local_domain)
    if args.state_dir:
        if os.environ.get("OUTBOUND_SPAM_SERVICE") == "1" and Path(args.state_dir).resolve() != Path(config["state_dir"]).resolve():
            config_warning = "La unidad fija state_dir; reinstale el servicio para cambiar la ruta de estado"
        config.v["state_dir"] = str(Path(args.state_dir).resolve())
    now = args.now if args.now is not None else time.time()
    commands = Commands(config)
    if args.inspect_local_user:
        import pwd
        require_root()
        try:
            uid = int(args.inspect_local_user) if args.inspect_local_user.isdigit() else pwd.getpwnam(args.inspect_local_user).pw_uid
        except KeyError:
            raise MonitorError("Usuario UNIX no encontrado")
        identity = unix_identity(uid)
        identity["domains"] = Websites(config, lambda sql: mysql_query(config, commands, sql)).domains(identity["user"])
        identity["account"] = "local:" + str(uid)
        identity["server_id"] = config["ispconfig_server_id"]
        if args.sender is not None:
            if args.sender:
                config.check_mailbox(args.sender)
            identity["sender"] = args.sender
            domain = mail_domain(args.sender)
            identity["foreign"] = None if identity["domains"] is None else bool(domain and not domain_belongs(domain, identity["domains"]))
        print(json.dumps(identity, ensure_ascii=False, indent=2))
        return 0
    if args.doctor:
        dependencies = ["postconf", "postmap", "postcat", "postqueue", "postsuper", "postfix"]
        dependencies += ["sendmail"] if config["notify"] and config["alert_transport"] == "sendmail" else []
        dependencies += ["mysql", "openssl", "doveadm"] if config["reset_password"] else []
        if config["block_smtp"] and config["routing_mode"] == "auto" and not config["local_domains"]:
            dependencies.append("mysql")
        if config["monitor_local"]:
            dependencies.append("mysql")
        for name in dependencies:
            if not os.access(config[name + "_bin"], os.X_OK):
                raise MonitorError("No se encuentra el ejecutable " + name)
        if config["block_smtp"] and "sqlite" not in commands.pc("-m").splitlines():
            raise MonitorError("Instale postfix-sqlite para block_smtp=true")
        Router(config, commands).load()
        if config["block_smtp"]:
            Guard(config, commands).verify()
        if config["monitor_local"]:
            Websites(config, lambda sql: mysql_query(config, commands, sql)).load()
            if config["block_local"]:
                LocalGuard(config, commands, atomic_write).verify()
        print("Configuración válida; Python %s; SQLite %s; Postfix %s" % (sys.version.split()[0], sqlite3.sqlite_version, commands.pc("-h", "mail_version")))
        return 0
    if not args.replay:
        require_root()
    directory = private_directory(config["state_dir"])
    if args.upgrade:
        if os.environ.get("OUTBOUND_SPAM_SERVICE") == "1":
            raise MonitorError("Ejecute --upgrade desde una sesión administrativa, fuera del servicio")
        from distribution import upgrade
        long_config = object.__new__(Config)
        long_config.v = dict(config.v, command_timeout=max(660, config["command_timeout"]))
        print(json.dumps(upgrade(base, VERSION, args.config_path, config, Commands(long_config)), ensure_ascii=False, indent=2))
        return 0
    if args.daemon:
        return runtime.daemon_loop(config, base, args.config_path, atomic_write)
    if args.install_service:
        print(runtime.install_service(config, commands, base, args.config_path, atomic_write))
        return 0
    if args.uninstall_service:
        print(runtime.uninstall_service(config, commands))
        return 0
    if args.install_monit:
        print(install_monit(config, commands, code_base))
        return 0
    if args.uninstall_monit:
        print(uninstall_monit(config, commands))
        return 0
    private_file(directory / "lock")
    if args.upgrade_lock_fd is not None:
        inherited = os.fstat(args.upgrade_lock_fd)
        expected = os.stat(directory / "lock")
        if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
            raise MonitorError("Descriptor de actualización no corresponde al estado")
        lock_stream = os.fdopen(os.dup(args.upgrade_lock_fd), "a")
    else:
        lock_stream = open(directory / "lock", "a")
    with lock_stream as lock:
        deadline = time.monotonic() + (args.lock_wait if args.lock_wait is not None else (0 if args.run else config["lock_wait"]))
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    if args.run and args.lock_wait is None:
                        if not args.quiet:
                            print("Ya hay una ejecución en curso")
                        return 0
                    raise MonitorError("Tiempo agotado esperando la operación activa")
                time.sleep(0.1)
        current = runtime.data_root() / "current"
        if current.is_symlink() and not args.prepare_upgrade and code_base.resolve() != current.resolve():
            raise MonitorError("La aplicación cambió mientras esperaba; vuelva a ejecutar el comando")
        if args.set_custom_limit or args.set_ignore:
            from postfix_settings import configure_account
            account = args.set_custom_limit[0] if args.set_custom_limit else args.set_ignore
            limit = args.set_custom_limit[1:] if args.set_custom_limit else None
            result = configure_account(args.config_path, code_base, parse_profile, atomic_write,
                                       account=account, limit=limit, state_dir=directory)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.status:
            status = json.loads((directory / "status.json").read_text())
            age = now - status["last_success"]
            if age < -60 or age > status["stale_after"]:
                status["code"] = 2
                status["message"] = "Spam Status: ERROR: analizador atrasado o reloj incoherente"
            print(json.dumps(runtime.readable_dates(status), ensure_ascii=False, indent=2))
            return status["code"]
        recovery = None if args.replay else Recovery(directory, config, atomic_write)
        state_valid = True
        try:
            store = Store(directory / "state.sqlite3") if recovery is None else recovery.open(directory / "state.sqlite3", Store)
        except Exception as exc:
            atomic_write(directory / "status.json", json.dumps({"last_success": 0, "stale_after": config["stale_after"], "code": 2,
                         "message": "Spam Status: ERROR al abrir estado: " + str(exc)}))
            raise
        try:
            stored_mode = store.get("mode")
            desired_mode = "offline" if args.replay else "live"
            if stored_mode is not None and stored_mode != desired_mode:
                raise MonitorError("No se puede mezclar estado offline y operativo")
            store.set("mode", desired_mode)
            store.db.commit()
            if args.prepare_upgrade:
                store.persist_critical()
                if recovery:
                    recovery.backup(store, force=True)
                if config_warning:
                    raise MonitorError(config_warning + "; corrija la configuración antes de actualizar")
                original_config = runtime.stable_bytes(args.config_path)
                try:
                    completion = runtime.migrate_config(args.config_path, code_base, parse_profile, atomic_write)
                    if config["monitor_local"] and config["block_local"]:
                        LocalGuard(config, commands, atomic_write).install(local_block_users(store, validate=True))
                        store.set("local_guard_installed", True)
                        store.persist_critical()
                    if config["monitor_local"] and not store.get("local_monitor_started"):
                        store.set("local_monitor_started", time.time())
                        store.set("warm_until", max(store.get("warm_until", 0), time.time() + config.max_period))
                        store.persist_critical()
                    from distribution import legacy_prepare
                    legacy_prepare(code_base, args.config_path)
                except Exception:
                    runtime.rollback_config_completion(args.config_path, original_config, code_base, parse_profile, atomic_write)
                    raise
                print(json.dumps({"version": VERSION, "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
                                  "prepared": True, "config_variables_added": completion["added"],
                                  "config_completion_backup": completion["backup"]}))
                return 0
            if recovery is not None:
                repair_guard(store, config, commands, recovery)
                repair_local_guard(store, config, commands)
            if args.set_limits or args.configure_postfix:
                from postfix_settings import configure
                result = configure(config, commands, args.config_path, atomic_write,
                                   persist=True, yes=args.yes, skip_postfix=args.skip_postfix)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.install_postfix_guard:
                print(install_guard(config, commands, store))
                if config["monitor_local"]:
                    LocalGuard(config, commands, atomic_write).install(local_block_users(store, validate=True))
                    store.set("local_guard_installed", True)
                    store.persist_critical()
            elif args.install_local_guard:
                changed = LocalGuard(config, commands, atomic_write).install(local_block_users(store, validate=True))
                store.set("local_guard_installed", True)
                store.persist_critical()
                print("ACL local instalada; Postfix recargado" if changed else "ACL local ya instalada")
            elif args.uninstall_local_guard:
                if local_block_users(store):
                    raise MonitorError("Desbloquee las webs antes de retirar la ACL local")
                LocalGuard(config, commands, atomic_write).uninstall()
                store.set("local_guard_installed", False)
                store.persist_critical()
                print("ACL local retirada; Postfix recargado")
            elif args.uninstall_postfix_guard:
                print(uninstall_guard(config, commands, store))
            elif not (args.run or args.replay):
                print(manage(store, config, commands, args, now))
                old = store.get("current", {})
                summary = summarize(store, config, now, old, coverage_issues(store, config, now))
                # No simular una ejecución correcta del analizador al administrar un incidente.
                summary["last_success"] = store.get("last_success", 0)
                summary["last_success_local"] = runtime.human_timestamp(summary["last_success"]) if summary["last_success"] else None
                summary["analysis_at"] = store.get("analysis_at", summary["last_success"])
                summary["analysis_at_local"] = runtime.human_timestamp(summary["analysis_at"]) if summary["analysis_at"] else None
                atomic_write(directory / "status.json", json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                started = time.monotonic()
                log_parser = Parser(store, config, now, realtime=not bool(args.replay))
                reader = LogReader(store, config, log_parser, now)
                try:
                    with store.db:
                        first = reader.run(from_start=args.from_start or bool(args.replay) or store.get("recovery_from_start", False))
                        store.set("recovery_from_start", False)
                        if first and not args.replay and not store.get("warm_until"):
                            oldest = log_parser.first_time if log_parser.first_time is not None else now
                            if oldest > now - config.max_period:
                                store.set("warm_until", oldest + config.max_period)
                        router = Router(config, commands)
                        issues = resolve_local_identities(store, config, commands, now, offline=bool(args.replay))
                        queue = None if args.replay else Queue(config, commands, store)
                        issues.extend(resolve_recipients(store, config, router, queue, bootstrap=first and not args.replay))
                        issues.extend(reader.notes)
                        if config_warning:
                            issues.append(config_warning)
                        if not args.replay and config["monitor_local"] and config["block_local"]:
                            try:
                                LocalGuard(config, commands, atomic_write).verify()
                            except MonitorError as exc:
                                issues.append(str(exc))
                        if recovery and recovery.note:
                            issues.append(recovery.note)
                        if not args.replay:
                            now = time.time()
                        current, opened = evaluate(store, config, now, offline=bool(args.replay))
                        store.set("current", current)
                        store.set("analysis_at", now)
                        if reader.backlog:
                            issues.append("pendiente de leer log: se alcanzó max_read_bytes")
                        store.set("last_success", now)
                        store.set("reader_notes", reader.notes)
                    if not args.replay:
                        store.persist_critical()
                        reconcile_queue_ops(store, config, commands)
                        Actions(store, config, commands, now).process()
                        reconcile_held(store, config, commands)
                    issues.extend(coverage_issues(store, config, now))
                    summary = summarize(store, config, now, current, list(dict.fromkeys(issues)))
                    summary["analysis_at"] = now
                    if not args.replay:
                        summary["last_success"] = time.time()
                        summary["last_success_local"] = runtime.human_timestamp(summary["last_success"])
                        store.set("last_success", summary["last_success"])
                        store.db.commit()
                    summary["metrics"] = {"bytes_read": reader.bytes, "lines_read": log_parser.lines,
                                          "duration_seconds": round(time.monotonic() - started, 4), "new_incidents": opened}
                    summary["offline"] = bool(args.replay)
                    atomic_write(directory / "status.json", json.dumps(summary, ensure_ascii=False, indent=2))
                    if not args.replay:
                        runtime.audit("estado", estado=summary["message"], incidencias=summary["issues"],
                                      nuevos_incidentes=opened, bytes_leidos=reader.bytes,
                                      duracion=summary["metrics"]["duration_seconds"])
                        for incident in summary["incidents"]:
                            details = dict(incident, ajustes={key: config[key] for key in ("block_smtp", "block_local", "hold_queue", "reset_password", "notify")})
                            key = "audit_incident_" + str(incident["id"])
                            if store.get(key) != details:
                                runtime.audit("incidencia", **details)
                                store.set(key, details)
                        store.db.commit()
                    # Purgar solo mensajes terminados: conservar correlación de colas antiguas/HOLD.
                    with store.db:
                        store.db.execute("DELETE FROM messages WHERE removed=1 AND created<?", (now - config["retention_days"] * 86400,))
                    if not args.quiet:
                        print(json.dumps(runtime.readable_dates(summary), ensure_ascii=False, indent=2))
                    elif summary["code"] == 2:
                        print(summary["message"], file=sys.stderr)
                    if recovery:
                        recovery.backup(store)
                    # Un incidente no hace fallar el trabajador; los errores operativos sí.
                    return 2 if summary["code"] == 2 else 0
                except Exception as e:
                    with contextlib.suppress(sqlite3.Error):
                        store.db.rollback()
                    if corruption(e):
                        with contextlib.suppress(sqlite3.Error):
                            store.set("guard_checked", 0)
                            store.db.commit()
                    try:
                        last_success = store.get("last_success", 0)
                    except sqlite3.Error:
                        last_success = 0
                    summary = {"version": VERSION, "last_success": last_success, "stale_after": config["stale_after"],
                               "code": 2, "message": "Spam Status: ERROR: " + str(e)}
                    atomic_write(directory / "status.json", json.dumps(summary, ensure_ascii=False))
                    raise
        except sqlite3.Error as exc:
            state_valid = False
            atomic_write(directory / "status.json", json.dumps({"last_success": 0, "stale_after": config["stale_after"], "code": 2,
                         "message": "Spam Status: ERROR SQLite: " + str(exc)}))
            raise
        except Exception as exc:
            atomic_write(directory / "status.json", json.dumps({"last_success": store.get("last_success", 0), "stale_after": config["stale_after"],
                         "code": 2, "message": "Spam Status: ERROR: " + str(exc)}))
            raise
        finally:
            try:
                if recovery and state_valid and not recovery.failed:
                    store.persist_critical()
                    recovery.backup(store)
            finally:
                store.db.close()
    return 0


def main():
    parser, args = cli()
    # Las reproducciones offline no escriben en el log operativo del servidor.
    if args.replay:
        return _main(parser, args)
    os.close(runtime.open_log())
    operation = next(("--" + key.replace("_", "-") for key, value in vars(args).items()
                      if value and key in ("run", "daemon", "upgrade", "status", "doctor", "prepare_upgrade",
                      "set_limits", "configure_postfix", "install_service", "uninstall_service", "install_monit",
                       "uninstall_monit", "install_postfix_guard", "inspect_local_user", "install_local_guard", "uninstall_local_guard",
                      "uninstall_postfix_guard", "list_held", "release_held", "delete_held", "unblock", "ack",
                      "purge_user_queue", "resolve_user", "block_user", "set_custom_limit", "set_ignore")), "consulta")
    target = next((getattr(args, key) for key in ("list_held", "release_held", "delete_held", "unblock", "ack",
                   "purge_user_queue", "resolve_user", "block_user") if getattr(args, key, None)), None)
    if args.set_custom_limit or args.set_ignore:
        target = args.set_custom_limit[0] if args.set_custom_limit else args.set_ignore
    service = os.environ.get("OUTBOUND_SPAM_SERVICE") == "1" or args.daemon
    if not service:
        parameters = {key: getattr(args, key) for key in ("max_sent", "time_period", "outbound_delay", "destination_delay",
                      "postfix_client_limit", "skip_postfix", "from_start") if getattr(args, key) is not None}
        if args.set_custom_limit:
            parameters.update(maximum=args.set_custom_limit[1], period=args.set_custom_limit[2])
        runtime.audit("operacion_solicitada", operacion=operation, cuenta=target, parametros=parameters)
    class Output:
        def __init__(self, original):
            self.original, self.text = original, ""
        def write(self, text):
            self.text += text[:max(0, 8192 - len(self.text))]
            return self.original.write(text)
        def flush(self):
            self.original.flush()
    output = Output(sys.stdout)
    try:
        with contextlib.redirect_stdout(output):
            result = _main(parser, args)
        if not service:
            runtime.audit("operacion_finalizada", operacion=operation, cuenta=target, codigo=result,
                          resultado=output.text.strip())
        return result
    except (Exception, SystemExit) as exc:
        runtime.audit("error", mode="servicio" if service else "manual", operacion=operation, cuenta=target, error=str(exc))
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MonitorError, OSError, ValueError, sqlite3.Error, KeyError) as exc:
        print("outbound-spam: " + str(exc), file=sys.stderr)
        sys.exit(2)

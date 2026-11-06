"""API de solo lectura y recursos locales del dashboard, independiente del prefijo del proxy."""
import contextlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

import analytics

SLOTS = threading.BoundedSemaphore(2)
ASSETS = {"": ("index.html", "text/html"), "assets/app.js": ("app.js", "application/javascript"),
          "assets/style.css": ("style.css", "text/css")}


class Filters:
    def __init__(self, query):
        now = time.time()
        self.end = float(query.get("to", [int(now // 60 + 1) * 60])[0])
        self.start = float(query.get("from", [int((now - 86400) // 60) * 60])[0])
        import math
        if not all(math.isfinite(v) for v in (self.start, self.end)) or not (
                now - analytics.RETENTION - 60 <= self.start < self.end <= now + 120):
            raise ValueError("Periodo inválido: seleccione un intervalo dentro de los últimos 60 días")
        # Los agregados son por minuto; mostrar explícitamente el intervalo efectivo.
        self.start = int(self.start // 60) * 60
        self.end = int((self.end + 59) // 60) * 60
        bucket = query.get("bucket", ["auto"])[0]
        duration = self.end - self.start
        if bucket == "auto":
            bucket = "minute" if duration <= 6 * 3600 else "hour" if duration <= 14 * 86400 else "day"
        if bucket not in ("minute", "hour", "day"):
            raise ValueError("Agrupación no válida")
        self.step = {"minute": 60, "hour": 3600, "day": 86400}[bucket]
        if duration / self.step > 1500:
            raise ValueError("Demasiados puntos: agrupe por horas o días")
        self.bucket = bucket
        self.accounts = list(dict.fromkeys(query.get("user", [])))
        if len(self.accounts) > 200 or any(len(a) > 320 for a in self.accounts):
            raise ValueError("Seleccione como máximo 200 usuarios por consulta")
        self.ignored = query.get("ignored", ["all"])[0]
        self.origin = query.get("origin", ["all"])[0]
        self.destination = query.get("destination", ["all"])[0]
        if self.ignored not in ("all", "exclude", "only") or self.origin not in ("all", "smtp", "local") or self.destination not in ("all", "local", "remote", "unknown"):
            raise ValueError("Filtro no válido")
        self.offset = int(query.get("offset", [0])[0])
        if not 0 <= self.offset <= 1000000:
            raise ValueError("Paginación no válida")

    def users(self):
        clauses, args = ["1=1"], []
        if self.accounts:
            clauses.append("u.account IN (%s)" % ",".join("?" for _ in self.accounts))
            args.extend(self.accounts)
        if self.ignored != "all":
            clauses.append("u.ignored=?")
            args.append(int(self.ignored == "only"))
        if self.origin != "all":
            clauses.append("u.origin=?")
            args.append(self.origin)
        return " AND ".join(clauses), args

    def where(self, alias="b", time_column="minute", destination=True):
        sql, args = self.users()
        sql += " AND %s.%s>=? AND %s.%s<?" % (alias, time_column, alias, time_column)
        args += [self.start, self.end]
        if destination and self.destination != "all":
            sql += " AND %s.destination=?" % alias
            args.append(self.destination)
        return sql, args


def overview(db, filters):
    f = filters
    where, args = f.where()
    graph = [dict(r) for r in db.execute(
        "SELECT CAST(b.minute/? AS INTEGER)*? AS at,b.destination,SUM(b.messages) messages,SUM(b.recipients) recipients "
        "FROM buckets b JOIN users u ON u.account=b.account WHERE " + where + " GROUP BY at,b.destination ORDER BY at",
        [f.step, f.step] + args)]
    totals = dict(db.execute("SELECT COALESCE(SUM(b.recipients),0) recipients,COALESCE(SUM(b.sent),0) sent,"
                             "COALESCE(SUM(b.deferred),0) deferred,COALESCE(SUM(b.bounced),0) bounced "
                             "FROM buckets b JOIN users u ON u.account=b.account WHERE " + where, args).fetchone())
    user_where, user_args = f.users()
    msg_sql = "SELECT COUNT(*) FROM messages m JOIN users u ON u.account=m.account WHERE " + user_where
    msg_sql += " AND m.accepted>=? AND m.accepted<? AND m.rejected=0"
    msg_args = user_args + [f.start, f.end]
    if f.destination != "all":
        msg_sql += " AND EXISTS(SELECT 1 FROM recipients r WHERE r.mid=m.id AND r.destination=?)"
        msg_args.append(f.destination)
    totals["messages"] = db.execute(msg_sql, msg_args).fetchone()[0]
    table = [dict(r) for r in db.execute(
        "SELECT u.account,u.label,u.origin,u.ignored,b.destination,SUM(b.messages) messages,SUM(b.recipients) recipients,"
        "SUM(b.sent) sent,SUM(b.deferred) deferred,SUM(b.bounced) bounced,MAX(b.minute) last_seen "
        "FROM buckets b JOIN users u ON u.account=b.account WHERE " + where +
        " GROUP BY u.account,b.destination HAVING SUM(b.messages)>0 ORDER BY recipients DESC,u.account,b.destination LIMIT 101 OFFSET ?",
        args + [f.offset])]
    event_where, event_args = f.where("e", "at")
    event_graph = [dict(r) for r in db.execute(
        "SELECT CAST(e.at/? AS INTEGER)*? at,e.kind,e.destination,COUNT(*) n FROM events e "
        "JOIN users u ON u.account=e.account WHERE " + event_where +
        " AND e.kind IN ('sent','rejected','held') GROUP BY 1,e.kind,e.destination ORDER BY 1", [f.step, f.step] + event_args)]
    # Bloqueos no tienen destinatario; se muestran también al seleccionar local/remoto.
    timeline_where, timeline_args = f.where("e", "at", destination=False)
    if f.destination != "all":
        timeline_where += " AND (e.kind IN ('blocked','unblocked') OR e.destination=?)"
        timeline_args.append(f.destination)
    timeline = [dict(r) for r in db.execute(
        "SELECT e.at,e.account,u.label,e.kind,e.qid,e.detail,e.destination FROM events e JOIN users u ON u.account=e.account WHERE " +
        timeline_where + " AND e.kind!='sent' ORDER BY e.at DESC,e.rowid DESC LIMIT 100", timeline_args)]
    totals["rejected"] = sum(r["n"] for r in event_graph if r["kind"] == "rejected")
    totals["held"] = sum(r["n"] for r in event_graph if r["kind"] == "held")
    active = [dict(r) for r in db.execute("SELECT a.kind,COUNT(*) n FROM active a JOIN users u ON u.account=a.account WHERE " +
                                         user_where + " GROUP BY a.kind", user_args)]
    # Mensajes cuyo sobre no está completo: no asignarlos arbitrariamente a local/remoto.
    incomplete = db.execute("SELECT COUNT(*) FROM messages m JOIN users u ON u.account=m.account WHERE " + user_where +
                            " AND m.accepted>=? AND m.accepted<? AND m.rejected=0 AND (m.expected IS NULL OR "
                            "m.expected>(SELECT COUNT(*) FROM recipients r WHERE r.mid=m.id))", user_args + [f.start, f.end]).fetchone()[0]
    return {"from": f.start, "to": f.end, "step": f.step, "bucket": f.bucket,
            "graph": graph, "event_graph": event_graph, "totals": totals, "active": active,
            "table": table[:100], "has_more": len(table) > 100, "offset": f.offset,
            "events": timeline, "incomplete_messages": incomplete}


def query(directory, endpoint, params):
    path = Path(directory) / "analytics.sqlite3"
    with contextlib.closing(analytics.connect(path, readonly=True)) as db:
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
        db.execute("BEGIN")
        if endpoint == "users":
            search = params.get("search", [""])[0][:200]
            offset = int(params.get("offset", [0])[0])
            if not 0 <= offset <= 1000000:
                raise ValueError("Paginación no válida")
            rows = [dict(r) for r in db.execute(
                "SELECT account,label,origin,ignored,domains,last_seen FROM users WHERE "
                "instr(lower(account||' '||label||' '||domains),lower(?))>0 ORDER BY label,account LIMIT 101 OFFSET ?", (search, offset))]
            result = {"users": rows[:100], "has_more": len(rows) > 100, "offset": offset}
        elif endpoint == "overview":
            result = overview(db, Filters(params))
        else:
            raise LookupError("Ruta no encontrada")
        result["history"] = {k: analytics.value(db, k) for k in (
            "updated_at", "analysed_until", "started_at", "coverage", "dropped", "loss_detected_at", "checked_at", "backup_at", "restored_at")}
        try:
            result["worker"] = json.loads((Path(directory) / "analytics-status.json").read_text())
        except (OSError, ValueError):
            result["worker"] = {"ok": False, "error": "Trabajador aún no disponible"}
        return result


def serve(handler, directory):
    """Devuelve False cuando la ruta no pertenece al dashboard."""
    parsed = urlsplit(handler.path)
    if parsed.path == "/dashboard":
        handler.send_response(308)
        handler.send_header("Location", "dashboard/")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True
    if not parsed.path.startswith("/dashboard/"):
        return False
    relative = parsed.path[len("/dashboard/"):]
    status, kind = 200, "application/json"
    try:
        if relative in ASSETS:
            filename, kind = ASSETS[relative]
            from runtime import dashboard_asset
            body = dashboard_asset(filename)
        elif relative.startswith("api/"):
            if len(parsed.query) > 16384:
                raise ValueError("Consulta demasiado larga")
            if not SLOTS.acquire(blocking=False):
                raise sqlite3.OperationalError("Dashboard ocupado; reintente en unos segundos")
            try:
                body = json.dumps(query(directory, relative[4:], parse_qs(parsed.query, max_num_fields=220)),
                                  ensure_ascii=False, allow_nan=False).encode("utf-8")
            finally:
                SLOTS.release()
        else:
            raise LookupError("Ruta no encontrada")
    except LookupError as exc:
        status, body = 404, json.dumps({"error": str(exc)}).encode()
    except (ValueError, OverflowError) as exc:
        status, body = 400, json.dumps({"error": str(exc)}).encode()
    except (sqlite3.Error, OSError) as exc:
        status, body = 503, json.dumps({"error": "Histórico no disponible: " + str(exc)}).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", kind + "; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store" if kind == "application/json" else "no-cache")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    handler.end_headers()
    handler.wfile.write(body)
    return True

"""Pruebas públicas del histórico: cursor, reintentos, filtros, API y recuperación."""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import analytics as a
import dashboard
import outbound_spam as m
import runtime

BASE, _ = runtime.validate_config(b"notify=false\nmonitor_local=false\nrouting_mode=static\nlocal_domains=(local.test)\n", ROOT, m.Config.from_bytes)


def line(at, service, qid, text):
    return "%s host postfix/%s[123]: %s: %s\n" % (dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(), service, qid, text)


def message(at, qid="ABC123", account="user@local.test", destination="remote.test", status="sent"):
    return [line(at, "smtpd", qid, "client=host[192.0.2.1], sasl_username=" + account),
            line(at, "qmgr", qid, "from=<sender@local.test>, size=100, nrcpt=1 (queue active)"),
            line(at, "smtp" if destination != "local.test" else "lmtp", qid,
                 "to=<one@%s>, relay=mx[192.0.2.2], status=%s (ok)" % (destination, status)),
            line(at, "qmgr", qid, "removed")]


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = int(time.time() // 60) * 60 - 120
        self.config = m.Config(dict(BASE.v, state_dir=str(self.root), ignore=["ignored@local.test"], custom_limits={}))
        self.store = m.Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.db.close)
        self.capture = self.store.analytics = a.Capture(self.store, self.config)
        self.history = a.History(self.root / "analytics.sqlite3")
        self.addCleanup(self.history.db.close)

    def ingest(self, lines, commit=True):
        parser = m.Parser(self.store, self.config, self.now + 600)
        self.store.db.execute("BEGIN")
        for text in lines:
            parser.feed(text)
        self.capture.finish(self.now + 30)
        if commit:
            self.store.db.commit()
            a.drain(self.root, self.history)
        else:
            self.store.db.rollback()

    def overview(self, **params):
        query = {"from": [str(self.now - 60)], "to": [str(self.now + 180)]}
        query.update({k: v if isinstance(v, list) else [str(v)] for k, v in params.items()})
        return dashboard.overview(self.history.db, dashboard.Filters(query))

    def test_ignored_captured_but_not_monitored_and_filterable(self):
        self.ingest(message(self.now, account="ignored@local.test") + message(self.now, "DEF456"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(self.overview()["totals"]["recipients"], 2)
        self.assertEqual(self.overview(ignored="only")["totals"]["recipients"], 1)
        self.assertEqual(self.overview(ignored="exclude")["totals"]["recipients"], 1)
        self.assertEqual(self.overview(user=["ignored@local.test"])["totals"]["messages"], 1)

    def test_cursor_rollback_does_not_publish_events(self):
        self.ingest(message(self.now), commit=False)
        self.assertEqual(a.drain(self.root, self.history), 0)
        self.assertEqual(self.history.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.ingest(message(self.now))
        self.assertEqual(self.overview()["totals"]["messages"], 1)

    def test_applied_before_ack_and_log_replay_are_idempotent(self):
        parser = m.Parser(self.store, self.config, self.now)
        with self.store.db:
            for text in message(self.now):
                parser.feed(text)
            self.capture.finish(self.now)
        row = self.store.db.execute("SELECT * FROM stats_outbox").fetchone()
        self.assertTrue(self.history.apply(row["token"], row["payload"]))
        self.assertFalse(self.history.apply(row["token"], row["payload"]))
        a.drain(self.root, self.history)
        self.ingest(message(self.now))
        self.assertEqual(self.overview()["totals"]["recipients"], 1)
        self.assertEqual(sum(r["n"] for r in self.overview()["event_graph"] if r["kind"] == "sent"), 1)

    def test_deferred_then_sent_does_not_add_another_acceptance(self):
        self.ingest(message(self.now, status="deferred")[:-1])
        self.assertEqual(self.overview()["totals"]["deferred"], 1)
        self.ingest([line(self.now + 60, "qmgr", "ABC123", "from=<sender@local.test>, size=100, nrcpt=1 (queue active)"),
                     line(self.now + 60, "smtp", "ABC123", "to=<one@remote.test>, status=sent (ok)")])
        totals = self.overview()["totals"]
        self.assertEqual((totals["messages"], totals["recipients"], totals["sent"], totals["deferred"]), (1, 1, 1, 0))

    def test_queue_reuse_and_mixed_recipients_have_distinct_counts(self):
        lines = message(self.now)
        lines.insert(-1, line(self.now, "lmtp", "ABC123", "to=<two@local.test>, status=sent (ok)"))
        self.ingest(lines + message(self.now + 60))
        totals = self.overview()["totals"]
        self.assertEqual((totals["messages"], totals["recipients"]), (2, 3))
        self.assertEqual(self.overview(destination="local")["totals"]["messages"], 1)
        self.assertEqual(self.overview(destination="remote")["totals"]["messages"], 2)

    def test_local_users_and_rejects_are_recorded(self):
        self.ingest([line(self.now, "pickup", "PHP123", "uid=65000 from=<web@local.test>"),
                     line(self.now, "qmgr", "PHP123", "from=<web@local.test>, size=100, nrcpt=1 (queue active)"),
                     line(self.now, "lmtp", "PHP123", "to=<one@local.test>, status=sent (ok)"),
                     line(self.now, "smtpd", "NOQUEUE", "reject: RCPT from host: 451 blocked; from=<x@local.test> to=<x@remote.test>, sasl_username=user@local.test")])
        self.assertEqual(self.overview(origin="local")["totals"]["messages"], 1)
        self.assertEqual(self.overview()["totals"]["rejected"], 1)

    def test_users_are_not_limited_to_selected_period_and_search_is_literal(self):
        self.ingest(message(self.now - 86400))
        result = dashboard.query(self.root, "users", {"search": ["user@"]})
        self.assertEqual(len(result["users"]), 1)
        self.assertEqual(self.overview()["totals"]["messages"], 0)
        self.assertEqual(dashboard.query(self.root, "users", {"search": ["' OR 1=1 --"]})["users"], [])

    def test_pending_envelope_completes_without_delivery(self):
        self.ingest([message(self.now)[0]])
        with self.store.db:
            self.capture.add(["seed", self.now, "ABC123", self.now, "user@local.test", "sender@local.test"])
            self.capture.add(["recipient", self.now, "ABC123", self.now, self.now, "user@local.test", "one@remote.test", 1])
            self.capture.finish(self.now)
        a.drain(self.root, self.history)
        totals = self.overview()["totals"]
        self.assertEqual((totals["recipients"], totals["sent"]), (1, 0))

    def test_retention_backup_and_explicit_restore(self):
        self.ingest(message(self.now))
        self.history.backup(time.time())
        backup = next(self.root.glob("analytics-backup-*.sqlite3"))
        self.history.db.close()
        quarantine = a.restore(self.root, backup)
        self.assertTrue(Path(quarantine, "analytics.sqlite3").exists())
        restored = a.History(self.root / "analytics.sqlite3")
        self.addCleanup(restored.db.close)
        self.assertIsNotNone(a.value(restored.db, "restored_at"))
        restored.maintain(time.time() + 61 * 86400)
        self.assertEqual(restored.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(restored.db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_uncommitted_killed_writer_preserves_integrity(self):
        self.ingest(message(self.now))
        script = "import sqlite3,os; d=sqlite3.connect(%r); d.execute('BEGIN'); d.execute('DELETE FROM buckets'); os._exit(9)" % str(self.history.path)
        self.assertEqual(subprocess.run([sys.executable, "-c", script]).returncode, 9)
        self.assertEqual(self.overview()["totals"]["recipients"], 1)
        self.assertEqual(self.history.db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_corruption_automatically_restores_backup_without_affecting_monitor(self):
        self.ingest(message(self.now))
        self.history.backup(time.time())
        self.history.db.close()
        (self.root / "analytics.sqlite3").write_bytes(b"not a database" * 100)
        recovered = a.open_history(self.root / "analytics.sqlite3")
        self.addCleanup(recovered.db.close)
        self.assertIsNotNone(a.value(recovered.db, "recovery")["backup"])
        self.history = recovered
        self.ingest(message(self.now + 60, "NEW123"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
        self.assertEqual(self.overview()["totals"]["messages"], 2)

    def test_corruption_without_valid_backup_starts_new_history_and_monitor_continues(self):
        self.ingest(message(self.now))
        self.history.db.close()
        (self.root / "analytics.sqlite3").write_bytes(b"damaged sqlite" * 100)
        recovered = a.open_history(self.root / "analytics.sqlite3")
        self.addCleanup(recovered.db.close)
        self.assertIsNone(a.value(recovered.db, "recovery")["backup"])
        self.history = recovered
        self.ingest(message(self.now + 60, "NEW123"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
        self.assertEqual(self.overview()["totals"]["messages"], 1)

    def test_confirmation_cannot_erase_a_newer_change(self):
        self.ingest(message(self.now, status="deferred")[:-1])
        with self.store.db:
            self.capture.dirty.add(1)
            self.capture.finish(self.now)
        pending = self.history.sync(self.store.path)
        self.assertEqual(len(pending), 1)
        with self.store.db:
            self.capture.dirty.add(1)
            self.capture.finish(self.now + 1)
            self.store.db.executemany("DELETE FROM stats_dirty WHERE mid=? AND revision=?", pending)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM stats_dirty").fetchone()[0], 1)

    def test_late_auth_timestamp_adjustment_keeps_one_generation(self):
        self.ingest(message(self.now))
        with self.store.db:
            self.store.db.execute("UPDATE messages SET created=created+1 WHERE id=1")
            self.capture.dirty.add(1)
            self.capture.finish(self.now)
        a.drain(self.root, self.history)
        self.assertEqual(self.overview()["totals"]["messages"], 1)
        self.assertEqual(self.overview()["totals"]["recipients"], 1)

    def test_bad_latest_backup_falls_back_to_verified_older_backup(self):
        self.ingest(message(self.now))
        self.history.backup(time.time() - 60)
        broken = self.root / ("analytics-backup-%d.sqlite3" % time.time())
        broken.write_bytes(b"bad backup" * 100)
        broken.chmod(0o600)
        self.history.db.close()
        (self.root / "analytics.sqlite3").write_bytes(b"damaged" * 100)
        recovered = a.open_history(self.root / "analytics.sqlite3")
        self.addCleanup(recovered.db.close)
        self.assertNotEqual(a.value(recovered.db, "recovery")["backup"], broken.name)
        self.assertEqual(recovered.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_range_and_query_budget_validation(self):
        for params in ({"from": ["nan"]}, {"bucket": ["invalid"]}, {"from": [str(self.now - 30 * 86400)], "bucket": ["minute"]}, {"user": [str(i) for i in range(201)]}):
            with self.assertRaises(ValueError):
                dashboard.Filters(params)

    def test_http_resources_api_and_existing_root(self):
        self.ingest(message(self.now))
        m.atomic_write(self.root / "status.json", json.dumps({"code": 0, "last_success": time.time(), "stale_after": 180}))
        config = dict(self.config.v, status_http_port=0)
        server, thread = runtime.start_status_http(config, self.root)
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        url = "http://127.0.0.1:%d" % server.server_port
        with urlopen(url + "/") as response:
            self.assertEqual(json.load(response)["code"], 0)
        for path, content in (("/dashboard/", b"assets/app.js"), ("/dashboard/assets/app.js", b'"api/"')):
            with urlopen(url + path) as response:
                self.assertIn(content, response.read())
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        with urlopen(url + "/dashboard/api/users") as response:
            self.assertEqual(len(json.load(response)["users"]), 1)
        with self.assertRaises(HTTPError) as error:
            urlopen(url + "/dashboard/assets/../../VERSION")
        self.assertEqual(error.exception.code, 404)

    def test_worker_retains_last_active_batch_metrics_during_idle_cycles(self):
        parser = m.Parser(self.store, self.config, self.now + 600)
        with self.store.db:
            for text in message(self.now):
                parser.feed(text)
            self.capture.finish(self.now)
        self.history.db.close()
        process = subprocess.Popen([sys.executable, str(ROOT / "lib/analytics.py"), str(self.root)],
                                   env=dict(os.environ, OUTBOUND_SPAM_LOG=str(self.root / "audit.log")),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            status_path = self.root / "analytics-status.json"
            deadline = time.monotonic() + 15
            active = idle = None
            while time.monotonic() < deadline:
                self.assertIsNone(process.poll(), "El trabajador ha terminado inesperadamente")
                if status_path.exists():
                    status = json.loads(status_path.read_text())
                    self.assertTrue(status["ok"], status)
                    if status["processed_items"] > 0:
                        active = status
                    elif active:
                        idle = status
                        break
                time.sleep(0.1)
            self.assertIsNotNone(active, "No se observó el lote con cambios pendientes")
            self.assertIsNotNone(idle, "No se observó el sondeo posterior sin cambios")
            self.assertGreaterEqual(active["last_work_duration_seconds"], 0)
            self.assertGreaterEqual(active["last_work_cpu_seconds"], 0)
            self.assertEqual(active["processed_items"], active["last_work_items"])
            for key in ("last_work_at", "last_work_duration_seconds", "last_work_cpu_seconds", "last_work_items"):
                self.assertEqual(idle[key], active[key], key)
        finally:
            process.terminate()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()

"""Regresiones del estado de cola y su migración aditiva en 2.1.1."""
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release210 import Base, NOW, m, line, runtime


QID = "C2B832800D096"


class QueueStatusTests(Base):
    def setUp(self):
        super().setUp()
        self.c.v["time_period"] = 3600
        self.c.v["postcat_bin"] = "/usr/sbin/postcat"
        self.queue = m.Queue(self.c, m.Commands(self.c), self.s)
        self.feed(self.message())

    def message(self, at=NOW):
        return (line(at, "smtpd", QID, "client=host[192.0.2.1], sasl_username=user@local.test")
                + line(at, "qmgr", QID, "from=<user@local.test>, size=100, nrcpt=1 (queue active)"))

    def cycle(self, at, stderr=None, stdout=""):
        if stderr is None and not stdout:
            stderr = "fatal: open queue file %s: No such file or directory" % QID
        result = subprocess.CompletedProcess([], int(bool(stderr)), stdout, "/usr/sbin/postcat: " + stderr if stderr else "")
        with self.s.db, patch.object(m.subprocess, "run", return_value=result) as command:
            issues = m.resolve_recipients(self.s, self.c, m.Router(self.c, self.cmd), self.queue, now=at)
            issues.extend(m.coverage_issues(self.s, self.c, at))
            status = m.summarize(self.s, self.c, at, {}, issues)
        m.atomic_write(self.directory / "status.json", json.dumps(status))
        with patch.object(runtime.time, "time", return_value=at):
            result = runtime.http_status(self.directory, self.c["stale_after"])
        return result, command.call_count

    def check_status(self, result, code, issues):
        self.assertEqual(result["code"], code)
        self.assertEqual(result["http_metrics"]["code"], code)
        self.assertEqual(len(result["issues"]), issues)
        self.assertEqual(result["http_metrics"]["issues"], issues)

    def test_real_sequence_resolves_from_log_before_deadline(self):
        status, probes = self.cycle(NOW + 1)
        self.check_status(status, 0, 0)
        self.assertEqual(probes, 1)
        self.assertIn('"evento":"cola_consulta_pendiente"', (self.directory / "audit.log").read_text())
        self.feed(line(NOW + 20, "smtp", QID, "to=<target@remote.test>, relay=remote[192.0.2.2], status=sent (ok)")
                  + line(NOW + 21, "qmgr", QID, "removed"))
        status, probes = self.cycle(NOW + 63)
        self.check_status(status, 0, 0)
        self.assertEqual(probes, 0)
        self.assertIsNone(self.s.db.execute("SELECT mid FROM queue_missing").fetchone())
        self.assertEqual(self.s.db.execute("SELECT delivery FROM recipients").fetchone()[0], "sent")

    def test_persists_across_restart_and_escalates_at_240_seconds(self):
        self.check_status(self.cycle(NOW)[0], 0, 0)
        self.s.db.close()
        self.s = m.Store(Path(self.c["state_dir"]) / "state.sqlite3")
        self.queue.store = self.s
        self.check_status(self.cycle(NOW + 239)[0], 0, 0)
        self.assertEqual(self.s.db.execute("SELECT first_seen FROM queue_missing").fetchone()[0], NOW)
        status, probes = self.cycle(NOW + 240)
        self.check_status(status, 2, 2)
        self.assertEqual(probes, 1)
        self.assertIn("open queue file " + QID, status["issues"][0])
        self.assertIn("destinatarios/aceptación incompletos", status["issues"][1])
        self.assertEqual((self.directory / "audit.log").read_text().count("cola_consulta_pendiente"), 1)

    def test_qmgr_removal_with_incomplete_data_is_not_admin_removal(self):
        self.cycle(NOW)
        self.feed(line(NOW + 60, "qmgr", QID, "removed"))
        self.check_status(self.cycle(NOW + 61)[0], 0, 0)
        status, probes = self.cycle(NOW + 240)
        self.check_status(status, 2, 2)
        self.assertEqual(probes, 0)

    def test_postsuper_removal_closes_pending_and_preserves_spam_incident(self):
        self.cycle(NOW)
        self.s.db.execute("INSERT INTO incidents(account,opened,event_time,peak,maximum,period,snapshot) "
                          "VALUES (?,?,?,?,?,?,?)", ("user@local.test", NOW, NOW, 12, 3, 60, "[]"))
        self.check_status(self.cycle(NOW + 1)[0], 1, 0)
        parser = m.Parser(self.s, self.c, NOW + 60, realtime=True)
        parser.feed(line(NOW + 60, "qmgr", QID, "removed").strip())
        parser.feed("%s host postfix/postsuper[123]: %s: removed" %
                    (m.dt.datetime.fromtimestamp(NOW + 60).strftime("%b %d %H:%M:%S"), QID))
        status, probes = self.cycle(NOW + 240)
        self.check_status(status, 1, 0)
        self.assertEqual(probes, 0)
        self.assertEqual(status["http_metrics"]["incidents"], 1)
        message = self.s.db.execute("SELECT accepted,removed,admin_removed FROM messages").fetchone()
        self.assertEqual(tuple(message), (NOW, 1, NOW + 60))
        self.assertIsNone(self.s.db.execute("SELECT mid FROM queue_missing").fetchone())
        self.assertIsNone(self.s.db.execute("SELECT address FROM recipients").fetchone())
        self.assertIn('"evento":"cola_borrado_administrativo"', (self.directory / "audit.log").read_text())

    def test_bulk_summary_move_and_embedded_log_are_not_confirmation(self):
        self.cycle(NOW)
        self.feed(line(NOW + 60, "postsuper", "Deleted", "37 messages")
                  + line(NOW + 60, "postsuper", QID, "requeued")
                  + line(NOW + 60, "postsuper", QID, "placed on hold")
                  + line(NOW + 60, "postsuper", QID, "removed").replace("host postfix/", "host app[321]: postfix/"))
        self.assertIsNotNone(self.s.active(QID))
        self.check_status(self.cycle(NOW + 240)[0], 2, 2)

    def test_old_postsuper_record_does_not_close_reused_active_id(self):
        self.cycle(NOW)
        self.feed(self.message(NOW + 60) + line(NOW + 59, "instance/postsuper", QID, "removed"))
        self.assertEqual(self.s.active(QID)["created"], NOW + 60)
        self.assertEqual(self.s.db.execute("SELECT admin_removed FROM messages WHERE created=?", (NOW,)).fetchone()[0], NOW + 59)
        # A second-precision record at the generation boundary is ambiguous.
        self.feed(line(NOW + 60, "postsuper", QID, "removed"))
        self.assertIsNotNone(self.s.active(QID))

    def test_permission_or_other_missing_file_is_immediate(self):
        self.cycle(NOW)
        status, _ = self.cycle(NOW + 1, "fatal: open queue file %s: Permission denied" % QID)
        self.check_status(status, 2, 2)
        self.assertIsNone(self.s.db.execute("SELECT mid FROM queue_missing").fetchone())
        status, _ = self.cycle(NOW + 2, "fatal: open queue file OTHER123: No such file or directory")
        self.check_status(status, 2, 2)


if __name__ == "__main__":
    unittest.main()

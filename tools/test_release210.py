"""Regresiones de seguridad: rechazos externos y activación efectiva del timer en upgrade."""
import json
import os
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import outbound_spam as m
import runtime
NOW = 1793275200.0


def line(at, service, qid, text):
    return "%s host postfix/%s[123]: %s: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(at)), service, qid, text)


class FakeCommands:
    def __init__(self):
        self.calls, self.fail, self.sql_updates = [], None, 0
        self.password = "$6$old$" + "a" * 86
        self.newhash = "$6$new$" + "b" * 86

    def run(self, binary, args=(), data=None, ok=(0,)):
        self.calls.append((binary, list(args), data))
        if self.fail == binary:
            raise m.MonitorError("simulated " + binary)
        if binary == "openssl":
            return self.newhash
        if binary == "mysql":
            if data.startswith("SELECT mailuser_id"):
                return "42\t" + self.password.encode().hex()
            if data.startswith("SELECT CHARACTER"):
                return "255"
            if data.startswith("UPDATE"):
                self.password = self.newhash
                self.sql_updates += 1
                return "1"
            if data.startswith("SELECT HEX"):
                return self.password.encode().hex()
        return ""


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release210-", dir="/root" if os.geteuid() == 0 else "/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        environment = patch.dict(os.environ, OUTBOUND_SPAM_DATA=str(self.directory / "data"),
                                 OUTBOUND_SPAM_LOG=str(self.directory / "audit.log"))
        environment.start()
        self.addCleanup(environment.stop)
        self.c, _ = runtime.validate_config(b"notify=false\nmonitor_local=false\nblock_local=false\n", ROOT, m.Config.from_bytes)
        self.c.v.update(state_dir=str(self.directory / "state"), log_file=str(self.directory / "mail.log"),
                        max_sent=3, time_period=60, initial_position="start", routing_mode="static", local_domains={"local.test"}, custom_limits={})
        self.s = m.Store(m.private_directory(self.directory / "state") / "state.sqlite3")
        self.cmd = FakeCommands()

    def tearDown(self):
        self.s.db.close()

    def feed(self, text):
        parser = m.Parser(self.s, self.c, NOW + 120)
        for item in text.splitlines():
            parser.feed(item)


class ExternalRejectionTests(Base):
    def reject(self, at=NOW, address="target@remote.test", account="user@local.test", auth=True):
        return line(at, "submission/smtpd", "NOQUEUE",
                    "reject: RCPT from client[192.0.2.1]: 550 blocked; from=<sender@local.test> to=<%s> proto=ESMTP%s" %
                    (address, ", sasl_username=" + account if auth else ""))

    def resolve(self, config=None, router=None):
        config = config or self.c
        return m.resolve_recipients(self.s, config, router or m.Router(config, self.cmd))

    def test_strict_half_threshold_and_no_accepted_sends(self):
        self.c.v["max_sent"] = 4
        for i in range(2):
            self.feed(self.reject(NOW + i))
        self.resolve()
        self.assertFalse(m.evaluate(self.s, self.c, NOW + 1)[1])
        self.feed(self.reject(NOW + 2))
        self.resolve()
        current, opened = m.evaluate(self.s, self.c, NOW + 2)
        self.assertEqual(len(opened), 1)
        self.assertEqual(current["user@local.test"]["external_recipients"], 0)
        self.assertEqual(current["user@local.test"]["external_rejections"], 3)
        incident = self.s.db.execute("SELECT * FROM incidents").fetchone()
        self.assertEqual(incident["reason"], "external_rejections")
        report = m.Actions(self.s, self.c, self.cmd, NOW + 2).report(incident)
        self.assertIn("Rechazos externos en la ventana: 3", report)
        self.assertIn("target@remote.test", report)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_odd_limit_and_limit_one(self):
        for maximum in (1, 3, 101):
            with self.subTest(maximum=maximum):
                self.s.db.execute("DELETE FROM incidents")
                self.s.db.execute("DELETE FROM rejections")
                self.c.v["max_sent"] = maximum
                for i in range(maximum // 2 + 1):
                    self.feed(self.reject(NOW + i))
                self.resolve()
                self.assertEqual(len(m.evaluate(self.s, self.c, NOW + maximum // 2)[1]), 1)

    def test_local_anonymous_ignored_and_unknown_never_trigger(self):
        self.c.v["ignore"] = {"ignored@local.test"}
        for i in range(4):
            self.feed(self.reject(NOW + i, "target@local.test"))
            self.feed(self.reject(NOW + i, auth=False))
            self.feed(self.reject(NOW + i, account="ignored@local.test"))
            self.feed(self.reject(NOW + i, account="local:123"))
            self.feed(self.reject(NOW + i, address="unknown"))
        self.assertTrue(self.resolve())
        self.assertFalse(m.evaluate(self.s, self.c, NOW + 4)[1])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM rejections WHERE external=1").fetchone()[0], 0)

    def test_custom_limit_and_exclusive_window_boundary(self):
        self.c.v["custom_limits"] = {"user@local.test": (4, 10)}
        self.feed(self.reject(NOW - 10) + self.reject(NOW - 9) + self.reject(NOW))
        self.resolve()
        current, opened = m.evaluate(self.s, self.c, NOW)
        self.assertFalse(opened)
        self.assertEqual(current["user@local.test"]["external_rejections"], 2)
        self.feed(self.reject(NOW + 1))
        self.resolve()
        self.assertFalse(m.evaluate(self.s, self.c, NOW + 1)[1])
        self.feed(self.reject(NOW + 2))
        self.resolve()
        self.assertEqual(len(m.evaluate(self.s, self.c, NOW + 2)[1]), 1)

    def test_cleanup_reject_correlates_account_and_classifies_recipient(self):
        self.feed(line(NOW, "smtpd", "QUEUE1", "client=client[192.0.2.1], sasl_username=user@local.test") +
                  line(NOW, "cleanup", "QUEUE1", "milter-reject: END-OF-MESSAGE from client: 550 spam; from=<sender@local.test> to=<one@remote.test> proto=ESMTP"))
        self.resolve()
        row = self.s.db.execute("SELECT * FROM rejections").fetchone()
        self.assertEqual(row["account"], "user@local.test")
        self.assertEqual(row["external"], 1)
        self.assertIsNone(self.s.db.execute("SELECT accepted FROM messages").fetchone()[0])

    def test_routing_failure_is_excluded_then_retried(self):
        self.feed(self.reject() + self.reject(NOW + 1))
        router = m.Router(self.c, self.cmd)
        with patch.object(router, "external", side_effect=m.MonitorError("map unavailable")):
            self.assertTrue(self.resolve(router=router))
        self.assertFalse(m.evaluate(self.s, self.c, NOW + 1)[1])
        self.resolve()
        self.assertTrue(m.evaluate(self.s, self.c, NOW + 1)[1])

    def test_aliases_follow_final_destination_and_loops_remain_unknown(self):
        self.c.v["routing_mode"] = "auto"
        router = m.Router(self.c, self.cmd)
        router.parameters = dict(recipient_delimiter="+", virtual_alias_maps="hash:/aliases", transport_maps="",
                                 mydestination="local.test", local_transport="local", virtual_mailbox_domains="",
                                 virtual_transport="lmtp", relay_domains="", relay_transport="relay", default_transport="smtp",
                                 sender_dependent_default_transport_maps="")
        aliases = {"remote-alias@local.test": "target@remote.test", "local-alias@alias.test": "target@local.test",
                   "loop@alias.test": "other@alias.test", "other@alias.test": "loop@alias.test"}
        def lookup(tables, keys):
            return next((aliases[key] for key in keys if tables == "hash:/aliases" and key in aliases), "")
        for address in ("remote-alias@local.test", "local-alias@alias.test", "loop@alias.test"):
            self.feed(self.reject(address=address))
        with patch.object(router, "lookup", side_effect=lookup):
            self.assertTrue(self.resolve(router=router))
        result = {row["address"]: row["external"] for row in self.s.db.execute("SELECT address,external FROM rejections")}
        self.assertEqual(result, {"remote-alias@local.test": 1, "local-alias@alias.test": 0, "loop@alias.test": None})

    def test_identical_attempts_count_but_restart_rotation_and_reread_do_not_duplicate(self):
        path = Path(self.c["log_file"])
        path.write_text(self.reject() * 2)
        with self.s.db:
            m.LogReader(self.s, self.c, m.Parser(self.s, self.c, NOW), NOW).run()
        self.s.db.close()
        self.s = m.Store(self.directory / "state/state.sqlite3")
        with self.s.db:
            m.LogReader(self.s, self.c, m.Parser(self.s, self.c, NOW), NOW).run()
        path.rename(str(path) + ".1")
        path.write_text(self.reject(NOW + 1))
        with self.s.db:
            m.LogReader(self.s, self.c, m.Parser(self.s, self.c, NOW + 1), NOW + 1).run()
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM rejections").fetchone()[0], 3)
        self.s.db.execute("DELETE FROM meta WHERE key IN ('cursor','retired')")
        with self.s.db:
            m.LogReader(self.s, self.c, m.Parser(self.s, self.c, NOW + 1), NOW + 1).run(from_start=True)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM rejections").fetchone()[0], 3)

    def test_password_setting_and_existing_incident_actions(self):
        for reset in (False, True):
            with self.subTest(reset=reset):
                self.s.db.execute("DELETE FROM actions")
                self.s.db.execute("DELETE FROM incidents")
                self.s.db.execute("DELETE FROM rejections")
                self.c.v.update(reset_password=reset, block_smtp=True, notify=False)
                self.feed(self.reject() + self.reject(NOW + 1))
                self.resolve()
                m.evaluate(self.s, self.c, NOW + 1)
                actions = {row[0] for row in self.s.db.execute("SELECT kind FROM actions")}
                self.assertEqual("reset_password" in actions, reset)
                with patch.object(m.Actions, "block") as block:
                    m.Actions(self.s, self.c, self.cmd, NOW + 1).process()
                    m.Actions(self.s, self.c, self.cmd, NOW + 1).process()
                block.assert_called_once()
                self.assertEqual(self.cmd.sql_updates, int(reset))
                # Activar una acción posteriormente también funciona con incidente abierto.
                self.c.v["hold_queue"] = True
                m.evaluate(self.s, self.c, NOW + 1)
                self.assertIsNotNone(self.s.db.execute("SELECT 1 FROM actions WHERE kind='hold_queue'").fetchone())

    def test_acknowledgement_and_old_incidents_are_not_reopened(self):
        self.feed(self.reject() + self.reject(NOW + 1))
        self.resolve()
        self.s.db.execute("INSERT INTO acknowledgements VALUES (?,?)", ("user@local.test", NOW + 1))
        self.assertFalse(m.evaluate(self.s, self.c, NOW + 1)[1])
        self.s.db.execute("DELETE FROM acknowledgements")
        self.assertFalse(m.evaluate(self.s, self.c, NOW + 1000)[1])


class AutoUpdateTests(Base):
    def setUp(self):
        super().setUp()
        self.c.v["service_unit"] = str(self.directory / "outbound-spam.service")
        Path(self.c["service_unit"]).write_text("# outbound-spam\n[Service]\n")
        self.config_path = str(self.directory / "outbound-spam.conf")

    def sync(self):
        return runtime.sync_auto_update(self.c, self.cmd, ROOT, self.config_path, m.atomic_write)

    def test_independent_timer_is_enabled_and_reloads_interval(self):
        self.sync()
        service, timer = runtime.auto_update_units(self.c)
        self.assertIn("--upgrade --automatic", service.read_text())
        self.assertIn("UnsetEnvironment=OUTBOUND_SPAM_SERVICE", service.read_text())
        self.assertNotIn("PartOf=", service.read_text())
        self.assertIn("OnUnitInactiveSec=3600", timer.read_text())
        self.assertIn(("systemctl", ["enable", "--now", timer.name], None), self.cmd.calls)
        before = timer.stat().st_mtime_ns
        self.sync()
        self.assertEqual(timer.stat().st_mtime_ns, before)
        self.c.v["auto_update_interval"] = 1800
        self.sync()
        self.assertIn("OnUnitInactiveSec=1800", timer.read_text())
        self.assertIn(("systemctl", ["restart", timer.name], None), self.cmd.calls)

    def test_explicit_disable_is_preserved_and_timer_stopped(self):
        self.c.v["auto_update"] = False
        self.sync()
        _, timer = runtime.auto_update_units(self.c)
        self.assertIn(("systemctl", ["disable", "--now", timer.name], None), self.cmd.calls)
        self.assertNotIn(("systemctl", ["enable", "--now", timer.name], None), self.cmd.calls)

    def test_does_not_overwrite_foreign_unit(self):
        service, timer = runtime.auto_update_units(self.c)
        timer.write_text("# another application\n")
        with self.assertRaisesRegex(ValueError, "ajena"):
            self.sync()
        self.assertEqual(timer.read_text(), "# another application\n")
        self.assertFalse(service.exists())

    def test_failed_systemctl_restores_unit_files(self):
        self.cmd.fail = "systemctl"
        with self.assertRaises(m.MonitorError):
            self.sync()
        self.assertTrue(all(not path.exists() for path in runtime.auto_update_units(self.c)))

    def test_packager_refuses_operational_setting_missing_from_example(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from build_release import verify_configuration
        source = self.directory / "candidate"
        shutil.copytree(ROOT / "lib", source / "lib")
        example = "\n".join(row for row in (ROOT / "outbound-spam.conf.example").read_text().splitlines()
                            if not row.startswith("auto_update_interval=")) + "\n"
        (source / "outbound-spam.conf.example").write_text(example)
        with self.assertRaisesRegex(ValueError, "Contrato de configuración"):
            verify_configuration(source)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "Requiere systemd-analyze")
    def test_unit_syntax_is_accepted_by_systemd(self):
        self.sync()
        service, timer = runtime.auto_update_units(self.c)
        result = subprocess.run(["systemd-analyze", "verify", str(service), str(timer)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

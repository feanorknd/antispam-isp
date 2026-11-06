"""Prueba en contenedor/root: paquete público 1.3.4 -> instalador 1.5 -> retorno a 1.3.4."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = (ROOT / "VERSION").read_text().strip()
BASE_RELEASE = os.environ.get("BASE_RELEASE", "1.3.4")
sys.path.insert(0, str(ROOT / "lib"))
import outbound_spam as m
import runtime


@unittest.skipUnless(os.geteuid() == 0 and os.environ.get("OUTBOUND_SPAM_NATIVE") == "1", "Ejecutar en el contenedor de pruebas con root")
class Upgrade150Tests(unittest.TestCase):
    def test_public_release_upgrade_daemon_dashboard_and_return(self):
        old_archive = ROOT / "dist" / ((BASE_RELEASE + "/") if BASE_RELEASE == "1.5.0" else "") / ("outbound-spam-" + BASE_RELEASE + ".tar.gz")
        self.assertEqual(hashlib.sha256(old_archive.read_bytes()).hexdigest(),
                         {"1.3.4": "7a7fdd25aed00ae2bb772fbf5ee030b013809eb03bcac0a5e8f906066b361dcf",
                          "1.5.0": "b89e5f3ccbd8d2bbc52c5a729fecdc4781d69bf751024247a3dd56b39c674905"}[BASE_RELEASE])
        new_archive = ROOT / "dist" / CANDIDATE / ("outbound-spam-" + CANDIDATE + ".tar.gz")
        with tempfile.TemporaryDirectory(dir="/root", prefix="release150-") as temp:
            root = Path(temp)
            for archive in (old_archive, new_archive):
                with tarfile.open(archive) as tar:
                    tar.extractall(root)
            target, data = root / "scripts", root / "data"
            env = dict(os.environ, OUTBOUND_SPAM_DATA=str(data), OUTBOUND_SPAM_LOG=str(root / "audit.log"),
                       OUTBOUND_SPAM_LOGROTATE=str(root / "logrotate"))
            def run(*args):
                p = subprocess.run(list(args), env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return p.stdout
            old_installer = root / ("outbound-spam-" + BASE_RELEASE) / "install.sh"
            run("bash", str(old_installer), "--target", str(target))
            config = target / "outbound-spam.conf"
            fake_systemctl = root / "systemctl"
            fake_systemctl.write_text("#!/bin/sh\ncase \"$1\" in is-active) printf 'active\\n';; *) exit 0;; esac\n")
            fake_systemctl.chmod(0o700)
            (root / "outbound-spam.service").write_text("# outbound-spam\n[Service]\nExecStart=/bin/true\n")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            log = root / "mail.log"
            log.write_text("")
            with config.open("a") as f:
                f.write("\nstate_dir=%s\nlog_file=%s\nmonitor_local=false\nblock_local=false\nnotify=false\n"
                        "max_sent=100000\ninitial_position=start\nrun_interval=1\nrouting_mode=static\nlocal_domains=(local.test)\n"
                        "status_http_port=%d\nsystemctl_bin=%s\nservice_unit=%s\n" %
                        (data, log, port, fake_systemctl, root / "outbound-spam.service"))
            before, _ = runtime.validate_config(config.read_bytes(), ROOT, m.Config.from_bytes)
            store = m.Store(data / "state.sqlite3")
            with store.db:
                store.set("mode", "live")
                store.set("stable_preserved", {"important": True})
            store.db.close()
            if BASE_RELEASE == "1.5.0":
                import analytics
                history = analytics.History(data / "analytics.sqlite3")
                with history.db:
                    analytics.put(history.db, "preview_preserved", True)
                history.db.close()
            output = run("bash", str(root / ("outbound-spam-" + CANDIDATE) / "install.sh"), "--target", str(target), "--config", str(config))
            # El preparador y el instalador publican cada uno su objeto JSON.
            decoder = json.JSONDecoder()
            while output.strip():
                output = output.lstrip()
                if not output.startswith("{"):
                    break
                result, end = decoder.raw_decode(output)
                output = output[end:]
            self.assertTrue(result["service_restarted"])
            if BASE_RELEASE == "1.3.4":
                self.assertIn("analytics_enabled", result["config_variables_added"])
            else:
                self.assertNotIn("analytics_enabled", result["config_variables_added"])
                history = analytics.History(data / "analytics.sqlite3")
                self.assertTrue(analytics.value(history.db, "preview_preserved"))
                history.db.close()
            self.assertIn(CANDIDATE, run("bash", str(target / "outbound-spam.sh"), "--version"))
            after, _ = runtime.validate_config(config.read_bytes(), ROOT, m.Config.from_bytes)
            self.assertEqual(before.v, after.v)
            run("bash", str(target / "outbound-spam.sh"), "--doctor")
            run("/usr/sbin/postfix", "start")
            daemon = subprocess.Popen(["bash", str(target / "outbound-spam.sh"), "--config", str(config), "--daemon"], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            stats_pid = None
            try:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
                log.write_text("\n".join(stamp + " host postfix/" + s for s in (
                    "smtpd[123]: TEST150: client=host[192.0.2.1], sasl_username=trial@local.test",
                    "qmgr[123]: TEST150: from=<trial@local.test>, size=100, nrcpt=1 (queue active)",
                    "smtp[123]: TEST150: to=<one@remote.test>, status=sent (ok)", "qmgr[123]: TEST150: removed")) + "\n")
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urlopen("http://127.0.0.1:%d/dashboard/api/overview" % port, timeout=2) as response:
                            panel = json.load(response)
                        if panel["totals"]["messages"] == 1 and panel["worker"].get("last_work_at") is not None:
                            break
                    except (OSError, URLError):
                        pass
                    self.assertIsNone(daemon.poll(), daemon.stderr.read() if daemon.poll() is not None else "")
                    if time.monotonic() >= deadline:
                        details = {p.name: p.read_text() for p in (data / "analytics-status.json", data / "status.json", root / "audit.log") if p.exists()}
                        self.fail("El trabajador no ha actualizado el panel: " + json.dumps(details))
                    time.sleep(0.3)
                stats_pid = panel["worker"]["pid"]
                self.assertIsNotNone(panel["worker"]["last_work_at"])
                self.assertGreaterEqual(panel["worker"]["last_work_duration_seconds"], 0)
                with urlopen("http://127.0.0.1:%d/" % port) as response:
                    self.assertIn("http_metrics", json.load(response))
                with urlopen("http://127.0.0.1:%d/dashboard/assets/app.js" % port) as response:
                    self.assertEqual(response.status, 200)
                if os.environ.get("ANALYTICS_CORRUPTION_LIVE") == "1":
                    # Avería real del histórico con el analizador siguiendo activo.
                    os.kill(stats_pid, signal.SIGTERM)
                    until = time.monotonic() + 10
                    while True:
                        try:
                            os.kill(stats_pid, 0)
                        except ProcessLookupError:
                            break
                        self.assertLess(time.monotonic(), until)
                        time.sleep(0.2)
                    (data / "analytics.sqlite3").write_bytes(b"damaged historical sqlite" * 100)
                    with log.open("a") as output:
                        output.write(log.read_text().replace("TEST150", "NEW150"))
                    started = time.time()
                    until = time.monotonic() + 85
                    monitor_continued = False
                    while time.monotonic() < until:
                        self.assertIsNone(daemon.poll(), "La avería estadística terminó el demonio")
                        with urlopen("http://127.0.0.1:%d/" % port) as response:
                            status = json.load(response)
                        if status["last_success"] > started:
                            monitor_continued = True
                        try:
                            with urlopen("http://127.0.0.1:%d/dashboard/api/overview" % port) as response:
                                recovered = json.load(response)
                            if recovered["history"]["restored_at"] and recovered["worker"]["ok"] and recovered["totals"]["messages"] >= 1:
                                stats_pid = recovered["worker"]["pid"]
                                break
                        except (OSError, URLError):
                            pass
                        time.sleep(0.3)
                    else:
                        self.fail("El trabajador no se recuperó automáticamente de la corrupción")
                    self.assertTrue(monitor_continued, "No hubo pasadas correctas durante la avería estadística")
            finally:
                daemon.terminate()
                daemon.communicate(timeout=20)
            self.assertEqual(daemon.returncode, 0)
            if stats_pid:
                with self.assertRaises(ProcessLookupError):
                    os.kill(stats_pid, 0)
            # El instalador antiguo puede volver a abrir el esquema operativo 4, sin rebobinarlo.
            run("bash", str(old_installer), "--target", str(target), "--config", str(config))
            self.assertIn(BASE_RELEASE, run("bash", str(target / "outbound-spam.sh"), "--version"))
            store = m.Store(data / "state.sqlite3")
            try:
                self.assertTrue(store.get("stable_preserved")["important"])
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM messages WHERE account='trial@local.test'").fetchone()[0],
                                 2 if os.environ.get("ANALYTICS_CORRUPTION_LIVE") == "1" else 1)
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 4)
            finally:
                store.db.close()


if __name__ == "__main__":
    unittest.main()

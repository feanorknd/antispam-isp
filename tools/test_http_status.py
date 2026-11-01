"""Pruebas de integración del endpoint HTTP y migración de configuración."""
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
TEMP_PARENT = "/root" if os.geteuid() == 0 else "/tmp/opencode"
sys.path.insert(0, str(ROOT / "lib"))
import outbound_spam
import runtime


def run_daemon(directory, port):
    class Worker:
        pid = 1234

        def poll(self):
            return 0

    os.environ["OUTBOUND_SPAM_LOG"] = str(Path(directory) / "service.log")
    config = {"state_dir": directory, "run_interval": 60, "lock_wait": 1,
              "status_http_host": "127.0.0.1", "status_http_port": port, "stale_after": 180}
    runtime.daemon_loop(config, ROOT, str(Path(directory) / "monitor.conf"), outbound_spam.atomic_write,
                        spawn=lambda command: Worker())


class StatusHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        config = {"status_http_host": "127.0.0.1", "status_http_port": 0, "stale_after": 180}
        self.server, self.thread = runtime.start_status_http(config, self.directory)
        self.addCleanup(self.stop_server)
        self.url = "http://127.0.0.1:%s/" % self.server.server_port

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self):
        with urlopen(self.url, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/json")
            return json.load(response)

    def write_status(self, **changes):
        status = {"code": 0, "message": "OK", "last_success": time.time(), "stale_after": 180,
                  "incidents": [], "held": 0, "blocked": [], "issues": [], "warming_up": True,
                  "accounts": {"test@example.org": {"count": 12}}}
        status.update(changes)
        (self.directory / "status.json").write_text(json.dumps(status))

    def test_fresh_complete_status_and_numeric_metrics(self):
        self.write_status(code=1, incidents=[{"peak": 99}], held=2, blocked=["user"], issues=[])
        result = self.get()
        self.assertEqual(result["accounts"]["test@example.org"]["count"], 12)
        self.assertEqual(result["http_metrics"]["code"], 1)
        self.assertEqual(result["http_metrics"]["warming_up"], 1)
        self.assertEqual(result["http_metrics"]["incidents"], 1)
        self.assertEqual(result["http_metrics"]["held"], 2)
        self.assertEqual(result["http_metrics"]["blocked"], 1)
        self.assertEqual(result["incidents"][0]["peak"], 99)

    def test_missing_invalid_stale_and_future_are_errors(self):
        self.assertEqual(self.get()["code"], 2)
        (self.directory / "status.json").write_text("{")
        self.assertEqual(self.get()["code"], 2)
        for value in (time.time() - 181, time.time() + 61):
            self.write_status(last_success=value)
            self.assertEqual(self.get()["http_metrics"]["code"], 2)
        self.write_status(code=2)
        self.assertEqual(self.get()["code"], 2)

    def test_non_finite_and_malformed_status_returns_json_error(self):
        for text in ('[]', '{}', '{"code":true}', '{"code":0,"last_success":NaN,"stale_after":180}',
                     '{"code":0,"last_success":0,"stale_after":180,"other":Infinity}'):
            (self.directory / "status.json").write_text(text)
            self.assertEqual(self.get()["code"], 2)

    def test_concurrent_reads_with_atomic_status_updates(self):
        self.write_status()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = []
            for index in range(20):
                outbound_spam.atomic_write(self.directory / "status.json", json.dumps({
                    "code": index % 3, "message": "estado", "last_success": time.time(), "stale_after": 180}))
                futures.append(pool.submit(self.get))
            for future in futures:
                self.assertIn(future.result()["code"], (0, 1, 2))

    def test_other_path_and_port_collision(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "status", timeout=3)
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(OSError):
            runtime.start_status_http({"status_http_host": "127.0.0.1", "status_http_port": self.server.server_port,
                                       "stale_after": 180}, self.directory)


class MigrationTests(unittest.TestCase):
    def test_migration_adds_only_missing_values_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=TEMP_PARENT) as folder:
            path = Path(folder) / "monitor.conf"
            path.write_text("notify=false\nstatus_http_port=34567\n")
            os.chmod(path, 0o600)
            result = runtime.migrate_http_config(path, ROOT, outbound_spam.Config.from_bytes, outbound_spam.atomic_write)
            self.assertEqual(result, ["status_http_host=127.0.0.1"])
            data = path.read_text()
            self.assertIn("status_http_port=34567", data)
            self.assertEqual(data.count("status_http_port="), 1)
            self.assertEqual(runtime.migrate_http_config(path, ROOT, outbound_spam.Config.from_bytes, outbound_spam.atomic_write), [])
            self.assertEqual(path.read_text(), data)

    def test_both_defaults_appended_and_invalid_config_preserved(self):
        with tempfile.TemporaryDirectory(dir=TEMP_PARENT) as folder:
            path = Path(folder) / "monitor.conf"
            path.write_text("notify=false")
            os.chmod(path, 0o600)
            runtime.migrate_http_config(path, ROOT, outbound_spam.Config.from_bytes, outbound_spam.atomic_write)
            self.assertTrue(path.read_text().startswith("notify=false\n"))
            config, _ = runtime.validate_config(path.read_bytes(), ROOT, outbound_spam.Config.from_bytes)
            self.assertEqual(config["status_http_host"], "127.0.0.1")
            self.assertEqual(config["status_http_port"], 23456)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for invalid in ("notify=false\nstatus_http_port=65536\n", "notify=false\nstatus_http_host=bad\n"):
                path.write_text(invalid)
                with self.assertRaises((ValueError, outbound_spam.MonitorError)):
                    runtime.migrate_http_config(path, ROOT, outbound_spam.Config.from_bytes, outbound_spam.atomic_write)
                self.assertEqual(path.read_text(), invalid)

    def test_help_and_cli_require_daemon_for_operational_pass(self):
        command = [sys.executable, str(ROOT / "lib/outbound_spam.py")]
        result = subprocess.run(command + ["--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("cron", result.stdout.lower())
        self.assertNotIn("--run", result.stdout)
        with tempfile.TemporaryDirectory(dir=TEMP_PARENT) as folder:
            env = dict(os.environ, OUTBOUND_SPAM_LOG=str(Path(folder) / "manual.log"))
            env.pop("OUTBOUND_SPAM_SERVICE", None)
            result = subprocess.run(command + ["--run"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("requiere el servicio", result.stderr)


class DaemonHTTPTests(unittest.TestCase):
    def test_listener_follows_daemon_lifecycle(self):
        with tempfile.TemporaryDirectory(dir=TEMP_PARENT) as folder:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            status = {"code": 0, "message": "OK", "last_success": time.time(), "stale_after": 180}
            (Path(folder) / "status.json").write_text(json.dumps(status))
            process = multiprocessing.Process(target=run_daemon, args=(folder, port))
            process.start()
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with urlopen("http://127.0.0.1:%d/" % port, timeout=1) as response:
                            self.assertEqual(json.load(response)["code"], 0)
                        break
                    except OSError:
                        if time.monotonic() >= deadline or not process.is_alive():
                            raise
                        time.sleep(0.05)
            finally:
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()
            self.assertEqual(process.exitcode, 0)
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)


if __name__ == "__main__":
    unittest.main()

"""Paquete compatible y CLI --upgrade real 1.3.0–1.3.4; en Docker se arranca el demonio resultante."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import analytics
import distribution as d
import outbound_spam as m
import runtime

spec = importlib.util.spec_from_file_location("release_builder_200", ROOT / "tools/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
VERSION = (ROOT / "VERSION").read_text().strip()
HASHES = {
    "1.3.0": "296a1ef23677675a145c1a00ec7e03b6850f45df2170a18e9b64f246f0702f94",
    "1.3.1": "7a22bee90b6c65e75165907c42c32d05b333360cf888e3b00324f80f82f110db",
    "1.3.2": "7a65fb5d8e9e9c5111fe65409b62d46b79d55a0ebf94eefbcb74ec6784227e30",
    "1.3.3": "12e3a926394b34a3156b368076c261ed38e249a0ece56969a312559c59b267ab",
    "1.3.4": "7a7fdd25aed00ae2bb772fbf5ee030b013809eb03bcac0a5e8f906066b361dcf",
}


class PackedDistributionTests(unittest.TestCase):
    def test_all_released_validators_accept_package_without_new_paths(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temp:
            root = Path(temp)
            archive, checksum = builder.build(ROOT, root / "assets")
            source = d.unpack(archive, root / "source", VERSION)
            manifest = d.validate_tree(source)
            self.assertEqual(set(manifest["bundled_components"]), set(d.BUNDLED_COMPONENTS))
            self.assertFalse(set(d.BUNDLED_COMPONENTS) & set(manifest["files"]))
            for version, expected in HASHES.items():
                with self.subTest(version=version):
                    previous = ROOT / "dist" / ("outbound-spam-" + version + ".tar.gz")
                    self.assertEqual(d.digest(previous), expected)
                    with tarfile.open(previous) as tar:
                        text = tar.extractfile("outbound-spam-" + version + "/lib/distribution.py").read()
                    old = types.ModuleType("distribution_" + version.replace(".", "_"))
                    exec(compile(text, str(previous), "exec"), old.__dict__)
                    old.unpack(archive, root / version, VERSION)
            code = ("import sys;sys.path.insert(0,%r);import runtime,analytics,dashboard;"
                    "assert b'OUTBOUND SPAM' in runtime.dashboard_asset('index.html');"
                    "assert callable(analytics.worker) and callable(dashboard.serve)" % str(source / "lib"))
            subprocess.run([sys.executable, "-c", code], check=True)

    def test_internal_bundle_hashes_detect_mismatch(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temp:
            root = Path(temp)
            archive, _ = builder.build(ROOT, root / "assets")
            source = d.unpack(archive, root / "source", VERSION)
            manifest_path = source / "RELEASE.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["bundled_components"]["web/app.js"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "componentes integrados"):
                d.validate_tree(source)


@unittest.skipUnless(os.geteuid() == 0 and os.environ.get("OUTBOUND_SPAM_NATIVE") == "1"
                     and Path("/.dockerenv").exists(), "Requiere contenedor de pruebas con root")
class ReleasedUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["/usr/sbin/postfix", "start"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["/usr/sbin/postfix", "stop"], check=True, capture_output=True)

    def check_upgrade(self, previous, active, enabled=True):
        with tempfile.TemporaryDirectory(dir="/root", prefix="upgrade200-") as temp:
            root = Path(temp)
            historical = ROOT / "dist" / ("outbound-spam-" + previous + ".tar.gz")
            self.assertEqual(d.digest(historical), HASHES[previous])
            with tarfile.open(historical) as tar:
                tar.extractall(root)
            target, data, state = root / "scripts", root / "data", root / "state"
            env = dict(os.environ, OUTBOUND_SPAM_DATA=str(data), OUTBOUND_SPAM_LOG=str(root / "audit.log"),
                       OUTBOUND_SPAM_LOGROTATE=str(root / "logrotate"))
            def run(*command, timeout=60):
                result = subprocess.run(list(command), env=env, capture_output=True, text=True, timeout=timeout)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            run("bash", str(root / ("outbound-spam-" + previous) / "install.sh"), "--target", str(target))
            config, log = target / "outbound-spam.conf", root / "mail.log"
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            unit, control = root / "outbound-spam.service", root / "systemctl"
            control.write_text("#!/usr/bin/python3\n" + """import os,signal,subprocess,sys,time
from pathlib import Path
root=Path(__file__).parent
action=sys.argv[1]
with (root/'calls').open('a') as output: output.write(' '.join(sys.argv[1:])+'\\n')
if action=='is-active':
    active=(root/'active').read_text()=='active'
    print('active' if active else 'inactive');sys.exit(0 if active else 3)
if action=='stop':
    if (root/'daemon.pid').exists():
        try: os.kill(int((root/'daemon.pid').read_text()),signal.SIGTERM)
        except ProcessLookupError: pass
    (root/'active').write_text('inactive')
if action=='start':
    with (root/'daemon-output').open('ab') as output:
        child=subprocess.Popen(['/bin/bash',str(root/'scripts/outbound-spam.sh'),'--config',str(root/'scripts/outbound-spam.conf'),'--daemon'],stdout=output,stderr=output,start_new_session=True)
    (root/'daemon.pid').write_text(str(child.pid));(root/'active').write_text('active')
""")
            control.chmod(0o700)
            (root / "active").write_text("active" if active else "inactive")
            original = ("# Configuración privada que debe conservar su prioridad\nnotify=false\nreport=()\nmonitor_local=false\n"
                        "block_local=false\nblock_smtp=false\nhold_queue=false\nreset_password=false\n"
                        "ignore=(ignored@example.test)\ndeclare -A custom_limits=([\"existing@example.test\"]=\"123:900\")\n"
                        "max_sent=100000\ninitial_position=start\nrouting_mode=static\nlocal_domains=(local.test)\n"
                        "state_dir=%s\nlog_file=%s\nservice_unit=%s\nsystemctl_bin=%s\nstatus_http_port=%d\n"
                        "run_interval=1\n" % (state, log, unit, control, port))
            if not enabled:
                original += "analytics_enabled=false\nanalytics_outbox_mb=17\n"
            config.write_text(original)
            config.chmod(0o600)
            profile, _ = runtime.validate_config(config.read_bytes(), ROOT, m.Config.from_bytes)
            unit.write_text(runtime.render_service(profile, target, str(config)))
            log.write_text("")
            state.mkdir(mode=0o700)
            store = m.Store(state / "state.sqlite3")
            with store.db:
                store.set("mode", "live")
                store.set("upgrade_preserved", {"important": True})
                store.db.execute("INSERT INTO messages(qid,account,created,accepted,sender,nrcpt,removed,auth_line) VALUES (?,?,?,?,?,?,?,?)",
                                 ("PRE200", "existing@example.test", time.time() - 60, time.time() - 60, "existing@example.test", 1, 1,
                                  "existing authenticated message"))
                store.recipient(1, "old@remote.test", 1, "sent", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 60)) + " old delivery")
            store.db.close()
            archive = ROOT / "dist" / VERSION / ("outbound-spam-" + VERSION + ".tar.gz")
            self.assertTrue(archive.exists(), "Generar el paquete final antes de ejecutar la prueba")
            checksum = d.digest(archive)
            tag = "v" + VERSION
            url = "https://github.com/" + d.PROJECT + "/releases/download/" + tag + "/"
            metadata = {"tag_name": tag, "draft": False, "prerelease": False, "assets": [
                {"name": archive.name, "browser_download_url": url + archive.name},
                {"name": "SHA256SUMS", "browser_download_url": url + "SHA256SUMS"}]}
            (root / "metadata.json").write_text(json.dumps(metadata))
            driver = root / "upgrade.py"
            driver.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0,%r)\nimport distribution\n" % str(data / "current/lib") +
                              "payloads={%r:Path(%r).read_bytes(),%r:Path(%r).read_bytes(),%r:%r}\n" %
                              (d.LATEST, str(root / "metadata.json"), url + archive.name, str(archive), url + "SHA256SUMS", (checksum + "  " + archive.name + "\n").encode()) +
                              ("" if os.environ.get("LIVE_RELEASE") == "1" else
                               "distribution.upgrade.__defaults__=(lambda url,limit:payloads[url],distribution._prepare)\n") +
                              "from outbound_spam import main\nsys.argv=['outbound-spam.sh','--config',%r,'--upgrade'];sys.exit(main())\n" % str(config))
            env.update(OUTBOUND_SPAM_BASE=str(target), OUTBOUND_SPAM_CODE=str(data / "current"))
            try:
                result = json.loads(run("python3", str(driver)))
                self.assertTrue(result["updated"])
                self.assertEqual(result["version"], VERSION)
                self.assertEqual(result["service_restarted"], active)
                self.assertTrue(config.read_text().endswith(original))
                self.assertEqual(d.assigned_names(config.read_text()), d.assigned_names((ROOT / "outbound-spam.conf.example").read_text()))
                migrated, _ = runtime.validate_config(config.read_bytes(), ROOT, m.Config.from_bytes)
                self.assertEqual(migrated.v, profile.v)
                self.assertEqual(migrated["analytics_enabled"], enabled)
                self.assertTrue(json.loads((data / "outbound-spam.conf.last-good.json").read_text())["data"])
                self.assertIn(VERSION, run("bash", str(target / "outbound-spam.sh"), "--version"))
                self.assertTrue(json.loads((data / "installation.json").read_text())["project"] == d.PROJECT)
                store = m.Store(state / "state.sqlite3")
                self.assertTrue(store.get("upgrade_preserved")["important"])
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM messages WHERE qid='PRE200'").fetchone()[0], 1)
                store.db.close()
                calls = (root / "calls").read_text()
                self.assertEqual("start outbound-spam.service" in calls, active)
                if not active:
                    run(str(control), "start", unit.name)
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
                def mail(qid, account, mixed=False):
                    rows = [("smtpd", "client=host[192.0.2.1], sasl_username=" + account),
                            ("qmgr", "from=<%s>, size=100, nrcpt=%d (queue active)" % (account, 2 if mixed else 1)),
                            ("smtp", "to=<one@remote.test>, relay=relay[192.0.2.2], status=sent (ok)")]
                    if mixed:
                        rows.append(("lmtp", "to=<two@local.test>, status=sent (ok)"))
                    rows.append(("qmgr", "removed"))
                    return "".join(stamp + " host postfix/" + service + "[123]: " + qid + ": " + text + "\n" for service, text in rows)
                log.write_text(mail("LIVE200", "new@example.test", True) + mail("IGNORE200", "ignored@example.test"))
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        with urlopen("http://127.0.0.1:%d/" % port, timeout=2) as response:
                            status = json.load(response)
                        if enabled:
                            with urlopen("http://127.0.0.1:%d/dashboard/api/overview?user=new%%40example.test" % port, timeout=2) as response:
                                panel = json.load(response)
                            if panel["totals"]["recipients"] == 2 and panel["worker"]["ok"]:
                                break
                        elif status.get("last_success", 0) > 0:
                            break
                    except (OSError, ValueError, KeyError):
                        pass
                    time.sleep(0.2)
                else:
                    detail = (root / "daemon-output").read_text() if (root / "daemon-output").exists() else ""
                    self.fail("El nuevo servicio no está operativo: " + detail)
                self.assertIn("http_metrics", status)
                self.assertIn(status["code"], (0, 1))
                if enabled:
                    self.assertEqual(panel["totals"]["messages"], 1)
                    with urlopen("http://127.0.0.1:%d/dashboard/assets/app.js" % port) as response:
                        self.assertIn(b"const tick", response.read())
                    with urlopen("http://127.0.0.1:%d/dashboard/" % port) as response:
                        self.assertIn(b"OUTBOUND SPAM", response.read())
                    with urlopen("http://127.0.0.1:%d/dashboard/assets/style.css" % port) as response:
                        self.assertIn(b"table th,table td,table td.number{text-align:left}", response.read())
                    with urlopen("http://127.0.0.1:%d/dashboard/api/overview?ignored=only" % port) as response:
                        self.assertEqual(json.load(response)["totals"]["recipients"], 1)
                else:
                    self.assertFalse((state / "analytics-status.json").exists())
                before_text, before_mtime = config.read_bytes(), config.stat().st_mtime_ns
                before_pid = (root / "daemon.pid").read_text()
                second = json.loads(run("python3", str(driver)))
                self.assertFalse(second["updated"])
                self.assertEqual(config.read_bytes(), before_text)
                self.assertEqual(config.stat().st_mtime_ns, before_mtime)
                self.assertEqual((root / "daemon.pid").read_text(), before_pid)
            finally:
                if (root / "daemon.pid").exists():
                    run(str(control), "stop", unit.name)
                    import fcntl
                    for name in ("daemon.lock", "analytics.lock"):
                        path = state / name
                        if not path.exists():
                            continue
                        until = time.monotonic() + 15
                        with path.open("a") as lock:
                            while True:
                                try:
                                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                    break
                                except BlockingIOError:
                                    self.assertLess(time.monotonic(), until, "No se detuvo el proceso " + name)
                                    time.sleep(0.1)

    def test_upgrade_130(self):
        for active in (True, False):
            with self.subTest(active=active):
                self.check_upgrade("1.3.0", active)

    def test_upgrade_131(self):
        for active in (True, False):
            with self.subTest(active=active):
                self.check_upgrade("1.3.1", active)

    def test_upgrade_132(self):
        for active in (True, False):
            with self.subTest(active=active):
                self.check_upgrade("1.3.2", active)

    def test_upgrade_133(self):
        for active in (True, False):
            with self.subTest(active=active):
                self.check_upgrade("1.3.3", active)

    def test_upgrade_134(self):
        for active in (True, False):
            with self.subTest(active=active):
                self.check_upgrade("1.3.4", active)

    def test_explicit_analytics_disabled_is_preserved(self):
        self.check_upgrade("1.3.4", True, enabled=False)


if __name__ == "__main__":
    unittest.main()

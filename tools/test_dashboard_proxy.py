"""Integración Nginx real aislado en Docker: prefijo, autenticación, recursos y filtros."""
import base64
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent


class ProxyTests(unittest.TestCase):
    def test_authenticated_prefix_and_relative_assets(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode", prefix="dashboard-proxy-") as folder:
            root = Path(folder)
            def port():
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    return sock.getsockname()[1]
            backend, frontend = port(), port()
            demo = subprocess.Popen([sys.executable, str(ROOT / "tools/dashboard_demo.py"), str(root / "data"), "--port", str(backend)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            container = None
            try:
                (root / "auth").write_text("tester:{SHA}" + base64.b64encode(hashlib.sha1(b"dashboard-test").digest()).decode() + "\n")
                (root / "nginx.conf").write_text("""user root;
events { worker_connections 64; }
http { access_log off; server { listen 127.0.0.1:%d;
location = /spam-status {
 auth_basic "Restricted access"; auth_basic_user_file /fixture/auth;
 proxy_pass http://127.0.0.1:%d/; proxy_buffering off;
}
location ^~ /spam-status/ {
 auth_basic "Restricted access"; auth_basic_user_file /fixture/auth;
 proxy_pass http://127.0.0.1:%d/dashboard/; proxy_buffering off;
}
} }
""" % (frontend, backend, backend))
                container = subprocess.check_output(["docker", "run", "--rm", "-d", "--network", "host", "-v", str(root) + ":/fixture:ro",
                                                     "nginx:alpine", "nginx", "-c", "/fixture/nginx.conf", "-g", "daemon off;"], text=True).strip()
                base = "http://127.0.0.1:%d/spam-status" % frontend
                def get(path, authenticated=True):
                    headers = {"Authorization": "Basic " + base64.b64encode(b"tester:dashboard-test").decode()} if authenticated else {}
                    return urlopen(Request(base + path, headers=headers), timeout=3)
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with get("/api/overview") as response:
                            result = json.load(response)
                        break
                    except (OSError, URLError):
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.2)
                for suffix in ("", "/", "/api/users", "/assets/app.js", "/assets/style.css"):
                    with self.assertRaises(HTTPError) as error:
                        get(suffix, authenticated=False)
                    self.assertEqual(error.exception.code, 401)
                    with get(suffix) as response:
                        self.assertEqual(response.status, 200)
                with get("") as response:
                    self.assertIn("http_metrics", json.load(response))
                with get("/") as response:
                    self.assertIn(b'src="assets/app.js"', response.read())
                with get("/api/overview?ignored=exclude") as response:
                    filtered = json.load(response)
                self.assertGreater(result["totals"]["messages"], filtered["totals"]["messages"])
                with get("/api/overview?user=ana%40example.test") as response:
                    selected = json.load(response)
                self.assertGreater(result["totals"]["messages"], selected["totals"]["messages"])
                with self.assertRaises(HTTPError) as error:
                    get("/api/overview?bucket=invalid")
                self.assertEqual(error.exception.code, 400)
            finally:
                if container:
                    subprocess.run(["docker", "stop", "--time", "3", container], capture_output=True, check=True)
                demo.terminate()
                demo.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()

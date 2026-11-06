"""Configuración estable, bucle secuencial e integración systemd."""
import base64
import datetime as dt
import fcntl
import hashlib
import http.server
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import types
import zlib


# Sustituido por el empaquetador: los actualizadores 1.3.x solo admiten los
# componentes originales. El repo conserva los fuentes separados y legibles.
_RELEASE_BUNDLE = ""
_bundle_files = None


def bundled_files():
    global _bundle_files
    if _bundle_files is None:
        _bundle_files = json.loads(zlib.decompress(base64.b64decode(_RELEASE_BUNDLE))) if _RELEASE_BUNDLE else {}
    return _bundle_files


def install_bundled_modules():
    files = bundled_files()
    for name in ("analytics", "dashboard"):
        filename = "lib/" + name + ".py"
        if filename in files and name not in sys.modules:
            module = types.ModuleType(name)
            module.__file__ = str(Path(__file__).with_name(name + ".py"))
            sys.modules[name] = module
            try:
                exec(compile(files[filename], module.__file__, "exec"), module.__dict__)
            except BaseException:
                sys.modules.pop(name, None)
                raise


def dashboard_asset(filename):
    files = bundled_files()
    if files:
        return files["web/" + filename].encode("utf-8")
    return (Path(__file__).parent.parent / "web" / filename).read_bytes()


def data_root():
    return Path(os.environ.get("OUTBOUND_SPAM_DATA", "/var/lib/outbound-spam")).absolute()


def private_data():
    directory = data_root()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Directorio privado inseguro: " + str(directory))
    if os.geteuid() == 0:
        for parent in directory.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("Ancestro de datos inseguro: " + str(parent))
    return directory


def config_cache(path):
    path = Path(path).absolute()
    # El nombre habitual queda exactamente como outbound-spam.conf.last-good.json.
    return private_data() / (path.name + ".last-good.json")


LOGROTATE = """/var/log/outbound-spam.log {
        weekly
        missingok
        rotate 8
        compress
        delaycompress
        notifempty
        copytruncate
        dateext
}
"""


def log_path():
    return Path(os.environ.get("OUTBOUND_SPAM_LOG", "/var/log/outbound-spam.log"))


def open_log():
    path = log_path()
    # O_NOFOLLOW y validación antes de chmod: no seguir ni modificar archivos ajenos.
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        os.close(fd)
        raise ValueError("Log inseguro: " + str(path))
    os.fchmod(fd, 0o600)
    return fd


def install_logging(write_atomic):
    os.close(open_log())
    path = Path(os.environ.get("OUTBOUND_SPAM_LOGROTATE", "/etc/logrotate.d/outbound-spam"))
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Logrotate no debe ser un enlace: " + str(path))
    write_atomic(path, LOGROTATE, 0o600)


def audit(event, mode=None, **fields):
    try:
        _audit(event, mode, **fields)
    except (OSError, ValueError) as exc:
        # Un disco lleno/rotación fallida no debe convertir una contención hecha en fallida.
        print("outbound-spam: no se puede escribir el log: " + str(exc), file=sys.stderr)


def _audit(event, mode=None, **fields):
    mode = mode or ("servicio" if os.environ.get("OUTBOUND_SPAM_SERVICE") == "1" else "manual")
    record = dict(tipo=mode, pid=os.getpid(), evento=event, **fields)
    line = human_timestamp(time.time()) + " " + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = open_log()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = line.encode("utf-8")
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


DATE_FIELDS = {"last_success", "analysis_at", "created", "accepted", "opened", "event_time", "acked", "last_notice",
               "attempted", "held_at", "last_attempt", "installed_at", "heartbeat", "worker_started", "at", "cutoff"}


def human_timestamp(value):
    local = dt.datetime.fromtimestamp(value).astimezone()
    offset = local.strftime("%z")
    offset = offset[:3] + ":" + offset[3:] if len(offset) == 5 else offset
    return local.strftime("%Y-%m-%d %H:%M:%S %Z") + " (UTC" + offset + ")"


def readable_dates(value):
    """Copia para presentación; los epochs del estado interno siguen siendo numéricos."""
    if isinstance(value, list):
        return [readable_dates(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key.endswith("_utc") and key[:-4] + "_local" in value:
            continue
        if key in DATE_FIELDS and isinstance(item, (int, float)) and not isinstance(item, bool):
            result[key] = human_timestamp(item) if item else None
        elif isinstance(item, str) and key.endswith(("_iso", "_local", "_utc")):
            try:
                result[key] = human_timestamp(dt.datetime.fromisoformat(item.replace("Z", "+00:00")).timestamp())
            except ValueError:
                result[key] = item
        else:
            result[key] = readable_dates(item)
    return result


def check_private(path, ancestors=True):
    path = Path(path).absolute()
    first = True
    while True:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & (0o077 if first else 0o022):
            raise ValueError("Configuración/ruta no privada: " + str(path))
        if first and not stat.S_ISREG(info.st_mode):
            raise ValueError("La configuración no es un archivo regular")
        if not ancestors or path == Path("/") or os.geteuid() != 0:
            break
        first = False
        path = path.parent


def stable_bytes(path, pause=time.sleep):
    check_private(path)
    previous = None
    for _ in range(5):
        check_private(path)
        with open(path, "rb") as stream:
            before = os.fstat(stream.fileno())
            content = stream.read(1048577)
            after = os.fstat(stream.fileno())
        if after.st_uid not in (0, os.geteuid()) or after.st_mode & 0o077:
            raise ValueError("Configuración no privada")
        signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, content)
        if len(content) > 1048576:
            raise ValueError("Configuración mayor de 1 MiB")
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns and signature == previous:
            if not content.strip() or not re.search(rb"(?m)^\s*(?:declare\s+-A\s+|export\s+)?[A-Za-z_][A-Za-z0-9_]*=", content):
                raise ValueError("Configuración vacía o incompleta")
            return content
        previous = signature
        pause(0.15)
    raise ValueError("La configuración está cambiando durante la lectura")


def validate_config(content, base, parse):
    syntax = subprocess.run(["/bin/bash", "-n"], input=content, capture_output=True, timeout=10)
    if syntax.returncode:
        raise ValueError("Sintaxis Bash inválida")
    proc = subprocess.run(["/bin/bash", str(base / "lib/load_config.sh"), str(base / "outbound-spam.conf.example")],
                          input=content, capture_output=True, timeout=15)
    if proc.returncode:
        raise ValueError("No se puede evaluar la configuración Bash")
    return parse(proc.stdout), proc.stdout


def migrate_http_config(path, base, parse, write_atomic):
    path = Path(path)
    original = stable_bytes(path)
    text = original.decode("utf-8")
    additions = []
    for name, value in (("status_http_host", "127.0.0.1"), ("status_http_port", "23456")):
        if not re.search(r"(?m)^\s*(?:(?:export|declare\s+-[A-Za-z]+)\s+)?" + name + r"\s*=", text):
            additions.append(name + "=" + value)
    if not additions:
        return []
    updated = original + (b"" if original.endswith(b"\n") else b"\n") + ("\n# Estado HTTP del servicio\n" + "\n".join(additions) + "\n").encode()
    validate_config(updated, base, parse)
    if stable_bytes(path) != original:
        raise ValueError("La configuración cambió durante la migración HTTP")
    write_atomic(path, updated.decode("utf-8"))
    return additions


CONFIG_BEGIN = "# BEGIN OUTBOUND-SPAM CONFIG COMPLETION"
CONFIG_END = "# END OUTBOUND-SPAM CONFIG COMPLETION"


def assigned_names(text):
    """Nombres explícitos; las ampliaciones se anteponen para respetar también Bash dinámico."""
    return set(re.findall(r"(?m)^\s*(?:(?:declare\s+(?:-[A-Za-z]+|--)|export|readonly)\s+)?"
                          r"([A-Za-z_][A-Za-z0-9_]*)\s*\+?=", text))


def example_declarations(base):
    """El ejemplo versionado es el inventario autoritativo, incluidos arrays Bash."""
    example = Path(base) / "outbound-spam.conf.example"
    names = sorted(assigned_names(example.read_text(encoding="utf-8")))
    if not names:
        raise ValueError("El ejemplo de configuración no contiene variables")
    script = 'set -euo pipefail; source "$1" >/dev/null; shift; for name; do declare -p "$name"; done'
    result = subprocess.run(["/bin/bash", "-c", script, "outbound-spam", str(example)] + names,
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("No se pueden obtener los valores predeterminados del ejemplo")
    declarations = {}
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"declare (?:--|-[A-Za-z]+) ([A-Za-z_][A-Za-z0-9_]*)=.*", line)
        if not match or match[1] in declarations:
            raise ValueError("Declaración del ejemplo no representable en una línea Bash")
        declarations[match[1]] = line[len("declare -- "):] if line.startswith("declare -- ") else line
    if set(declarations) != set(names):
        raise ValueError("Inventario de variables del ejemplo incompleto")
    return declarations


def completed_config_matches(original, current):
    """Reconocer nuestra ampliación exacta sin deshacer ediciones externas."""
    pattern = (re.escape((CONFIG_BEGIN + "\n# sha256: ").encode()) + rb"([a-f0-9]{64})\n(.*?)" +
               re.escape((CONFIG_END + "\n\n").encode()))
    match = re.match(pattern, current, re.S)
    return bool(match and hashlib.sha256(match[2]).hexdigest().encode() == match[1]
                and current[match.end():] == original)


def migrate_config(path, base, parse, write_atomic):
    """Completar todas las variables ausentes conservando contenido y valores efectivos."""
    path, base = Path(path).absolute(), Path(base)
    original = stable_bytes(path)
    config, _ = validate_config(original, base, parse)
    declarations = example_declarations(base)
    added = sorted(set(declarations) - assigned_names(original.decode("utf-8")))
    if not added:
        return {"added": [], "backup": None}
    body = ("\n".join(declarations[name] for name in added) + "\n").encode("utf-8")
    prefix = ((CONFIG_BEGIN + "\n# sha256: " + hashlib.sha256(body).hexdigest() + "\n").encode() +
              body + (CONFIG_END + "\n\n").encode())
    # Anteponer es importante: asignaciones dinámicas, += y bloques del usuario
    # siguen teniendo la última palabra, como al cargar el ejemplo antes del .conf.
    updated = prefix + original
    candidate, serialized = validate_config(updated, base, parse)
    if candidate.v != config.v or set(declarations) - assigned_names(updated.decode("utf-8")):
        raise ValueError("La ampliación de configuración cambia valores existentes o deja variables ausentes")
    cache = config_cache(path)
    if cache.exists() or cache.is_symlink():
        check_private(cache)
    old_cache = cache.read_text() if cache.exists() else None
    backup = private_data() / ("config-completion-backup-%d-%s.json" % (time.time_ns(), secrets.token_hex(4)))
    write_atomic(backup, json.dumps({"config_path": str(path), "config": original.decode("utf-8"),
                                    "cache": old_cache, "added": added}, ensure_ascii=False))
    if stable_bytes(path) != original:
        raise ValueError("La configuración cambió durante su ampliación automática")
    try:
        write_atomic(path, updated.decode("utf-8"))
        write_atomic(cache, json.dumps(config_record(path, updated, serialized)))
    except Exception:
        if path.is_file() and completed_config_matches(original, path.read_bytes()):
            write_atomic(path, original.decode("utf-8"))
            if old_cache is not None:
                write_atomic(cache, old_cache)
            elif cache.exists():
                cache.unlink()
        raise
    audit("configuracion_completada", config=str(path), variables=added, respaldo=str(backup))
    return {"added": added, "backup": str(backup)}


def rollback_config_completion(path, original, base, parse, write_atomic):
    path = Path(path)
    if path.is_file() and not path.is_symlink() and completed_config_matches(original, path.read_bytes()):
        _, serialized = validate_config(original, base, parse)
        write_atomic(path, original.decode("utf-8"))
        write_atomic(config_cache(path), json.dumps(config_record(path, original, serialized)))


def config_record(path, content, serialized):
    return {"path": str(Path(path).absolute()), "data": base64.b64encode(serialized).decode("ascii"),
            "sha256": hashlib.sha256(serialized).hexdigest(), "source_sha256": hashlib.sha256(content).hexdigest()}


def load_config(path, base, parse, write_atomic, cache_path=None):
    path = Path(path).absolute()
    cache = Path(cache_path) if cache_path is not None else config_cache(path)
    legacy_cache = Path(str(path) + ".last-good.json")
    if not cache.exists() and legacy_cache.exists():
        check_private(legacy_cache)
        saved = json.loads(legacy_cache.read_text())
        if saved.get("path") == str(path):
            write_atomic(cache, json.dumps(saved))
    try:
        content = stable_bytes(path)
        config, serialized = validate_config(content, base, parse)
        record = config_record(path, content, serialized)
        old = None
        if cache.exists():
            check_private(cache)
            try:
                old = json.loads(cache.read_text())
            except ValueError:
                pass
        if old != record:
            write_atomic(cache, json.dumps(record))
        return config, None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        try:
            check_private(cache)
            saved = json.loads(cache.read_text())
            data = base64.b64decode(saved["data"], validate=True)
            if saved["path"] != str(path) or hashlib.sha256(data).hexdigest() != saved["sha256"]:
                raise ValueError("Caché de configuración no verificable")
            config = parse(data)
        except Exception:
            raise ValueError("Configuración no válida y sin última copia válida: " + str(path)) from exc
        return config, "Configuración no válida; se utiliza la última versión validada: " + str(path)


def http_status(directory, stale_after):
    now = time.time()
    try:
        def reject_constant(value):
            raise ValueError("valor JSON no finito: " + value)
        summary = json.loads((directory / "status.json").read_text(encoding="utf-8"), parse_constant=reject_constant)
        if not isinstance(summary, dict) or type(summary.get("code")) is not int or summary["code"] not in (0, 1, 2):
            raise ValueError("código de estado inválido")
        last = summary["last_success"]
        limit = summary["stale_after"]
        if (isinstance(last, bool) or isinstance(limit, bool) or not isinstance(last, (int, float))
                or not isinstance(limit, (int, float)) or not math.isfinite(last) or not math.isfinite(limit)):
            raise ValueError("fechas de estado inválidas")
        age = now - last
        if not (-60 <= age <= limit) or limit <= 0:
            summary["code"] = 2
            summary["message"] = "Spam Status: ERROR: analizador atrasado o reloj incoherente"
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        summary = {"code": 2, "message": "Spam Status: ERROR: " + str(exc), "last_success": 0, "stale_after": stale_after}
        age = -1
    summary["http_metrics"] = {
        "code": summary["code"], "last_success_age_seconds": max(0, age) if age != -1 else -1,
        "warming_up": int(bool(summary.get("warming_up", False))),
        "incidents": len(summary.get("incidents", [])) if isinstance(summary.get("incidents"), list) else 0,
        "held": summary.get("held", 0) if type(summary.get("held", 0)) is int else 0,
        "blocked": len(summary.get("blocked", [])) if isinstance(summary.get("blocked"), list) else 0,
        "issues": len(summary.get("issues", [])) if isinstance(summary.get("issues"), list) else 0,
    }
    return summary


class StatusHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def start_status_http(config, directory):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                if getattr(config, "v", config).get("analytics_enabled", False):
                    from dashboard import serve
                    if serve(self, directory):
                        return
                self.send_error(404)
                return
            body = json.dumps(http_status(directory, config["stale_after"]), ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = StatusHTTPServer((config["status_http_host"], config["status_http_port"]), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def daemon_loop(config, base, config_path, write_atomic, max_cycles=None, spawn=None, wait=None):
    directory = Path(config["state_dir"])
    lock_path = directory / "daemon.lock"
    if lock_path.exists() or lock_path.is_symlink():
        info = lock_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Bloqueo de servicio no privado/regular")
    spawn = subprocess.Popen if spawn is None else spawn
    stopping = [False]
    http_server = None
    stats_child = None
    stats_retry = [0]
    stats_enabled = getattr(config, "v", config).get("analytics_enabled", False)
    def supervise_stats():
        nonlocal stats_child
        if not stats_enabled or stopping[0]:
            return
        if stats_child is not None and stats_child.poll() is not None:
            audit("estadisticas_terminadas", codigo=stats_child.returncode)
            stats_child = None
        if stats_child is None and time.monotonic() >= stats_retry[0]:
            stats_retry[0] = time.monotonic() + 60
            try:
                stats_child = subprocess.Popen([sys.executable, str(Path(__file__)), "--analytics-worker", str(directory)],
                                               env=dict(os.environ, OUTBOUND_SPAM_SERVICE="1"))
            except OSError as exc:
                audit("estadisticas_arranque_error", error=str(exc))
    old_handlers = {}
    def stop(signum, frame):
        stopping[0] = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, stop)
    try:
        with open(lock_path, "a") as lock:
            os.chmod(str(lock_path), 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Ya hay otro demonio para este estado")
            http_server, http_thread = start_status_http(config, directory)
            audit("http_iniciado", host=config["status_http_host"], puerto=config["status_http_port"])
            cycles = 0
            audit("servicio_iniciado", mode="servicio", intervalo=config["run_interval"], estado=str(directory))
            while not stopping[0] and (max_cycles is None or cycles < max_cycles):
                supervise_stats()
                started = time.time()
                command = ["/bin/bash", str(base / "outbound-spam.sh"), "--config", config_path,
                           "--state-dir", str(directory), "--run", "--quiet", "--lock-wait", str(config["lock_wait"])]
                environment = dict(os.environ, OUTBOUND_SPAM_SERVICE="1")
                child = spawn(command) if spawn is not subprocess.Popen else spawn(command, env=environment)
                last_heartbeat = float("-inf")
                while True:
                    supervise_stats()
                    result = child.poll()
                    if result is not None or time.monotonic() - last_heartbeat >= 2:
                        write_atomic(directory / "service-status.json", json.dumps({"pid": os.getpid(), "worker_pid": child.pid,
                                     "heartbeat": time.time(), "worker_started": started, "active": result is None,
                                     "last_result": result}))
                        last_heartbeat = time.monotonic()
                    if result is not None:
                        break
                    # SIGTERM detiene la siguiente pasada; systemd concede tiempo al hijo.
                    time.sleep(0.2)
                cycles += 1
                interval = config["run_interval"]
                try:
                    summary = json.loads((directory / "status.json").read_text())
                    interval = max(1, int(summary.get("run_interval", interval)))
                except (OSError, ValueError, TypeError):
                    pass
                if not stopping[0] and (max_cycles is None or cycles < max_cycles):
                    if wait:
                        wait(interval)
                    else:
                        until = time.monotonic() + interval
                        while not stopping[0] and time.monotonic() < until:
                            supervise_stats()
                            time.sleep(min(0.2, max(0, until - time.monotonic())))
            write_atomic(directory / "service-status.json", json.dumps({"pid": os.getpid(), "heartbeat": time.time(), "active": False, "stopped": True}))
            audit("servicio_detenido", mode="servicio", pasadas=cycles)
    finally:
        if stats_child is not None:
            if stats_child.poll() is None:
                stats_child.terminate()
            try:
                stats_child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                stats_child.kill()
                stats_child.wait()
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
            http_thread.join()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    return 0


def render_service(config, base, config_path, exec_start=None):
    for value in (str(base), config_path, config["state_dir"]):
        if re.search(r"[^A-Za-z0-9_./-]", value):
            raise ValueError("Las rutas del servicio no pueden contener espacios/metacaracteres")
    command = exec_start or "/bin/bash %s/outbound-spam.sh --config %s --state-dir %s --daemon" % (base, config_path, config["state_dir"])
    if "\n" in command or "\r" in command:
        raise ValueError("ExecStart inválido")
    return """# outbound-spam: generado por --install-service
[Unit]
Description=Outbound authenticated spam monitor
After=network.target postfix.service

[Service]
Type=simple
User=root
UMask=0077
Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
Environment=OUTBOUND_SPAM_SERVICE=1
ExecStart=%s
Restart=on-failure
RestartSec=60
KillMode=mixed
TimeoutStopSec=600

[Install]
WantedBy=multi-user.target
""" % command


def install_service(config, commands, base, config_path, write_atomic):
    path = Path(config["service_unit"])
    if not path.parent.is_dir():
        raise ValueError("No existe el directorio de unidades systemd")
    if path.is_symlink():
        raise ValueError("La unidad no debe ser un enlace ajeno")
    if path.exists() and "# outbound-spam" not in path.read_text():
        raise ValueError("La unidad existente no pertenece a outbound-spam")
    if os.geteuid() == 0:
        code = base / "outbound-spam.sh"
        while True:
            info = code.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("Instale el código en una ruta de root no modificable por otros: " + str(code))
            if code == Path("/"):
                break
            code = code.parent
    text = render_service(config, base, config_path)
    previous = path.read_text() if path.exists() else None
    try:
        write_atomic(path, text, 0o644)
        commands.run("systemctl", ["daemon-reload"])
        commands.run("systemctl", ["enable", path.name])
        commands.run("systemctl", ["restart", path.name])
    except Exception:
        if previous is None:
            try:
                commands.run("systemctl", ["disable", "--now", path.name])
            except Exception:
                pass
        if previous is None:
            if path.exists():
                path.unlink()
        else:
            write_atomic(path, previous, 0o644)
        commands.run("systemctl", ["daemon-reload"])
        raise
    return "Servicio instalado y habilitado; espera %d s al terminar cada pasada" % config["run_interval"]


def uninstall_service(config, commands):
    path = Path(config["service_unit"])
    if not path.exists():
        return "Servicio no instalado"
    if path.is_symlink() or "# outbound-spam" not in path.read_text():
        raise ValueError("La unidad no pertenece a outbound-spam")
    commands.run("systemctl", ["disable", "--now", path.name])
    path.unlink()
    commands.run("systemctl", ["daemon-reload"])
    return "Servicio retirado; se conserva estado, bloqueos y retenciones"


install_bundled_modules()

if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--analytics-worker":
        raise SystemExit("Uso interno: runtime.py --analytics-worker DIRECTORIO")
    import analytics
    analytics.worker(sys.argv[2])

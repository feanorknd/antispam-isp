"""Configuración estable, bucle secuencial e integración systemd."""
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time


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


def load_config(path, base, parse, write_atomic):
    path = Path(path).absolute()
    cache = Path(str(path) + ".last-good.json")
    try:
        content = stable_bytes(path)
        syntax = subprocess.run(["/bin/bash", "-n"], input=content, capture_output=True, timeout=10)
        if syntax.returncode:
            raise ValueError("Sintaxis Bash inválida")
        proc = subprocess.run(["/bin/bash", str(base / "lib/load_config.sh"), str(base / "outbound-spam.conf.example")],
                              input=content, capture_output=True, timeout=15)
        if proc.returncode:
            raise ValueError("No se puede evaluar la configuración Bash")
        config = parse(proc.stdout)
        encoded = base64.b64encode(proc.stdout).decode("ascii")
        record = {"path": str(path), "data": encoded, "sha256": hashlib.sha256(proc.stdout).hexdigest(),
                  "source_sha256": hashlib.sha256(content).hexdigest()}
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


def daemon_loop(config, base, config_path, write_atomic, max_cycles=None, spawn=None, wait=None):
    directory = Path(config["state_dir"])
    lock_path = directory / "daemon.lock"
    if lock_path.exists() or lock_path.is_symlink():
        info = lock_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Bloqueo de servicio no privado/regular")
    spawn = subprocess.Popen if spawn is None else spawn
    stopping = [False]
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
            cycles = 0
            while not stopping[0] and (max_cycles is None or cycles < max_cycles):
                started = time.time()
                command = ["/bin/bash", str(base / "outbound-spam.sh"), "--config", config_path,
                           "--state-dir", str(directory), "--run", "--quiet", "--lock-wait", str(config["lock_wait"])]
                child = spawn(command)
                last_heartbeat = float("-inf")
                while True:
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
                            time.sleep(min(0.2, max(0, until - time.monotonic())))
            write_atomic(directory / "service-status.json", json.dumps({"pid": os.getpid(), "heartbeat": time.time(), "active": False, "stopped": True}))
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    return 0


def managed_cron_remove(config):
    path = Path(config["cron_file"])
    if not path.exists():
        return None
    if path.is_symlink() or "# outbound-spam" not in path.read_text():
        raise ValueError("El cron no pertenece a outbound-spam: " + str(path))
    content = path.read_text()
    path.unlink()
    return content


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
    cron = managed_cron_remove(config)
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
        if cron is not None:
            write_atomic(Path(config["cron_file"]), cron, 0o644)
        commands.run("systemctl", ["daemon-reload"])
        raise
    return "Servicio instalado y habilitado; espera %d s al terminar cada pasada; cron gestionado retirado" % config["run_interval"]


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

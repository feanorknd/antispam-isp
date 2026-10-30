"""Instalación versionada y actualizaciones desde los releases públicos de GitHub."""
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

PROJECT = "feanorknd/antispam-isp"
LATEST = "https://api.github.com/repos/" + PROJECT + "/releases/latest"
RUNTIME = (
    "VERSION", "install.sh", "outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example",
    "lib/outbound_spam.py", "lib/postfix_settings.py", "lib/runtime.py", "lib/state_recovery.py",
    "lib/load_config.sh", "lib/distribution.py",
)
ROOT_FILES = {"README.md"} | set(RUNTIME)
MAX_ARCHIVE = 10 * 1024 * 1024
MAX_UNPACKED = 20 * 1024 * 1024


def version(value):
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", str(value))
    if not match:
        raise ValueError("Versión estable no válida: " + str(value))
    return tuple(map(int, match.groups()))


def allowed(name):
    return name in ROOT_FILES or bool(re.fullmatch(r"docs/[A-Za-z0-9_.-]+\.(md|json)", name))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write(path, text, mode=0o600):
    path = Path(path)
    temp = path.with_name("." + path.name + "-" + secrets.token_hex(5))
    fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp), str(path))
        sync_directory(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


def sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def protected_directory(path, create=False):
    path = Path(os.path.abspath(str(path)))
    if not path.exists() and create:
        protected_directory(path.parent)
        path.mkdir(mode=0o700)
    current = path
    while True:
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise ValueError("Ruta de instalación insegura: " + str(current))
        if current == Path("/") or os.geteuid() != 0:
            break
        current = current.parent
    return path


def make_manifest(source):
    source = Path(source)
    number = (source / "VERSION").read_text().strip()
    version(number)
    names = list(RUNTIME) + ["README.md"]
    names += [p.relative_to(source).as_posix() for p in sorted((source / "docs").glob("*")) if p.is_file() and allowed(p.relative_to(source).as_posix())]
    files = {}
    for name in names:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Falta un componente regular: " + name)
        files[name] = digest(path)
    return {"project": PROJECT, "format": 1, "upgrade_api": 1, "version": number,
            "schema_version": 3, "python_min": [3, 8], "files": files}


def validate_tree(source, expected=None):
    source = Path(source)
    manifest_path = source / "RELEASE.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else make_manifest(source)
    if manifest.get("project") != PROJECT or manifest.get("format") != 1 or manifest.get("upgrade_api") != 1:
        raise ValueError("El paquete no pertenece al proyecto o usa un formato no compatible")
    number = manifest["version"]
    version(number)
    if tuple(manifest.get("python_min", [3, 8])) > sys.version_info[:2]:
        raise ValueError("El release necesita una versión de Python más moderna")
    if expected is not None and version(number) != version(expected):
        raise ValueError("La versión del paquete no coincide con el release")
    files = manifest["files"]
    if not isinstance(files, dict) or set(RUNTIME) - set(files):
        raise ValueError("El paquete no contiene todos los componentes")
    for name, checksum in files.items():
        if not allowed(name) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Componente no permitido en el paquete: " + name)
        path = source / name
        if path.is_symlink() or not path.is_file() or digest(path) != checksum:
            raise ValueError("No coincide el checksum de " + name)
    if (source / "VERSION").read_text().strip() != number:
        raise ValueError("VERSION no coincide con RELEASE.json")
    tree = ast.parse((source / "lib/outbound_spam.py").read_text())
    constants = [n.value.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in n.targets) and isinstance(n.value, ast.Constant)]
    if constants != [number]:
        raise ValueError("La versión del motor no coincide con el paquete")
    for name in files:
        if name.endswith(".py"):
            compile((source / name).read_bytes(), name, "exec")
        elif name.endswith(".sh") or name.endswith(".conf.example"):
            proc = subprocess.run(["/bin/bash", "-n", str(source / name)], capture_output=True, timeout=10)
            if proc.returncode:
                raise ValueError("Componente Bash inválido: " + name)
    return manifest


def unpack(archive, destination, expected):
    prefix = "antispam-isp-" + expected.lstrip("v")
    destination = Path(destination)
    total, seen = 0, set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = member.name.rstrip("/")
            parts = PurePosixPath(name).parts
            if not parts or name != PurePosixPath(name).as_posix() or parts[0] != prefix or any(p in ("..", ".") for p in parts) or "\\" in name or name.startswith("/") or name in seen:
                raise ValueError("Ruta no permitida o duplicada en el archivo")
            seen.add(name)
            relative = "/".join(parts[1:])
            if member.isdir():
                if relative and relative not in ("lib", "docs"):
                    raise ValueError("Directorio no permitido")
                (destination / name).mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            if not member.isfile() or (relative != "RELEASE.json" and not allowed(relative)):
                raise ValueError("Tipo o componente no permitido en el archivo")
            if member.size < 0:
                raise ValueError("Tamaño TAR inválido")
            total += member.size
            if total > MAX_UNPACKED:
                raise ValueError("Paquete descomprimido excesivo")
            target = destination / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("xb") as stream:
                shutil.copyfileobj(source, stream)
            target.chmod(0o600)
    root = destination / prefix
    manifest = validate_tree(root, expected)
    expected_files = {prefix + "/" + n for n in manifest["files"]} | {prefix + "/RELEASE.json"}
    actual_files = {p.relative_to(destination).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual_files != expected_files:
        raise ValueError("Contenido inesperado en el archivo")
    return root


def fetch(url, limit):
    if not url.startswith("https://"):
        raise ValueError("La descarga requiere HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "antispam-isp-upgrade", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if not response.geturl().startswith("https://"):
                raise ValueError("Redirección fuera de HTTPS")
            data = response.read(limit + 1)
    except urllib.error.URLError as exc:
        raise ValueError("No se puede consultar/descargar el release de GitHub: " + str(exc)) from exc
    if len(data) > limit:
        raise ValueError("Respuesta de descarga excesiva")
    return data


def latest_release(current, downloader=fetch):
    release = json.loads(downloader(LATEST, 1024 * 1024))
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("El release no es estable")
    tag = release["tag_name"]
    version(tag)
    number = tag[1:] if tag.startswith("v") else tag
    if version(number) <= version(current):
        return None
    name = "antispam-isp-" + number + ".tar.gz"
    assets = {a["name"]: a for a in release.get("assets", [])}
    if name not in assets or "SHA256SUMS" not in assets:
        raise ValueError("El release carece del paquete instalable o sus checksums")
    urls = {}
    for key in (name, "SHA256SUMS"):
        expected_url = "https://github.com/%s/releases/download/%s/%s" % (PROJECT, tag, key)
        if assets[key].get("browser_download_url") != expected_url:
            raise ValueError("El asset no corresponde al repositorio oficial")
        urls[key] = expected_url
    return number, name, urls


def _copy_release(source, target, manifest):
    root = target / ".antispam"
    releases = root / "releases"
    releases.mkdir(mode=0o700, exist_ok=True)
    final = releases / manifest["version"]
    if final.exists():
        installed = validate_tree(final, manifest["version"])
        if installed["files"] != manifest["files"]:
            raise ValueError("Ya hay otra distribución con esa misma versión")
        return final
    temporary = releases / (".install-" + secrets.token_hex(6))
    temporary.mkdir(mode=0o700)
    try:
        for name in manifest["files"]:
            dest = temporary / name
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(str(source / name), str(dest))
            dest.chmod(0o700 if name in ("install.sh", "outbound-spam.sh", "outbound-spam-status.sh") else 0o600)
        write(temporary / "RELEASE.json", json.dumps(manifest, indent=2))
        validate_tree(temporary, manifest["version"])
        os.replace(str(temporary), str(final))
        sync_directory(releases)
    finally:
        if temporary.exists():
            shutil.rmtree(str(temporary))
    return final


def _activate(target, release):
    root = target / ".antispam"
    link = root / (".current-" + secrets.token_hex(5))
    link.symlink_to("releases/" + release.name)
    os.replace(str(link), str(root / "current"))
    sync_directory(root)


def _service_active(config, commands):
    path = Path(config["service_unit"])
    if not path.exists():
        return False
    if path.is_symlink() or "# outbound-spam" not in path.read_text():
        raise ValueError("No se modifica una unidad de servicio ajena")
    return commands.run("systemctl", ["is-active", path.name], ok=(0, 3, 4)).strip() == "active"


def _snapshot_state(config, config_path, backup):
    state = Path(config["state_dir"])
    for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm", "status.json", "recovery"):
        path = state / name
        if path.is_symlink():
            raise ValueError("El estado no debe contener enlaces administrados")
        if path.is_dir():
            shutil.copytree(str(path), str(backup / name), symlinks=True)
        elif path.exists():
            shutil.copy2(str(path), str(backup / name))
    cache = Path(str(config_path) + ".last-good.json")
    if cache.exists():
        shutil.copy2(str(cache), str(backup / "config.last-good.json"))


def _restore_state(config, config_path, backup):
    failed = backup / "failed-state"
    failed.mkdir(mode=0o700, exist_ok=True)
    state = Path(config["state_dir"])
    for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm", "status.json", "recovery"):
        current, saved = state / name, backup / name
        if current.exists():
            os.replace(str(current), str(failed / name))
        if saved.is_dir():
            shutil.copytree(str(saved), str(current), symlinks=True)
        elif saved.exists():
            shutil.copy2(str(saved), str(current))
    cache = Path(str(config_path) + ".last-good.json")
    if (backup / "config.last-good.json").exists():
        shutil.copy2(str(backup / "config.last-good.json"), str(cache))
    elif cache.exists():
        os.replace(str(cache), str(failed / "config.last-good.json"))


def _owned_entrypoints(target, source):
    launcher = target / "outbound-spam.sh"
    if launcher.is_symlink():
        raise ValueError("El lanzador existente no debe ser un enlace")
    if launcher.exists():
        text = launcher.read_text()
        if "OUTBOUND_SPAM_BASE" not in text or "outbound_spam.py" not in text:
            raise ValueError("El lanzador existente no pertenece a esta aplicación")
    for name in ("outbound-spam-status.sh", "outbound-spam.conf.example"):
        path = target / name
        if path.is_symlink():
            raise ValueError("Componente existente no administrable: " + name)
        if path.exists() and "outbound-spam" not in path.read_text():
            raise ValueError("Componente existente ajeno: " + name)


def _install_entrypoints(source, target):
    for name in ("outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example"):
        write(target / name, (source / name).read_text(), 0o600 if name.endswith(".example") else 0o700)


def _prepare(source, config, config_path, lock_fd):
    command = ["/bin/bash", str(source / "outbound-spam.sh"), "--config", str(config_path),
               "--state-dir", config["state_dir"], "--prepare-upgrade", "--upgrade-lock-fd", str(lock_fd)]
    result = subprocess.run(command, pass_fds=(lock_fd,), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError("La preparación de la actualización ha fallado: " + result.stderr.strip()[:300])


def deploy(source, target, config_path=None, config=None, commands=None, preparer=_prepare):
    source, target = Path(source).absolute(), protected_directory(target, create=True)
    if source == target:
        raise ValueError("Instale en una ruta distinta del paquete/repo de origen")
    manifest = validate_tree(source)
    root = protected_directory(target / ".antispam", create=True)
    _owned_entrypoints(target, source)
    existing = (target / "outbound-spam.sh").exists()
    config_path = Path(config_path or target / "outbound-spam.conf").absolute()
    protected_directory(config_path.parent, create=True)
    if config_path.exists() or config_path.is_symlink():
        info = config_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("La configuración existente debe ser privada y pertenecer al usuario ejecutor")
    if existing and config is None:
        config, commands = installed_context(source, config_path)
    with (root / "upgrade.lock").open("a") as upgrade_lock:
        os.chmod(str(root / "upgrade.lock"), 0o600)
        try:
            fcntl.flock(upgrade_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Ya hay otra instalación/actualización en curso")
        was_active, started, changed, state_saved = False, False, False, False
        old_link = os.readlink(str(root / "current")) if (root / "current").is_symlink() else None
        if (root / "current").exists() and old_link is None:
            raise ValueError("La ruta current no es un enlace gestionado")
        backup = root / ("upgrade-backup-%d-%s" % (time.time(), secrets.token_hex(4)))
        backup.mkdir(mode=0o700)
        old_entries = {}
        for name in ("outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example", ".antispam/installation.json"):
            path = target / name
            if path.exists():
                old_entries[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        operation_lock = None
        try:
            if existing:
                was_active = _service_active(config, commands)
                if was_active:
                    commands.run("systemctl", ["stop", Path(config["service_unit"]).name])
                state = protected_directory(config["state_dir"], create=True)
                daemon_lock = state / "daemon.lock"
                with daemon_lock.open("a") as probe:
                    daemon_lock.chmod(0o600)
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise ValueError("Hay un demonio manual activo; deténgalo antes de actualizar")
                operation_lock = (state / "lock").open("a")
                (state / "lock").chmod(0o600)
                deadline = time.monotonic() + config["lock_wait"]
                while True:
                    try:
                        fcntl.flock(operation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ValueError("Una operación manual impide actualizar")
                        time.sleep(0.1)
                _snapshot_state(config, config_path, backup)
                state_saved = True
            release = _copy_release(source, target, manifest)
            if existing:
                preparer(release, config, config_path, operation_lock.fileno())
            changed = True
            _install_entrypoints(release, target)
            _activate(target, release)
            if not config_path.exists():
                initial = (release / "outbound-spam.conf.example").read_text()
                initial = initial.replace("report=(admin@example.org)", "report=()")
                initial = initial.replace("notify=true", "notify=false")
                initial = re.sub(r"(?m)^declare -A custom_limits=.*$", "declare -A custom_limits=()", initial)
                write(config_path, initial)
            write(root / "installation.json", json.dumps({"project": PROJECT, "version": manifest["version"],
                  "target": str(target), "config": str(config_path), "installed_at": time.time()}))
            if existing and Path(config["monit_script"]).exists():
                if "outbound-spam" not in Path(config["monit_script"]).read_text():
                    raise ValueError("El comprobador Monit instalado es ajeno")
                write(Path(config["monit_script"]), (release / "outbound-spam-status.sh").read_text(), 0o700)
            if was_active:
                # A partir de aquí el nuevo código puede ejecutar acciones. No rebobinar SQL.
                started = True
                commands.run("systemctl", ["start", Path(config["service_unit"]).name])
            return {"version": manifest["version"], "target": str(target), "config": str(config_path),
                    "service_restarted": was_active, "backup": str(backup)}
        except Exception:
            if not started:
                if existing and state_saved:
                    _restore_state(config, config_path, backup)
                if changed:
                    if old_link:
                        temp = root / (".rollback-" + secrets.token_hex(4))
                        temp.symlink_to(old_link)
                        os.replace(str(temp), str(root / "current"))
                    elif (root / "current").is_symlink():
                        (root / "current").unlink()
                    for name in ("outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example", ".antispam/installation.json"):
                        path = target / name
                        if name in old_entries:
                            value, mode = old_entries[name]
                            write(path, value.decode("utf-8"), mode)
                        elif path.exists():
                            path.unlink()
                if was_active:
                    commands.run("systemctl", ["start", Path(config["service_unit"]).name])
            else:
                raise ValueError("Versión instalada, pero falló el arranque del servicio. Se conserva el estado actual; revise systemctl. Respaldo: " + str(backup))
            raise
        finally:
            if operation_lock is not None:
                operation_lock.close()


def installed_context(source, config_path):
    import runtime
    from outbound_spam import Config, Commands, MonitorError
    def parse(data):
        try:
            return Config.from_bytes(data)
        except MonitorError as exc:
            raise ValueError(str(exc)) from exc
    config, warning = runtime.load_config(config_path, source, parse, write)
    config.v["command_timeout"] = max(660, config["command_timeout"])
    return config, Commands(config)


def upgrade(target, current, config_path, config, commands, downloader=fetch, preparer=_prepare):
    target = protected_directory(target)
    record = target / ".antispam/installation.json"
    if not record.is_file() or record.is_symlink():
        raise ValueError("Instalación sin gestor de releases; ejecute primero install.sh de la distribución oficial")
    installation = json.loads(record.read_text())
    if installation.get("project") != PROJECT or installation.get("target") != str(target):
        raise ValueError("La instalación no corresponde a este proyecto/ruta")
    release = latest_release(current, downloader)
    if release is None:
        return {"updated": False, "version": current, "message": "Ya está instalada la última versión estable"}
    number, name, urls = release
    root = target / ".antispam"
    with tempfile.TemporaryDirectory(prefix="download-", dir=str(root)) as folder:
        staging = Path(folder)
        checksums = downloader(urls["SHA256SUMS"], 65536).decode("ascii")
        matches = [line.split()[0] for line in checksums.splitlines() if len(line.split()) == 2 and line.split()[1].lstrip("*") == name]
        if len(matches) != 1 or not re.fullmatch(r"[a-f0-9]{64}", matches[0]):
            raise ValueError("No hay un checksum único para el paquete")
        archive = staging / name
        data = downloader(urls[name], MAX_ARCHIVE)
        if hashlib.sha256(data).hexdigest() != matches[0]:
            raise ValueError("Checksum del release incorrecto; se conserva la versión instalada")
        archive.write_bytes(data)
        source = unpack(archive, staging / "unpacked", number)
        result = deploy(source, target, config_path, config, commands, preparer)
        return dict(result, updated=True)


def main():
    p = argparse.ArgumentParser(description="Instalador del release antispam-isp")
    p.add_argument("action", choices=["install"])
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--target", default="/root/scripts", type=Path)
    p.add_argument("--config", type=Path)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("La instalación requiere root")
    os.umask(0o077)
    print(json.dumps(deploy(args.source, args.target, args.config), ensure_ascii=False, indent=2))
    print("Configure %s; después ejecute --doctor y los instaladores de guard/servicio/Monit." % (args.config or args.target / "outbound-spam.conf"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit("Instalación: " + str(exc))

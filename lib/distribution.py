"""Instalación versionada y actualizaciones desde los releases públicos de GitHub."""
import argparse
import ast
import contextlib
import fcntl
import hashlib
import importlib.util
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
import runtime

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
            "schema_version": 4, "python_min": [3, 8], "service_unit_api": 1, "files": files}


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
    prefixes = {name + expected.lstrip("v") for name in ("outbound-spam-", "antispam-isp-")}
    prefix = None
    destination = Path(destination)
    total, seen = 0, set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = member.name.rstrip("/")
            parts = PurePosixPath(name).parts
            if prefix is None and parts and parts[0] in prefixes:
                prefix = parts[0]
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


def migrate_legacy(target, config_path):
    """Traslado bajo los bloqueos de upgrade/operación; alias solo mientras acaba 1.0.x."""
    target = Path(target)
    legacy, root = target / ".antispam", runtime.private_data()
    if not legacy.exists() or legacy.is_symlink():
        return
    protected_directory(legacy)
    old_cache = Path(str(config_path) + ".last-good.json")
    cache = runtime.config_cache(config_path)
    if old_cache.exists() and not cache.exists():
        runtime.check_private(old_cache)
        write(cache, old_cache.read_text())
    lock_info = (legacy / "upgrade.lock").stat()
    names = [p.name for p in legacy.iterdir()]
    for name in names:
        if (root / name).exists() or (root / name).is_symlink():
            raise ValueError("Conflicto al migrar almacenamiento: " + str(root / name))
    marker = root / "layout-migration.json"
    write(marker, json.dumps({"target": str(target), "config": str(config_path), "names": names}))
    moved = []
    try:
        for name in names:
            shutil.move(str(legacy / name), str(root / name))
            moved.append(name)
        legacy.rmdir()
        legacy.symlink_to(root, target_is_directory=True)
        sync_directory(target)
    except Exception:
        if legacy.is_symlink():
            legacy.unlink()
        legacy.mkdir(mode=0o700, exist_ok=True)
        for name in reversed(moved):
            shutil.move(str(root / name), str(legacy / name))
        marker.unlink()
        raise
    runtime.audit("rutas_migradas", origen=str(legacy), destino=str(root))
    new_info = (root / "upgrade.lock").stat()
    if (lock_info.st_dev, lock_info.st_ino) != (new_info.st_dev, new_info.st_ino):
        fd = os.open(str(root / "upgrade.lock"), os.O_RDWR | os.O_NOFOLLOW)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    return None


def finish_legacy(target, success):
    root, target = runtime.private_data(), Path(target)
    marker = root / "layout-migration.json"
    if not marker.exists():
        return
    saved = json.loads(marker.read_text())
    if saved["target"] != str(target):
        raise ValueError("La migración pendiente pertenece a otra instalación")
    legacy = target / ".antispam"
    if not legacy.is_symlink() or legacy.resolve() != root.resolve():
        raise ValueError("Alias de transición inesperado")
    if success:
        old_cache = Path(saved["config"] + ".last-good.json")
        if old_cache.exists():
            runtime.check_private(old_cache)
            old_cache.unlink()
        legacy.unlink()
        runtime.audit("migracion_completada", destino=str(root), alias_retirado=str(legacy))
    else:
        legacy.unlink()
        legacy.mkdir(mode=0o700)
        for name in saved["names"]:
            if (root / name).exists() or (root / name).is_symlink():
                shutil.move(str(root / name), str(legacy / name))
        runtime.audit("migracion_restaurada", destino=str(legacy))
    marker.unlink()
    sync_directory(target)


def legacy_prepare(code, config_path):
    """Puente ejecutado por --prepare-upgrade del actualizador público 1.0.x."""
    code = Path(code).absolute()
    legacy = code.parent.parent
    if legacy.name != ".antispam" or legacy.is_symlink() or not (legacy / "installation.json").is_file():
        return
    record = json.loads((legacy / "installation.json").read_text())
    target = legacy.parent
    if record.get("project") != PROJECT or record.get("target") != str(target):
        raise ValueError("Instalación histórica no verificable")
    runtime.install_logging(write)
    runtime.audit("upgrade_historico_preparado", version_origen=record.get("version"), version_destino=code.name)
    # Conservar el inode bloqueado incluso si /root y /var son sistemas de archivos distintos.
    lock_fd = os.open(str(legacy / "upgrade.lock"), os.O_RDONLY | os.O_NOFOLLOW)
    new_lock_fd = None
    try:
        new_lock_fd = migrate_legacy(target, config_path)
        helper = runtime.data_root() / "releases" / code.name / "lib/distribution.py"
        command = [sys.executable, str(helper), "finish-legacy", "--target", str(target), "--legacy-lock-fd", str(lock_fd),
                   "--parent-pid", str(os.getppid())]
        descriptors = (lock_fd,)
        if new_lock_fd is not None:
            command += ["--new-lock-fd", str(new_lock_fd)]
            descriptors += (new_lock_fd,)
        subprocess.Popen(command,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         pass_fds=descriptors, start_new_session=True)
    except Exception:
        finish_legacy(target, success=False)
        raise
    finally:
        os.close(lock_fd)
        if new_lock_fd is not None:
            os.close(new_lock_fd)


def wait_legacy(target, lock_fd, parent_pid):
    root = runtime.private_data()
    with os.fdopen(lock_fd) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # TemporaryDirectory del actualizador 1.0.x se elimina al salir de deploy.
        while any(root.glob("download-*")):
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        record = json.loads((root / "installation.json").read_text())
        success = version(record["version"]) >= (1, 1, 0)
        finish_legacy(target, success)
        if success:
            config, commands = installed_context(root / "current", record["config"])
            active = _service_active(config, commands)
            runtime.audit("upgrade_historico_completado", version=record["version"], servicio_activo=active)


def fetch(url, limit):
    if not url.startswith("https://"):
        raise ValueError("La descarga requiere HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "outbound-spam-upgrade", "Accept": "application/vnd.github+json"})
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
    name = "outbound-spam-" + number + ".tar.gz"
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
    root = runtime.private_data()
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
    root = runtime.private_data()
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


def _snapshot_state(config, config_path, backup, commands=None):
    shutil.copy2(str(config_path), str(backup / "config.before"))
    state = Path(config["state_dir"])
    for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm", "status.json", "recovery"):
        path = state / name
        if path.is_symlink():
            raise ValueError("El estado no debe contener enlaces administrados")
        if path.is_dir():
            shutil.copytree(str(path), str(backup / name), symlinks=True)
        elif path.exists():
            shutil.copy2(str(path), str(backup / name))
    cache = runtime.config_cache(config_path)
    if cache.exists():
        shutil.copy2(str(cache), str(backup / "config.last-good.json"))
    if commands is not None and (Path(config["postfix_config"]) / "main.cf").is_file():
        files = [Path(config["postfix_config"]) / name for name in ("outbound-spam-submit-blocked", "outbound-spam-submit-blocked.db")]
        files.append(Path(config["state_dir"]) / "postfix-submit-backup.json")
        saved = {"parameter": commands.pc("-h", "authorized_submit_users"), "files": {}}
        for path in files:
            if path.is_symlink():
                raise ValueError("Archivo de ACL local no debe ser un enlace")
            saved["files"][str(path)] = None
            if path.exists():
                saved["files"][str(path)] = {"hex": path.read_bytes().hex(), "mode": stat.S_IMODE(path.stat().st_mode)}
        write(backup / "postfix-submit.before.json", json.dumps(saved))


def _restore_state(config, config_path, backup, commands=None):
    failed = backup / "failed-state"
    failed.mkdir(mode=0o700, exist_ok=True)
    state = Path(config["state_dir"])
    saved_config = backup / "config.before"
    if saved_config.exists() and Path(config_path).is_file():
        original = saved_config.read_text()
        current = Path(config_path).read_text()
        # Restaurar únicamente nuestra ampliación; conservar una edición externa.
        suffix = current[len(original):] if current.startswith(original) else ""
        if (runtime.completed_config_matches(original.encode("utf-8"), current.encode("utf-8")) or
                re.fullmatch(r"\n?\n# Estado HTTP del servicio\n(?:status_http_(?:host=127\.0\.0\.1|port=23456)\n){1,2}", suffix)):
            write(config_path, original)
    for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm", "status.json", "recovery"):
        current, saved = state / name, backup / name
        if current.exists():
            os.replace(str(current), str(failed / name))
        if saved.is_dir():
            shutil.copytree(str(saved), str(current), symlinks=True)
        elif saved.exists():
            shutil.copy2(str(saved), str(current))
    cache = runtime.config_cache(config_path)
    if (backup / "config.last-good.json").exists():
        shutil.copy2(str(backup / "config.last-good.json"), str(cache))
    elif cache.exists():
        os.replace(str(cache), str(failed / "config.last-good.json"))
    snapshot = backup / "postfix-submit.before.json"
    if commands is not None and snapshot.exists():
        saved = json.loads(snapshot.read_text())
        installed = "!hash:" + str(Path(config["postfix_config"]) / "outbound-spam-submit-blocked")
        installed += ", " + saved["parameter"] if saved["parameter"] else ""
        actual = commands.pc("-h", "authorized_submit_users")
        if actual == installed and actual != saved["parameter"]:
            commands.pc("-e", "authorized_submit_users=" + saved["parameter"])
            commands.run("postfix", ["-c", config["postfix_config"], "reload"])
            for name, data in saved["files"].items():
                path = Path(name)
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(bytes.fromhex(data["hex"]))
                    path.chmod(data["mode"])


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


def assigned_names(text):
    return runtime.assigned_names(text)


def missing_new_variables(source, target, config_path):
    """Compatibilidad de diagnóstico: todas las ausentes, no solo nombres nuevos."""
    if not config_path.is_file():
        return []
    return sorted(assigned_names((source / "outbound-spam.conf.example").read_text()) - assigned_names(config_path.read_text()))


def _prepare(source, config, config_path, lock_fd):
    command = ["/bin/bash", str(source / "outbound-spam.sh"), "--config", str(config_path),
               "--state-dir", config["state_dir"], "--prepare-upgrade", "--upgrade-lock-fd", str(lock_fd)]
    result = subprocess.run(command, pass_fds=(lock_fd,), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError("La preparación de la actualización ha fallado: " + result.stderr.strip()[:300])
    return json.loads(result.stdout)


def service_update_plan(release, manifest, target, config, config_path, previous_installation):
    """Actualizar solo una unidad propia intacta, conservando su ExecStart y drop-ins."""
    path = Path(config["service_unit"])
    result = {"updated": False, "note": "Servicio no instalado", "sha256": None, "text": None}
    if not path.exists():
        return result
    if path.is_symlink() or "# outbound-spam" not in path.read_text():
        raise ValueError("La unidad no pertenece a outbound-spam")
    original = path.read_text()
    exec_lines = re.findall(r"(?m)^ExecStart=(.*)$", original)
    if len(exec_lines) != 1 or not re.fullmatch(r"/bin/bash " + re.escape(str(target)) + r"/outbound-spam\.sh --config [A-Za-z0-9_./-]+ --state-dir [A-Za-z0-9_./-]+ --daemon", exec_lines[0]):
        return dict(result, note="ExecStart personalizado: se conserva la unidad")
    import runtime
    current_template = runtime.render_service(config, target, str(config_path), exec_start=exec_lines[0])
    old_hash = previous_installation.get("service_unit_sha256")
    if original != current_template and (not old_hash or digest(path) != old_hash):
        return dict(result, note="Unidad modificada manualmente: se conserva el archivo", sha256=old_hash)
    if manifest.get("service_unit_api") != 1:
        return dict(result, note="Release sin API de plantilla systemd: se conserva la unidad", sha256=old_hash)
    spec = importlib.util.spec_from_file_location("outbound_spam_candidate_runtime", release / "lib/runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.render_service(config, target, str(config_path), exec_start=exec_lines[0])
    if "# outbound-spam" not in rendered or re.findall(r"(?m)^ExecStart=(.*)$", rendered) != exec_lines:
        raise ValueError("La plantilla candidata cambia la identidad del servicio")
    return {"updated": rendered != original, "note": "Unidad gestionada actualizada" if rendered != original else "Unidad gestionada ya actualizada",
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(), "text": rendered if rendered != original else None}


def deploy(source, target, config_path=None, config=None, commands=None, preparer=_prepare):
    source, target = Path(source).absolute(), protected_directory(target, create=True)
    if source == target:
        raise ValueError("Instale en una ruta distinta del paquete/repo de origen")
    manifest = validate_tree(source)
    root = runtime.private_data()
    if (root / "installation.json").exists():
        installed = json.loads((root / "installation.json").read_text())
        if installed.get("project") != PROJECT or installed.get("target") != str(target):
            raise ValueError("El almacenamiento pertenece a otra instalación")
    _owned_entrypoints(target, source)
    existing = (target / "outbound-spam.sh").exists()
    config_path = Path(config_path or target / "outbound-spam.conf").absolute()
    protected_directory(config_path.parent, create=True)
    managed_entries = {target / name for name in ("outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example")}
    if config_path in managed_entries:
        raise ValueError("La configuración operativa no puede ser un componente de código/ejemplo gestionado")
    config_existed = config_path.exists()
    if config_path.exists() or config_path.is_symlink():
        info = config_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("La configuración existente debe ser privada y pertenecer al usuario ejecutor")
    elif existing:
        raise ValueError("La configuración existente no está disponible; no se crea ni sustituye durante un upgrade")
    before_variables = assigned_names(config_path.read_text()) if config_path.exists() else set()
    initial_config = config_path.read_bytes() if config_existed and not existing else None
    if existing and config is None:
        config, commands = installed_context(source, config_path)
    legacy = target / ".antispam"
    lock_root = protected_directory(legacy) if legacy.exists() and not legacy.is_symlink() else root
    with (lock_root / "upgrade.lock").open("a") as upgrade_lock:
        os.chmod(str(lock_root / "upgrade.lock"), 0o600)
        try:
            fcntl.flock(upgrade_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Ya hay otra instalación/actualización en curso")
        was_active, started, changed, state_saved, unit_changed = False, False, False, False, False
        unit_before, unit_mode, unit_plan = None, None, None
        old_link = os.readlink(str(lock_root / "current")) if (lock_root / "current").is_symlink() else None
        if (lock_root / "current").exists() and old_link is None:
            raise ValueError("La ruta current no es un enlace gestionado")
        backup = root / ("upgrade-backup-%s-%s" % (time.strftime("%Y%m%d-%H%M%S%z"), secrets.token_hex(4)))
        backup.mkdir(mode=0o700)
        old_entries = {}
        for name in ("outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example", "installation.json"):
            path = lock_root / name if name == "installation.json" else target / name
            if path.exists():
                old_entries[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        operation_lock, migration_lock = None, None
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
                _snapshot_state(config, config_path, backup, commands)
                state_saved = True
            if lock_root != root:
                migration_lock = migrate_legacy(target, config_path)
            runtime.install_logging(write)
            runtime.audit("instalacion_inicio", operacion="upgrade" if existing else "install", version=manifest["version"], destino=str(target))
            release = _copy_release(source, target, manifest)
            if existing:
                previous_installation = json.loads(old_entries.get("installation.json", (b"{}", 0))[0])
                unit_plan = service_update_plan(release, manifest, target, config, config_path, previous_installation)
                if unit_plan["updated"]:
                    unit_path = Path(config["service_unit"])
                    unit_before, unit_mode = unit_path.read_text(), stat.S_IMODE(unit_path.stat().st_mode)
                    write(backup / "service-unit.before", unit_before)
                preparer(release, config, config_path, operation_lock.fileno())
                if not config_path.is_file() or config_path.is_symlink():
                    raise ValueError("La configuración dejó de estar disponible; se cancela sin regenerarla")
                if assigned_names((release / "outbound-spam.conf.example").read_text()) - assigned_names(config_path.read_text()):
                    raise ValueError("La preparación no completó todas las variables de configuración")
            changed = True
            _install_entrypoints(release, target)
            _activate(target, release)
            if not existing and not config_path.exists():
                initial = (release / "outbound-spam.conf.example").read_text()
                initial = initial.replace("report=(admin@example.org)", "report=()")
                initial = initial.replace("notify=true", "notify=false")
                initial = re.sub(r"(?m)^declare -A custom_limits=.*$", "declare -A custom_limits=()", initial)
                write(config_path, initial)
            if not existing:
                from outbound_spam import Config
                runtime.migrate_config(config_path, release, Config.from_bytes, write)
            if unit_plan and unit_plan["updated"]:
                unit_changed = True
                write(Path(config["service_unit"]), unit_plan["text"], unit_mode)
                commands.run("systemctl", ["daemon-reload"])
            write(root / "installation.json", json.dumps({"project": PROJECT, "version": manifest["version"],
                  "target": str(target), "config": str(config_path), "installed_at": time.time(),
                  "service_unit_sha256": unit_plan["sha256"] if unit_plan else None}))
            if existing and Path(config["monit_script"]).exists():
                if "outbound-spam" not in Path(config["monit_script"]).read_text():
                    raise ValueError("El comprobador Monit instalado es ajeno")
                write(Path(config["monit_script"]), (release / "outbound-spam-status.sh").read_text(), 0o700)
            if was_active:
                # A partir de aquí el nuevo código puede ejecutar acciones. No rebobinar SQL.
                started = True
                commands.run("systemctl", ["start", Path(config["service_unit"]).name])
            finish_legacy(target, success=True)
            old_cache = Path(str(config_path) + ".last-good.json")
            if old_cache.exists():
                runtime.check_private(old_cache)
                cache = runtime.config_cache(config_path)
                if not cache.exists():
                    write(cache, old_cache.read_text())
                old_cache.unlink()
            runtime.audit("instalacion_completada", version=manifest["version"], servicio_reiniciado=was_active, respaldo=str(backup))
            return {"version": manifest["version"], "target": str(target), "config": str(config_path),
                    "service_restarted": was_active, "backup": str(backup),
                    "config_preserved": config_existed, "config_created": not config_existed,
                    "config_variables_added": sorted(assigned_names(config_path.read_text()) - before_variables),
                    "missing_new_variables": sorted(assigned_names((release / "outbound-spam.conf.example").read_text()) - assigned_names(config_path.read_text())),
                    "service_unit_updated": bool(unit_plan and unit_plan["updated"]),
                    "service_unit_note": unit_plan["note"] if unit_plan else "Servicio no instalado"}
        except Exception as exc:
            runtime.audit("instalacion_error", error=str(exc), nuevo_servicio_iniciado=started, respaldo=str(backup))
            if not started:
                if initial_config is not None:
                    from outbound_spam import Config
                    runtime.rollback_config_completion(config_path, initial_config, source, Config.from_bytes, write)
                if existing and state_saved:
                    _restore_state(config, config_path, backup, commands)
                if changed:
                    if old_link:
                        temp = root / (".rollback-" + secrets.token_hex(4))
                        temp.symlink_to(old_link)
                        os.replace(str(temp), str(root / "current"))
                    elif (root / "current").is_symlink():
                        (root / "current").unlink()
                    for name in ("outbound-spam.sh", "outbound-spam-status.sh", "outbound-spam.conf.example", "installation.json"):
                        path = root / name if name == "installation.json" else target / name
                        if name in old_entries:
                            value, mode = old_entries[name]
                            write(path, value.decode("utf-8"), mode)
                        elif path.exists():
                            path.unlink()
                if unit_changed:
                    write(Path(config["service_unit"]), unit_before, unit_mode)
                    commands.run("systemctl", ["daemon-reload"])
                finish_legacy(target, success=False)
                if was_active:
                    commands.run("systemctl", ["start", Path(config["service_unit"]).name])
            else:
                raise ValueError("Versión instalada, pero falló el arranque del servicio. Se conserva el estado actual; revise systemctl. Respaldo: " + str(backup))
            raise
        finally:
            if operation_lock is not None:
                operation_lock.close()
            if migration_lock is not None:
                os.close(migration_lock)


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
    record = runtime.data_root() / "installation.json"
    if not record.is_file() or record.is_symlink():
        raise ValueError("Instalación sin gestor de releases; ejecute primero install.sh de la distribución oficial")
    installation = json.loads(record.read_text())
    if installation.get("project") != PROJECT or installation.get("target") != str(target):
        raise ValueError("La instalación no corresponde a este proyecto/ruta")
    release = latest_release(current, downloader)
    if release is None:
        completed = complete_installed_config(target, config_path, config)
        return dict(completed, updated=False, version=current, message="Ya está instalada la última versión estable; configuración completa")
    number, name, urls = release
    root = runtime.private_data()
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


def complete_installed_config(target, config_path, config):
    """Incluso sin release nuevo, --upgrade sincroniza configuración y ejemplo."""
    root = runtime.private_data()
    source = root / "current"
    validate_tree(source)
    with (root / "upgrade.lock").open("a") as upgrade_lock:
        os.chmod(str(root / "upgrade.lock"), 0o600)
        try:
            fcntl.flock(upgrade_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Ya hay otra instalación/actualización en curso")
        state = protected_directory(config["state_dir"], create=True)
        with (state / "lock").open("a") as lock:
            os.chmod(str(state / "lock"), 0o600)
            deadline = time.monotonic() + config["lock_wait"]
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Una operación activa impide completar la configuración")
                    time.sleep(0.1)
            _owned_entrypoints(target, source)
            from outbound_spam import Config
            completed = runtime.migrate_config(config_path, source, Config.from_bytes, write)
            example = Path(target) / "outbound-spam.conf.example"
            text = (source / "outbound-spam.conf.example").read_text()
            if not example.exists() or example.read_text() != text:
                write(example, text)
            return {"config_variables_added": completed["added"], "config_completion_backup": completed["backup"],
                    "missing_new_variables": []}


def main():
    p = argparse.ArgumentParser(description="Instalador del release outbound-spam")
    p.add_argument("action", choices=["install", "finish-legacy"])
    p.add_argument("--source", type=Path)
    p.add_argument("--target", default="/root/scripts", type=Path)
    p.add_argument("--config", type=Path)
    p.add_argument("--legacy-lock-fd", type=int)
    p.add_argument("--new-lock-fd", type=int)
    p.add_argument("--parent-pid", type=int)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("La instalación requiere root")
    os.umask(0o077)
    if args.action == "finish-legacy":
        if args.legacy_lock_fd is None or args.parent_pid is None:
            p.error("finish-legacy requiere descriptor del bloqueo histórico y PID del actualizador")
        wait_legacy(args.target, args.legacy_lock_fd, args.parent_pid)
        return
    if args.source is None:
        p.error("install requiere --source")
    print(json.dumps(deploy(args.source, args.target, args.config), ensure_ascii=False, indent=2))
    print("Configure %s; después ejecute --doctor y los instaladores de guard/servicio/Monit." % (args.config or args.target / "outbound-spam.conf"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        runtime.audit("instalacion_error", error=str(exc))
        raise SystemExit("Instalación: " + str(exc))

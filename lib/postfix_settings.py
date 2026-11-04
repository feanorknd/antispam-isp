"""Edición administrativa de límites. Sin dependencias externas de Python."""
import functools
import ipaddress
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time
import runtime

BEGIN = "# ================= BEGIN OUTBOUND-SPAM RATELIMITS ================="
END = "# ================== END OUTBOUND-SPAM RATELIMITS =================="
CONF_BEGIN = "# BEGIN OUTBOUND-SPAM LIMITS"
CONF_END = "# END OUTBOUND-SPAM LIMITS"
ACCOUNTS_BEGIN = "# BEGIN OUTBOUND-SPAM ACCOUNT RULES"
ACCOUNTS_END = "# END OUTBOUND-SPAM ACCOUNT RULES"


class MonitorError(Exception):
    pass


def is_local(account):
    return isinstance(account, str) and bool(re.fullmatch(r"local:[0-9]+", account))


def unix_identity(uid):
    try:
        entry = pwd.getpwuid(int(uid))
    except KeyError:
        raise MonitorError("UID local sin usuario del sistema: " + str(uid))
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", entry.pw_name):
        raise MonitorError("Usuario local no representable en la ACL Postfix")
    return {"uid": entry.pw_uid, "user": entry.pw_name, "home": entry.pw_dir}


def domain_belongs(domain, domains):
    return bool(domain and domains is not None and (domain in domains or any(
        d.startswith("*.") and domain.endswith(d[1:]) for d in domains)))


@functools.lru_cache(maxsize=1)
def public_suffix_rules():
    """PSL ICANN y PRIVATE local, fijada por el release y sin acceso a red."""
    try:
        rules = json.loads((Path(__file__).resolve().parent.parent / "docs/PUBLIC_SUFFIX_LIST.json").read_text(encoding="utf-8"))["rules"]
        exact, wildcard, exceptions = set(), set(), set()
        for rule in rules:
            if rule.startswith("!"):
                exceptions.add(rule[1:])
            elif rule.startswith("*."):
                wildcard.add(rule[2:])
            else:
                exact.add(rule)
        if not {"com", "com.es", "co.uk", "github.io"} <= exact or not wildcard or not exceptions:
            raise ValueError("PSL incompleta")
        return frozenset(exact), frozenset(wildcard), frozenset(exceptions)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise MonitorError("No se puede cargar la Public Suffix List del release: " + str(exc)) from None


def registrable_domain(domain):
    """eTLD+1 según PSL: coincidencia más larga, comodines y excepciones."""
    try:
        domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if domain.startswith("*."):
        domain = domain[2:]
    labels = domain.split(".")
    if len(domain) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        return None
    try:
        ipaddress.ip_address(domain)
        return None
    except ValueError:
        pass
    exact, wildcard, exceptions = public_suffix_rules()
    suffix_length = 1  # Regla implícita * de la PSL para sufijos desconocidos.
    for index in range(len(labels)):
        suffix = ".".join(labels[index:])
        if suffix in exceptions:
            suffix_length = len(labels) - index - 1
            break
        if suffix in exact:
            suffix_length = max(suffix_length, len(labels) - index)
        if index > 0 and suffix in wildcard:
            suffix_length = max(suffix_length, len(labels) - index + 1)
    return ".".join(labels[-suffix_length - 1:]) if len(labels) > suffix_length else None


class Websites:
    """Instantánea de dominios de webs ISPConfig, cacheada por pasada."""
    def __init__(self, config, query):
        self.c, self.query = config, query
        self.rows = None

    def load(self):
        if self.rows is None:
            text = self.query("SELECT domain_id,parent_domain_id,HEX(domain),HEX(system_user),HEX(type),HEX(subdomain) "
                              "FROM web_domain WHERE server_id=%d;" % self.c["ispconfig_server_id"])
            rows = {}
            for number, line in enumerate(text.splitlines(), 1):
                fields = line.split("\t")
                ident = fields[0] if re.fullmatch(r"[0-9]{1,20}", fields[0]) else "?"
                field = "columnas"
                try:
                    if len(fields) != 6:
                        raise ValueError()
                    field = "domain_id"
                    ident = int(fields[0])
                    if ident in rows or ident <= 0:
                        raise ValueError()
                    field = "parent_domain_id"
                    parent = int(fields[1])
                    if parent < 0:
                        raise ValueError()
                    decoded = {}
                    for field, value in zip(("domain", "system_user", "type", "subdomain"), fields[2:]):
                        # HEX(NULL) devuelve el literal SQL NULL, no texto hexadecimal.
                        # Un alias sin usuario propio hereda del padre en domains().
                        decoded[field] = "" if field == "system_user" and value == "NULL" else bytes.fromhex(value).decode("utf-8")
                    field = "domain"
                    domain = decoded["domain"].rstrip(".").encode("idna").decode("ascii").lower()
                    rows[ident] = {"id": ident, "parent": parent,
                                   "domain": domain, "user": decoded["system_user"],
                                   "type": decoded["type"], "subdomain": decoded["subdomain"]}
                except (ValueError, UnicodeError):
                    raise MonitorError("Respuesta web_domain de ISPConfig no válida: fila=%d domain_id=%s campo=%s" %
                                       (number, ident, field)) from None
            self.rows = rows
        return self.rows

    def domains(self, user):
        rows = self.load()
        selected = {i for i, row in rows.items() if row["user"] == user
                    and (row["parent"] == 0 or row["type"].startswith("vhost"))}
        if not selected:
            return None
        # No heredar un subdominio con su propio usuario/pool.
        while True:
            children = {i for i, row in rows.items() if row["parent"] in selected
                        and row["user"] in ("", user)}
            if children <= selected:
                break
            selected |= children
        domains = {rows[i]["domain"] for i in selected if rows[i]["domain"]}
        # Varios subdominios de la misma web comparten un único eTLD+1.
        domains |= {parent for parent in (registrable_domain(domain) for domain in domains) if parent}
        for i in selected:
            if rows[i]["subdomain"] in ("www", "*"):
                domains.add(("www." if rows[i]["subdomain"] == "www" else "*.") + rows[i]["domain"])
        return sorted(domains)


class LocalGuard:
    """ACL sendmail/postdrop. Pertenencia al mapa negado, sin reload por incidente."""
    def __init__(self, config, commands, write):
        self.c, self.cmd, self.write = config, commands, write
        self.source = Path(config["postfix_config"]) / "outbound-spam-submit-blocked"
        self.database = Path(str(self.source) + ".db")
        self.backup = Path(config["state_dir"]) / "postfix-submit-backup.json"

    @property
    def restriction(self):
        return "!hash:" + str(self.source)

    def record(self):
        try:
            info = self.backup.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError()
            saved = json.loads(self.backup.read_text())
            if saved["restriction"] != self.restriction:
                raise ValueError()
            return saved
        except (OSError, ValueError, KeyError):
            raise MonitorError("Respaldo de la ACL local ausente o no válido")

    def verify(self):
        saved = self.record()
        if self.cmd.pc("-h", "authorized_submit_users") != saved["installed"]:
            raise MonitorError("authorized_submit_users cambió; revise --install-local-guard")
        if not self.database.is_file() or self.database.is_symlink():
            raise MonitorError("Mapa de bloqueo local ausente")

    def users(self):
        if not self.database.exists():
            raise MonitorError("Mapa de bloqueo local ausente")
        output = self.cmd.run("postmap", ["-c", self.c["postfix_config"], "-s", "hash:" + str(self.source)])
        users = set()
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", fields[0]):
                raise MonitorError("Mapa de bloqueo local no válido")
            users.add(fields[0])
        return users

    def publish(self, users, verify=True):
        if verify:
            self.verify()
        users = set(users)
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", u) for u in users):
            raise MonitorError("Usuario no válido para el mapa de bloqueo local")
        temp = self.source.with_name("." + self.source.name + "-" + secrets.token_hex(8))
        indexed = Path(str(temp) + ".db")
        text = "# Gestionado por outbound-spam: usuarios locales bloqueados\n"
        text += "".join(u + " blocked\n" for u in sorted(users))
        try:
            self.write(temp, text, 0o644)
            self.cmd.run("postmap", ["-c", self.c["postfix_config"], "hash:" + str(temp)])
            os.chmod(str(indexed), 0o644)
            with indexed.open("rb") as stream:
                os.fsync(stream.fileno())
            for user in users:
                if not self.cmd.run("postmap", ["-c", self.c["postfix_config"], "-q", user, "hash:" + str(temp)]).strip():
                    raise MonitorError("No se verifica el bloqueo local de " + user)
            os.replace(str(indexed), str(self.database))
            self.write(self.source, text, 0o644)
        finally:
            for path in (temp, indexed):
                if path.exists():
                    path.unlink()

    def install(self, users=()):
        if re.search(r"[\s,{}\"'\\]", str(self.source)):
            raise MonitorError("Ruta no válida para el mapa de inyección local")
        actual = self.cmd.pc("-h", "authorized_submit_users")
        if self.backup.exists():
            saved = self.record()
            if actual != saved["installed"]:
                raise MonitorError("authorized_submit_users cambió después de instalar la ACL local")
            self.verify()
            published = self.users()
            if published - set(users):
                raise MonitorError("La ACL local conserva bloqueos sin historial; no se eliminan durante instalación/upgrade")
            if published != set(users):
                self.publish(users)
            return False
        if self.source.exists() or self.database.exists() or self.restriction in actual:
            raise MonitorError("Archivos o restricción local existentes sin respaldo gestionado")
        if "hash" not in self.cmd.pc("-m").splitlines():
            raise MonitorError("Postfix necesita soporte hash para el bloqueo local")
        installed = self.restriction + (", " + actual if actual else "")
        saved = {"restriction": self.restriction, "original": actual, "installed": installed}
        self.write(self.backup, json.dumps(saved))
        changed = False
        try:
            self.publish(users, verify=False)
            changed = True
            self.cmd.pc("-e", "authorized_submit_users=" + installed)
            self.cmd.run("postfix", ["-c", self.c["postfix_config"], "check"])
            self.verify()
            self.cmd.run("postfix", ["-c", self.c["postfix_config"], "reload"])
        except Exception:
            try:
                if changed:
                    self.cmd.pc("-e", "authorized_submit_users=" + actual)
                    self.cmd.run("postfix", ["-c", self.c["postfix_config"], "reload"])
            finally:
                if self.cmd.pc("-h", "authorized_submit_users") == actual:
                    for path in (self.source, self.database, self.backup):
                        if path.exists():
                            path.unlink()
            raise
        return True

    def uninstall(self):
        self.verify()
        if self.users():
            raise MonitorError("Desbloquee las webs antes de retirar la ACL local")
        saved = self.record()
        try:
            self.cmd.pc("-e", "authorized_submit_users=" + saved["original"])
            self.cmd.run("postfix", ["-c", self.c["postfix_config"], "check"])
            self.cmd.run("postfix", ["-c", self.c["postfix_config"], "reload"])
        except Exception:
            self.cmd.pc("-e", "authorized_submit_users=" + saved["installed"])
            self.cmd.run("postfix", ["-c", self.c["postfix_config"], "reload"])
            raise
        self.source.unlink()
        self.database.unlink()
        self.backup.rename(str(self.backup) + ".removed-" + secrets.token_hex(4))


def account_address(value):
    """Formato de cuenta SASL soportado: local dot-atom y dominio DNS/IDNA."""
    if re.fullmatch(r"local:[0-9]+", value):
        return "local:" + str(int(value.split(":", 1)[1]))
    if len(value) > 254 or value.count("@") != 1 or value.startswith("-"):
        raise ValueError("Cuenta de correo inválida: " + repr(value))
    local, domain = value.split("@")
    if not 1 <= len(local) <= 64 or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local) or local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("Cuenta de correo inválida: " + repr(value))
    try:
        labels = domain.encode("idna").decode("ascii").split(".")
    except UnicodeError:
        raise ValueError("Dominio de correo inválido: " + repr(domain))
    if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        raise ValueError("Dominio de correo inválido: " + repr(domain))
    return value.casefold()


def account_limit(value):
    if not re.fullmatch(r"[0-9]+", value) or not 0 < int(value) <= 9223372036854775807:
        raise ValueError("El límite y el periodo deben ser enteros positivos (máximo 2^63-1)")
    return int(value)


def render_accounts(text, ignored, limits):
    # Bash es ejecutable: conservar declaraciones/manuales y aplicar un único bloque
    # final autoritativo evita mutilar arrays multilínea, funciones o comentarios.
    text = remove_block(text, ACCOUNTS_BEGIN, ACCOUNTS_END)
    block = [ACCOUNTS_BEGIN,
             "# Valores finales de ignore y custom_limits; editar aquí o usar los comandos --set-*.",
             "unset ignore custom_limits",
             "declare -a ignore=(" + " ".join(shlex.quote(value) for value in sorted(ignored)) + ")",
             "declare -A custom_limits=("]
    block += ["    [%s]=%s" % (shlex.quote(account), shlex.quote("%d:%d" % limits[account])) for account in sorted(limits)]
    block += [")", ACCOUNTS_END]
    return text.rstrip() + "\n\n" + "\n".join(block) + "\n"


def configure_account(config_path, base, parse, write_atomic, *, account, limit=None, state_dir):
    """El llamante posee el bloqueo operativo. Releer para no perder cambios en espera."""
    conf = Path(config_path).absolute()
    original = runtime.stable_bytes(conf)
    config, _ = runtime.validate_config(original, base, parse)
    if Path(config["state_dir"]).resolve() != Path(state_dir).resolve():
        raise ValueError("state_dir cambió mientras esperaba; vuelva a ejecutar el comando")
    account = account_address(account)
    if limit is not None:
        limit = tuple(account_limit(str(value)) for value in limit)
        if len(limit) != 2:
            raise ValueError("Se requieren máximo y periodo")
    ignored, limits = set(config["ignore"]), dict(config["custom_limits"])
    previous_limit, was_ignored = limits.get(account), account in ignored
    if limit is None:
        ignored.add(account)
        limits.pop(account, None)
    else:
        ignored.discard(account)
        limits[account] = limit
    result = {"account": account, "ignored": account in ignored, "custom_limit": limit,
              "previous_ignored": was_ignored, "previous_custom_limit": previous_limit,
              "config": str(conf), "changed": ignored != config["ignore"] or limits != config["custom_limits"],
              "note": "Se conservan incidentes, bloqueos y mensajes retenidos existentes; la regla se aplica en la siguiente pasada"}
    if not result["changed"]:
        return result
    proposed = render_accounts(original.decode("utf-8"), ignored, limits).encode("utf-8")
    candidate, serialized = runtime.validate_config(proposed, base, parse)
    expected = dict(config.v, ignore=ignored, custom_limits=limits)
    if candidate.v != expected:
        raise ValueError("La configuración propuesta no conserva los demás ajustes")
    cache = runtime.config_cache(conf)
    if cache.exists() or cache.is_symlink():
        runtime.check_private(cache)
    old_cache = cache.read_text() if cache.exists() else None
    backup = runtime.private_data() / ("account-rules-backup-%d-%s.json" % (time.time(), secrets.token_hex(4)))
    write_atomic(backup, json.dumps({"config_path": str(conf), "config": original.decode("utf-8"), "cache": old_cache}, ensure_ascii=False))
    if runtime.stable_bytes(conf) != original:
        raise ValueError("La configuración cambió durante la operación; vuelva a intentarlo")
    try:
        write_atomic(conf, proposed.decode("utf-8"))
        write_atomic(cache, json.dumps(runtime.config_record(conf, proposed, serialized)))
    except Exception:
        write_atomic(conf, original.decode("utf-8"))
        if old_cache is None:
            if cache.exists():
                cache.unlink()
        else:
            write_atomic(cache, old_cache)
        raise
    return dict(result, backup=str(backup))


def numeric_value(value):
    match = re.fullmatch(r"(\d+)([smhdw]?)", value.strip())
    if not match:
        return value.strip()
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def restart_postfix(config, commands):
    if Path(config["postfix_config"]).resolve() == Path("/etc/postfix").resolve():
        commands.run("systemctl", ["restart", "postfix"])
    else:
        # Una configuración alternativa no debe reiniciar la instancia predeterminada.
        commands.run("postfix", ["-c", config["postfix_config"], "stop"])
        commands.run("postfix", ["-c", config["postfix_config"], "start"])


def remove_block(text, begin, end):
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise ValueError("Bloque gestionado incompleto o duplicado: " + begin)
    if begin not in text:
        return text
    before, rest = text.split(begin, 1)
    _, after = rest.split(end, 1)
    return before.rstrip("\n") + "\n" + after.lstrip("\n")


def remove_assignments(text, names, bash=False):
    """Eliminar solo las asignaciones administradas, incluidas continuaciones main.cf."""
    output, skipping = [], False
    for line in text.splitlines(keepends=True):
        pattern = r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=" if bash else r"^([A-Za-z_][A-Za-z0-9_-]*)\s*="
        match = re.match(pattern, line)
        if match:
            skipping = match.group(1) in names
        elif not bash and (not line.strip() or line.lstrip().startswith("#")):
            output.append(line)
            continue
        elif not bash and line[:1].isspace() and skipping:
            continue
        else:
            skipping = False
        if not skipping:
            output.append(line)
    return "".join(output)


def rate_parameters(config, commands):
    version = commands.pc("-h", "mail_version")
    match = re.match(r"(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (3, 1):
        raise ValueError("Se requiere Postfix >= 3.1 para limitar por transporte sin depender del relay")
    commands.pc("-d", "-h", "default_transport_rate_delay")
    delay = str(config["postfix_outbound_delay"])
    if delay == "auto":
        seconds = math.ceil(config["time_period"] / (config["max_sent"] * config["postfix_headroom"]))
        delay = "%ds" % min(5, max(1, seconds))
    if not re.fullmatch(r"\d+[smhdw]?", delay):
        raise ValueError("Duración Postfix inválida")
    if delay.isdigit():
        delay += "s"
    master = []
    for line in commands.pc("-M").splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[1] == "=":
            fields = fields[2:]
        if len(fields) >= 8 and fields[1] == "unix":
            master.append(fields)
    services = {line[0] for line in master}
    destination_delay = str(config["postfix_destination_delay"])
    if not re.fullmatch(r"\d+[smhdw]?", destination_delay):
        raise ValueError("Retraso por destino inválido")
    if destination_delay.isdigit():
        destination_delay += "s"
    result = dict(default_destination_rate_delay=destination_delay, default_transport_rate_delay="0s",
                  smtpd_client_message_rate_limit=str(config["postfix_client_message_limit"]), anvil_rate_time_unit="60s")
    remote = set(config["external_transports"]) & services
    if not remote:
        raise ValueError("No se encuentra ningún transporte externo configurado en master.cf")
    for transport in sorted(remote):
        for suffix, value in (("_destination_rate_delay", destination_delay), ("_transport_rate_delay", delay)):
            key = transport + suffix
            if not commands.pc("-d", "-h", key):
                raise ValueError("Postfix no expone el parámetro del transporte externo " + key)
            result[key] = value
    local = ({"local", "lmtp", "virtual"} | set(config["local_transports"])) & services
    for fields in master:
        agent = Path(fields[7]).name
        if agent in ("local", "lmtp", "virtual", "error"):
            local.add(fields[0])
        # pipe puede ser entrega local o un método externo (UUCP/BSMTP).
        # Solo inferir locales para ejecutables conocidos; el resto se declara.
        argv = next((f[len("argv="):] for f in fields[8:] if f.startswith("argv=")), "")
        if agent == "pipe" and Path(argv).name in ("maildrop", "deliver", "dovecot-lda", "scalemail-store", "postfix-to-mailman.py"):
            local.add(fields[0])
    content_filter = commands.pc("-h", "content_filter").split(":", 1)[0].strip()
    if content_filter and content_filter in remote:
        raise ValueError("El filtro comparte el transporte de Internet; use un transporte de filtro separado para preservar locales")
    if content_filter in services:
        local.add(content_filter)
    local.difference_update(config["external_transports"])
    for transport in sorted(local):
        for suffix in ("_destination_rate_delay", "_transport_rate_delay"):
            key = transport + suffix
            # Estar en master.cf no implica que postconf exponga estas variables:
            # discard es un ejemplo estándar. No escribir claves que no se podrán
            # verificar en esta instalación concreta.
            if commands.pc("-d", "-h", key):
                result[key] = "0s"
    for line in commands.pc("-P").splitlines():
        key = line.split("=", 1)[0].strip().rsplit("/", 1)[-1]
        value = line.split("=", 1)[1].strip() if "=" in line else ""
        if key in result and numeric_value(value) != numeric_value(result[key]):
            raise ValueError("master.cf sobrescribe un límite gestionado: " + line.split("=", 1)[0])
    return result


def render_main(text, config, parameters):
    text = remove_block(text, BEGIN, END)
    # virt no es el transporte virtual estándar; retirar esa antigua asignación aislada.
    text = remove_assignments(text, set(parameters) | {"virt_destination_rate_delay"})
    reduced = max(1, config["max_sent"] // config["foreign_sender_divisor"])
    block = [BEGIN, "# Ratelimits", "# Detector SASL: %d destinatarios externos / %ds; dominio distinto: %d." %
             (config["max_sent"], config["time_period"], reduced),
             "# Regla global de salida SMTP: por transporte, independiente del relay/destino.",
             "# Postfix limita entregas; el detector cuenta destinatarios: no son cuotas equivalentes."]
    printed = set()
    remote_keys = [transport + suffix for transport in config["external_transports"] for suffix in ("_transport_rate_delay", "_destination_rate_delay")]
    for key in ["default_destination_rate_delay", "default_transport_rate_delay"] + remote_keys:
        if key not in parameters:
            continue
        block.append(key + " = " + parameters[key])
        printed.add(key)
    block.append("# Sin retraso para entregas locales/LMTP/virtual y transportes locales conocidos.")
    for key, value in parameters.items():
        if key.endswith("rate_delay") and key not in printed:
            block.append(key + " = " + value)
    block += ["# Máximos en recepción: por IP; 0 evita limitar también correo local y usuarios NAT.",
              "smtpd_client_message_rate_limit = " + parameters["smtpd_client_message_rate_limit"],
              "anvil_rate_time_unit = 60s", END]
    return text.rstrip() + "\n\n" + "\n".join(block) + "\n"


def render_config(text, config):
    values = {k: config[k] for k in ("max_sent", "time_period", "reminder_interval", "foreign_sender_divisor",
                                    "postfix_outbound_delay", "postfix_destination_delay", "postfix_headroom", "postfix_client_message_limit")}
    text = remove_block(text, CONF_BEGIN, CONF_END)
    text = remove_assignments(text, set(values), bash=True)
    return text.rstrip() + "\n\n" + CONF_BEGIN + "\n" + "\n".join("%s=%s" % (k, v) for k, v in values.items()) + "\n" + CONF_END + "\n"


def configure(config, commands, config_path, write_atomic, *, persist=True, yes=False, skip_postfix=False, prompt=None):
    main = (Path(config["postfix_config"]) / "main.cf").resolve()
    conf = Path(config_path).resolve()
    original_main = main.read_text(encoding="utf-8") if not skip_postfix else None
    original_conf = conf.read_text(encoding="utf-8") if persist else None
    mode = stat.S_IMODE(main.stat().st_mode) if not skip_postfix else 0o644
    if mode & 0o022:
        raise ValueError("main.cf no debe ser modificable por grupo/otros")
    parameters = rate_parameters(config, commands) if not skip_postfix else {}
    existing = any(re.match(r"^(" + "|".join(map(re.escape, parameters)) + r")\s*=", line)
                   for line in original_main.splitlines()) if parameters else False
    apply_postfix = not skip_postfix
    if apply_postfix and not existing and not yes:
        if prompt is None:
            if not sys.stdin.isatty():
                raise ValueError("No hay ajustes previos. Ejecute interactivamente, use --yes o --skip-postfix")
            prompt = input
        answer = prompt("No hay ajustes Ratelimits previos. ¿Crear el bloque y reiniciar Postfix? [s/N] ")
        apply_postfix = answer.strip().casefold() in ("s", "si", "sí", "y", "yes")
    proposed_main = render_main(original_main, config, parameters) if apply_postfix else original_main
    proposed_conf = render_config(original_conf, config) if persist else None
    backup = Path(config["state_dir"]) / ("limits-backup-%d-%s.json" % (time.time(), secrets.token_hex(4)))
    write_atomic(backup, json.dumps({"main_cf_path": str(main), "main_cf": original_main,
                                   "config_path": str(conf), "config": original_conf}, ensure_ascii=False))
    restarted = False
    try:
        if persist:
            candidate = conf.with_name("." + conf.name + ".check-" + secrets.token_hex(4))
            try:
                write_atomic(candidate, proposed_conf)
                check = subprocess.run(["/bin/bash", "-n", str(candidate)], capture_output=True, timeout=10)
                if check.returncode:
                    raise ValueError("La configuración Bash propuesta no es válida")
            finally:
                if candidate.exists():
                    candidate.unlink()
            write_atomic(conf, proposed_conf)
        if apply_postfix:
            write_atomic(main, proposed_main, mode)
            commands.run("postfix", ["-c", config["postfix_config"], "check"])
            for key, expected in parameters.items():
                actual = commands.pc("-h", key)
                if actual != expected:
                    raise ValueError("No se verifica %s: esperado %r, obtenido %r" % (key, expected, actual))
            restarted = True
            restart_postfix(config, commands)
    except Exception:
        if apply_postfix:
            write_atomic(main, original_main, mode)
        if persist:
            write_atomic(conf, original_conf)
        if restarted:
            with_restore_error = None
            try:
                if Path(config["postfix_config"]).resolve() == Path("/etc/postfix").resolve():
                    restart_postfix(config, commands)
                else:
                    try:
                        commands.run("postfix", ["-c", config["postfix_config"], "stop"])
                    except Exception:
                        pass
                    commands.run("postfix", ["-c", config["postfix_config"], "start"])
            except Exception as e:
                with_restore_error = e
            if with_restore_error:
                raise ValueError("Archivos restaurados; comprobar arranque de Postfix. Respaldo: " + str(backup)) from with_restore_error
        raise
    return {"max_sent": config["max_sent"], "time_period": config["time_period"],
            "foreign_sender_limit": max(1, config["max_sent"] // config["foreign_sender_divisor"]),
            "report_interval": config["reminder_interval"],
            "config_updated": persist, "postfix_updated": apply_postfix,
            "postfix_restarted": apply_postfix, "parameters": parameters if apply_postfix else {}, "backup": str(backup)}

"""Edición administrativa de límites. Sin dependencias externas de Python."""
import json
import math
import os
from pathlib import Path
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


def account_address(value):
    """Formato de cuenta SASL soportado: local dot-atom y dominio DNS/IDNA."""
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

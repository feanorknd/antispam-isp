#!/usr/bin/env bash
# Instalador del paquete antispam-isp. La configuración operativa se conserva.
set -euo pipefail
umask 077
base_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ " ${*} " == *' --help '* || " ${*} " == *' -h '* ]]; then
    printf '%s\n' 'Uso: sudo ./install.sh [--target /root/scripts] [--config /ruta/privada.conf] [--install-deps]' \
        'Instala el programa; conserva configuración, estado y restricciones.' \
        'Después configure el archivo .conf y ejecute --doctor, --install-postfix-guard, --install-service y --install-monit según corresponda.'
    exit 0
fi
(( EUID == 0 )) || { printf '%s\n' 'La instalación requiere root.' >&2; exit 2; }
args=()
for arg in "$@"; do
    if [[ "$arg" == --install-deps ]]; then
        command -v apt-get >/dev/null || { printf '%s\n' '--install-deps requiere Debian/Ubuntu con apt-get.' >&2; exit 2; }
        apt-get update
        apt-get install -y python3 postfix-sqlite ca-certificates default-mysql-client monit openssl
    else
        args+=("$arg")
    fi
done
command -v python3 >/dev/null || { printf '%s\n' 'Instale Python 3.8+ o utilice --install-deps.' >&2; exit 2; }
exec python3 "$base_dir/lib/distribution.py" install --source "$base_dir" "${args[@]}"

#!/usr/bin/env bash
# Instalador de outbound-spam. La configuración operativa se conserva.
set -euo pipefail
umask 077
base_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ " ${*} " == *' --help '* || " ${*} " == *' -h '* ]]; then
    printf '%s\n' 'Uso: sudo ./install.sh [--target /root/scripts] [--config /ruta/privada.conf] [--install-deps]' \
        'Instala el programa; conserva configuración, estado y restricciones.' \
        '--install-deps instala únicamente postfix-sqlite, sin recomendaciones.' \
        'Después configure el archivo .conf y ejecute --doctor, --install-postfix-guard, --install-service y --install-monit según corresponda.'
    exit 0
fi
(( EUID == 0 )) || { printf '%s\n' 'La instalación requiere root.' >&2; exit 2; }
command -v python3 >/dev/null || { printf '%s\n' 'Python 3.8+ debe estar instalado previamente.' >&2; exit 2; }
command -v postconf >/dev/null || { printf '%s\n' 'Postfix debe estar instalado previamente; el instalador solo añade postfix-sqlite.' >&2; exit 2; }
args=()
for arg in "$@"; do
    if [[ "$arg" == --install-deps ]]; then
        command -v apt-get >/dev/null || { printf '%s\n' '--install-deps requiere Debian/Ubuntu con apt-get.' >&2; exit 2; }
        apt-get update
        apt-get install --no-install-recommends -y postfix-sqlite
    else
        args+=("$arg")
    fi
done
exec python3 "$base_dir/lib/distribution.py" install --source "$base_dir" "${args[@]}"

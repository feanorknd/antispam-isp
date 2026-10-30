#!/usr/bin/env bash
# Entrada única para servicio, ejecución puntual y administración.
set -euo pipefail
umask 077
script_path=${BASH_SOURCE[0]}
links=0
while [[ -L "$script_path" ]]; do
    ((links += 1))
    ((links <= 40)) || { printf '%s\n' 'Demasiados enlaces simbólicos en el lanzador.' >&2; exit 2; }
    link_dir=$(CDPATH='' cd -- "$(dirname -- "$script_path")" && pwd -P)
    script_path=$(readlink -- "$script_path")
    [[ "$script_path" == /* ]] || script_path="$link_dir/$script_path"
done
base_dir=$(CDPATH='' cd -- "$(dirname -- "$script_path")" && pwd -P)
export OUTBOUND_SPAM_BASE="$base_dir"
if [[ -f "$base_dir/.antispam/current/lib/outbound_spam.py" ]]; then
    code_dir=$(CDPATH='' cd -- "$base_dir/.antispam/current" && pwd -P)
else
    code_dir=$base_dir
fi
export OUTBOUND_SPAM_CODE="$code_dir"
exec python3 "$code_dir/lib/outbound_spam.py" "$@"

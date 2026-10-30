#!/usr/bin/env bash
# Entrada única para servicio, ejecución puntual y administración.
set -euo pipefail
umask 077
base_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export OUTBOUND_SPAM_BASE="$base_dir"
if [[ -f "$base_dir/.antispam/current/lib/outbound_spam.py" ]]; then
    code_dir=$(CDPATH='' cd -- "$base_dir/.antispam/current" && pwd -P)
else
    code_dir=$base_dir
fi
export OUTBOUND_SPAM_CODE="$code_dir"
exec python3 "$code_dir/lib/outbound_spam.py" "$@"

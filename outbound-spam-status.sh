#!/usr/bin/env bash
# Monit usa únicamente el resumen atómico; no consulta SQLite ni el log de correo.
set -euo pipefail
exec python3 - "$@" <<'PY'
import json, os, sys, time
path = sys.argv[1] if len(sys.argv) > 1 else '/var/lib/outbound-spam/status.json'
try:
    with open(path, encoding='utf-8') as f:
        s = json.load(f)
    age = time.time() - s['last_success']
    if age < -60 or age > s['stale_after']:
        raise ValueError('analizador atrasado o reloj incoherente: %.0f segundos' % age)
    print(s['message'])
    sys.exit(s['code'] if s['code'] in (0, 1, 2) else 2)
except (OSError, ValueError, KeyError, TypeError) as e:
    print('Spam Status: ERROR: ' + str(e))
    sys.exit(2)
PY

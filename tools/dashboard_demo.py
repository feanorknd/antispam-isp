#!/usr/bin/env python3
"""Servidor temporal con datos sintéticos para comprobación visual; nunca toca el correo real."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
CODE = Path(os.environ.get("OUTBOUND_SPAM_DEMO_CODE", str(ROOT)))
sys.path.insert(0, str(CODE / "lib"))
import runtime
import analytics
import outbound_spam as m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--port", type=int, default=23457)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    history = analytics.History(args.directory / "analytics.sqlite3")
    now = int(time.time() // 60) * 60
    settings = {"ignore": ["newsletter@example.test"], "routing_mode": "static", "local_domains": ["local.test"],
                "external_transports": ["smtp", "relay"], "local_transports": ["local", "lmtp"]}
    records = []
    for i in range(288):
        at = now - 86400 + i * 300
        account = ["ana@example.test", "newsletter@example.test", "local:65000"][i % 3]
        qid = "DEMO%05d" % i
        records.append(["log", at, "pickup" if account.startswith("local:") else "smtpd", qid,
                        "uid=65000 from=<web@local.test>" if account.startswith("local:") else "client=host[192.0.2.1], sasl_username=" + account])
        records.append(["log", at, "qmgr", qid, "from=<sender@local.test>, size=100, nrcpt=1 (queue active)"])
        local = i % 4 == 0
        records.append(["log", at + 5, "lmtp" if local else "smtp", qid,
                        "to=<one@%s>, status=sent (ok)" % ("local.test" if local else "remote.test")])
        if i % 13 == 0:
            records.append(["log", at + 10, "smtpd", "NOQUEUE", "reject: RCPT 451 blocked; to=<x@remote.test>, sasl_username=" + account])
    state = {"at": now, "blocks": [{"account": "ana@example.test"}],
             "held": [{"qid": "DEMOHOLD", "account": "ana@example.test", "held_at": now - 3600}],
             "identities": [{"uid": 65000, "user": "web6", "domains": ["web.example.test"]}], "coverage": [], "dropped": 0}
    history.apply("demo-" + str(now), json.dumps({"settings": settings, "records": records, "state": state}))
    with history.db:
        analytics.put(history.db, "started_at", now - 86400)
    history.db.close()
    m.atomic_write(args.directory / "analytics-status.json", json.dumps({"ok": True, "heartbeat": time.time()}))
    m.atomic_write(args.directory / "status.json", json.dumps({"code": 1, "last_success": time.time(), "stale_after": 3600,
                   "message": "Datos sintéticos", "incidents": [], "blocked": ["ana@example.test"], "held": 1}))
    server, thread = runtime.start_status_http({"analytics_enabled": True, "status_http_host": args.host, "status_http_port": args.port, "stale_after": 3600}, args.directory)
    print("http://127.0.0.1:%d/dashboard/" % args.port, flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

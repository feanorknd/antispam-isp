#!/usr/bin/env python3
"""Comparación aislada contra main: incluye captura, trabajador y consultas, sin servidores reales."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))


def run(mode, count, reference, ignored_percent=0, local_percent=0):
    import outbound_spam as current
    import analytics
    import dashboard
    import runtime
    if mode == "baseline":
        source = subprocess.check_output(["git", "show", reference + ":lib/outbound_spam.py"], cwd=ROOT)
        m = types.ModuleType("baseline_monitor")
        exec(compile(source, "baseline_monitor.py", "exec"), m.__dict__)
    else:
        m = current
    values, _ = runtime.validate_config(b"notify=false\nmonitor_local=false\nrouting_mode=static\nlocal_domains=(local.test)\ndeclare -A custom_limits=()\n", ROOT, current.Config.from_bytes)
    with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temp:
        root = Path(temp)
        ignored = ["user%d@local.test" % i for i in range(ignored_percent)] + ["local:" + str(65000 + i) for i in range(ignored_percent)]
        c = m.Config(dict(values.v, custom_limits={}, ignore=ignored, monitor_local=True, state_dir=str(root)))
        store = m.Store(root / "state.sqlite3")
        if mode != "baseline":
            store.analytics = analytics.Capture(store, c)
        now = int(time.time() // 60) * 60 - 120
        # Construcción fuera de la medición: 60 días, 100 usuarios, destinos mixtos.
        lines = []
        from datetime import datetime, timezone
        for i in range(count):
            at = now - (i % 60) * 86400 + (i % 60)
            stamp = datetime.fromtimestamp(at, timezone.utc).isoformat()
            prefix = stamp + " host postfix/"
            qid = "Q%010d" % i
            def line(service, text):
                lines.append(prefix + service + "[123]: " + qid + ": " + text + "\n")
            if i % 100 < local_percent:
                line("pickup", "uid=%d from=<sender@local.test>" % (65000 + i % 100))
            else:
                line("smtpd", "client=host[192.0.2.1], sasl_username=user%d@local.test" % (i % 100))
            line("cleanup", "message-id=<%s@test>" % qid)
            line("qmgr", "from=<sender@local.test>, size=1000, nrcpt=1 (queue active)")
            local = i % 4 == 0
            line("lmtp" if local else "smtp", "to=<one@%s>, relay=mx[192.0.2.2], status=sent (ok)" % ("local.test" if local else "remote.test"))
            line("qmgr", "removed")
        cpu = time.process_time()
        wall = time.monotonic()
        parser = m.Parser(store, c, now + 120)
        with store.db:
            for text in lines:
                parser.feed(text)
            if mode != "baseline":
                store.analytics.finish(now)
            current_accounts, incidents = m.evaluate(store, c, now, offline=True)
        monitor_cpu = time.process_time() - cpu
        monitor_wall = time.monotonic() - wall
        worker_cpu = worker_wall = query_cpu = query_wall = 0
        if mode != "baseline":
            h = analytics.History(root / "analytics.sqlite3")
            start_cpu, start_wall = time.process_time(), time.monotonic()
            while analytics.drain(root, h):
                pass
            h.maintain()
            worker_cpu = time.process_time() - start_cpu
            worker_wall = time.monotonic() - start_wall
            start_cpu, start_wall = time.process_time(), time.monotonic()
            for _ in range(3):
                dashboard.query(root, "overview", {"from": [str(int(time.time()) - 60 * 86400 + 60)], "to": [str(now + 60)], "bucket": ["day"]})
            query_cpu = time.process_time() - start_cpu
            query_wall = time.monotonic() - start_wall
            h.db.close()
        store.db.close()
        return {"mode": mode, "messages": count, "monitor_cpu": monitor_cpu, "monitor_wall": monitor_wall,
                "worker_cpu": worker_cpu, "worker_wall": worker_wall, "three_queries_cpu": query_cpu,
                "three_queries_wall": query_wall, "total_cpu": monitor_cpu + worker_cpu + query_cpu,
                "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "disk_bytes": sum(p.stat().st_size for p in root.iterdir() if p.is_file())}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--messages", type=int, default=10000)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--reference", default="main")
    p.add_argument("--ignored-percent", type=int, default=0)
    p.add_argument("--local-percent", type=int, default=0)
    p.add_argument("--mode", choices=["baseline", "analytics"])
    args = p.parse_args()
    if args.mode:
        print(json.dumps(run(args.mode, args.messages, args.reference, args.ignored_percent, args.local_percent)))
        return
    results = []
    for _ in range(args.repeats):
        for mode in ("baseline", "analytics"):
            result = subprocess.check_output([sys.executable, str(Path(__file__).absolute()), "--mode", mode,
                                              "--messages", str(args.messages), "--reference", args.reference,
                                              "--ignored-percent", str(args.ignored_percent), "--local-percent", str(args.local_percent)], cwd=ROOT)
            results.append(json.loads(result))
    median = {mode: {key: statistics.median(r[key] for r in results if r["mode"] == mode)
                     for key in results[0] if key != "mode"} for mode in ("baseline", "analytics")}
    median["cpu_overhead_percent"] = (median["analytics"]["total_cpu"] / median["baseline"]["total_cpu"] - 1) * 100
    median["monitor_wall_overhead_percent"] = (median["analytics"]["monitor_wall"] / median["baseline"]["monitor_wall"] - 1) * 100
    print(json.dumps({"reference": args.reference, "repeats": args.repeats, "median": median, "runs": results}, indent=2))


if __name__ == "__main__":
    main()

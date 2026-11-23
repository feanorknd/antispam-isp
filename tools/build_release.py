#!/usr/bin/env python3
"""Construir el asset instalable mediante una lista explícita de componentes."""
import argparse
import base64
import gzip
import io
import json
import hashlib
from pathlib import Path
import sys
import subprocess
import tarfile
import zlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import distribution


def verify_configuration(source):
    """Contrato obligatorio en cada paquete: ejemplo, migración y valores privados."""
    code = r'''
import os, sys, tempfile
from pathlib import Path
base = Path(sys.argv[1])
sys.path.insert(0, str(base / "lib"))
import outbound_spam as m
import runtime
with tempfile.TemporaryDirectory(prefix="release-config-", dir="/root" if os.geteuid() == 0 else "/tmp/opencode") as folder:
    root = Path(folder)
    os.environ["OUTBOUND_SPAM_DATA"] = str(root / "data")
    os.environ["OUTBOUND_SPAM_LOG"] = str(root / "audit.log")
    path = root / "production.conf"
    original = "notify=false\nreport=()\nmax_sent=713\nauto_update=false\n"
    m.atomic_write(path, original)
    before, serialized = runtime.validate_config(path.read_bytes(), base, m.Config.from_bytes)
    declarations = runtime.example_declarations(base)
    if set(m.Config.from_bytes(serialized).v) - set(declarations):
        raise ValueError("Variables operativas ausentes del ejemplo")
    runtime.migrate_config(path, base, m.Config.from_bytes, m.atomic_write)
    after, _ = runtime.validate_config(path.read_bytes(), base, m.Config.from_bytes)
    if before.v != after.v or runtime.assigned_names(path.read_text()) != set(declarations) or not path.read_text().endswith(original):
        raise ValueError("Migración de configuración incompleta o cambia valores de producción")
    content, modified = path.read_bytes(), path.stat().st_mtime_ns
    if runtime.migrate_config(path, base, m.Config.from_bytes, m.atomic_write)["added"] or path.read_bytes() != content or path.stat().st_mtime_ns != modified:
        raise ValueError("Migración de configuración no idempotente")
'''
    result = subprocess.run([sys.executable, "-c", code, str(source)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Contrato de configuración del release incumplido: " + result.stderr.strip())


def build(source, output, legacy=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = distribution.make_manifest(source)
    distribution.validate_tree(source)
    verify_configuration(source)
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    number = manifest["version"]
    prefix = ("antispam-isp-" if legacy else "outbound-spam-") + number
    archive = output / (prefix + ".tar.gz")
    payloads = {name: (source / name).read_bytes() for name in manifest["files"]}
    if all(name in payloads for name in distribution.BUNDLED_COMPONENTS):
        bundled = {name: payloads.pop(name).decode("utf-8") for name in distribution.BUNDLED_COMPONENTS}
        encoded = base64.b64encode(zlib.compress(json.dumps(bundled, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)).decode("ascii")
        original = payloads["lib/runtime.py"].decode("utf-8")
        marker = '_RELEASE_BUNDLE = ""'
        if original.count(marker) != 1:
            raise ValueError("Falta el punto único de integración del bundle")
        payloads["lib/runtime.py"] = original.replace(marker, "_RELEASE_BUNDLE = " + repr(encoded)).encode("utf-8")
        manifest["bundled_components"] = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in bundled.items()}
        manifest["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    payloads["RELEASE.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for name, data in sorted(payloads.items()):
                    info = tarfile.TarInfo(prefix + "/" + name)
                    info.size = len(data)
                    info.mode = 0o755 if name in ("install.sh", "outbound-spam.sh", "outbound-spam-status.sh") else 0o644
                    info.mtime = 0
                    info.uid = info.gid = 0
                    tar.addfile(info, io.BytesIO(data))
    checksum = distribution.digest(archive)
    archives = [output / (name + number + ".tar.gz") for name in ("outbound-spam-", "antispam-isp-")]
    (output / "SHA256SUMS").write_text("".join(distribution.digest(path) + "  " + path.name + "\n" for path in archives if path.exists()))
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    archive, checksum = build(args.source, args.output)
    build(args.source, args.output, legacy=True)
    print(json.dumps({"archive": str(archive), "sha256": checksum, "checksums": str(archive.parent / "SHA256SUMS")}, indent=2))


if __name__ == "__main__":
    main()

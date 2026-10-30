#!/usr/bin/env python3
"""Construir el asset instalable mediante una lista explícita de componentes."""
import argparse
import gzip
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import distribution


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = distribution.make_manifest(source)
    distribution.validate_tree(source)
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    number = manifest["version"]
    prefix = "antispam-isp-" + number
    archive = output / (prefix + ".tar.gz")
    payloads = {name: (source / name).read_bytes() for name in manifest["files"]}
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
    (output / "SHA256SUMS").write_text(checksum + "  " + archive.name + "\n")
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    archive, checksum = build(args.source, args.output)
    print(json.dumps({"archive": str(archive), "sha256": checksum, "checksums": str(archive.parent / "SHA256SUMS")}, indent=2))


if __name__ == "__main__":
    main()

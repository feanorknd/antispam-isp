#!/usr/bin/env python3
"""Actualizar la instantánea PSL distribuida; solo se ejecuta al preparar releases."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "https://publicsuffix.org/list/public_suffix_list.dat"


def main():
    with urllib.request.urlopen(SOURCE, timeout=60) as response:
        data = response.read(2 * 1024 * 1024)
    text = data.decode("utf-8")
    for section in ("ICANN", "PRIVATE"):
        if "// ===BEGIN %s DOMAINS===" % section not in text or "// ===END %s DOMAINS===" % section not in text:
            raise ValueError("PSL incompleta: " + section)
    rules = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        prefix = "!" if line.startswith("!") else "*." if line.startswith("*.") else ""
        domain = line[len(prefix):].encode("idna").decode("ascii").lower()
        if not all(re.fullmatch(r"[a-z0-9-]+", label) for label in domain.split(".")):
            raise ValueError("Regla PSL no válida")
        rules.add(prefix + domain)
    if len(rules) < 9000 or not {"com", "com.es", "co.uk", "*.ck", "!www.ck", "github.io"} <= rules:
        raise ValueError("PSL incompleta")
    snapshot = {
        "source": SOURCE,
        "version": re.search(r"^// VERSION: (.+)$", text, re.M).group(1),
        "commit": re.search(r"^// COMMIT: (.+)$", text, re.M).group(1),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "license_notice": text.split("\n\n", 1)[0],
        "sections": ["ICANN", "PRIVATE"],
        "rules": sorted(rules),
    }
    output = ROOT / "docs/PUBLIC_SUFFIX_LIST.json"
    output.write_text(json.dumps(snapshot, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print("PSL %s: %d reglas en %s" % (snapshot["version"], len(rules), output))


if __name__ == "__main__":
    main()

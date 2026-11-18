"""Offline version/notice inventory. Does not decide legal compatibility.

Inputs are `go mod download -json all`, `go list -deps -json ./cmd/server`,
and the reviewed Connect package-lock.json. No credentials/network access.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def documents(path):
    data = Path(path).read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    while data.strip():
        value, end = decoder.raw_decode(data.lstrip())
        yield value
        data = data.lstrip()[end:]


def license_family(text):
    """Labels for navigation only; retained text, not this heuristic, is authority."""
    text = re.sub(r"\s+", " ", text.lower())
    labels = []
    if "mozilla public license" in text:
        labels.append("MPL-2.0")
    if "gnu general public license" in text and "version 3" in text:
        labels.append("GPL-3.0 (see exact terms)")
    if "apache license" in text and "version 2.0" in text:
        labels.append("Apache-2.0")
    if "permission is hereby granted, free of charge" in text:
        labels.append("MIT-family")
    if "permission to use, copy, modify, and" in text:
        labels.append("ISC-family")
    if "redistribution and use in source and binary forms" in text:
        labels.append("BSD-family")
    return "; ".join(labels) or "Manual review required"


def inventory(downloads, packages, lock, output):
    runtime = {p.get("Module", {}).get("Path") for p in documents(packages)}
    output.mkdir(parents=True, exist_ok=True)
    notices = output / "notices"
    notices.mkdir(exist_ok=True)
    modules = []
    for module in documents(downloads):
        root = Path(module["Dir"])
        evidence = []
        # Keep nested vendored/third-party notices as well as the module's licence.
        for source in sorted(root.rglob("*")):
            name = source.name.upper()
            if not source.is_file() or not name.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                continue
            content = source.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            target = notices / (digest + ".txt")
            if not target.exists():
                target.write_bytes(content)
            evidence.append({"path": source.relative_to(root).as_posix(), "sha256": digest,
                             "retained_notice": "notices/" + target.name})
        primary = [e for e in evidence if e["path"].upper() in {"LICENSE", "LICENSE.TXT", "LICENSE.MD", "COPYING"}]
        labels = license_family("\n".join((output/e["retained_notice"]).read_text(errors="replace") for e in primary))
        modules.append({"module": module["Path"], "version": module["Version"],
                        "sum": module.get("Sum"), "go_mod_sum": module.get("GoModSum"),
                        "used_by_server_build": module["Path"] in runtime,
                        "primary_notice_family_hint": labels,
                        "license_evidence": evidence,
                        "compatibility_review": "not_determined_by_inventory"})
    node = json.loads(Path(lock).read_text(encoding="utf-8-sig"))
    node_packages = [{"path": name, "version": value.get("version"),
                      "declared_license": value.get("license"), "dev": value.get("dev", False),
                      "optional": value.get("optional", False), "integrity": value.get("integrity")}
                     for name, value in sorted(node.get("packages", {}).items()) if name]
    for name, data in (("go-module-inventory.json", modules), ("connect-lock-inventory.json", node_packages)):
        (output / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    table = ["# Go dependency versions and notice families", "",
             "Family hints are generated for navigation, not a legal compatibility decision. Read all retained notices for each module, including third-party material. See LICENSES.md for the unresolved gates.", "",
             "| Module | Exact version | Server build | Primary notice family |",
             "|---|---|---|---|"]
    for module in modules:
        table.append(f"| `{module['module']}` | `{module['version']}` | {'yes' if module['used_by_server_build'] else 'tool/test/transitive only'} | {module['primary_notice_family_hint']} |")
    (output/"GO-LICENSE-TABLE.md").write_text("\n".join(table)+"\n", encoding="utf-8")
    print(json.dumps({"go_modules": len(modules), "server_modules": sum(m["used_by_server_build"] for m in modules),
                      "modules_without_notices": [m["module"] for m in modules if not m["license_evidence"]],
                      "connect_packages": len(node_packages), "notices": len(list(notices.glob("*.txt")))}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--downloads", required=True)
    p.add_argument("--packages", required=True)
    p.add_argument("--connect-lock", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    inventory(args.downloads, args.packages, args.connect_lock, args.output)

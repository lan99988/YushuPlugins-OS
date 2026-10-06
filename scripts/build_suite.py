"""Generate independent, locked packages from reviewed sources; no private data."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

import yaml
from yushuos.deployment import lock_plugin
from yushuos.manifest import load_manifest
from business_runtime.contracts import REGISTRY, manifest, events

ROOT = Path(__file__).resolve().parents[1]


def stage(slug, output, *, bootstrap=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "plugin.yaml").write_text(yaml.safe_dump(manifest(slug), allow_unicode=True, sort_keys=False),
                                        encoding="utf-8", newline="\n")
    package = output / "business_runtime"
    package.mkdir(exist_ok=True)
    for source in sorted((ROOT / "business_runtime").glob("*.py")):
        shutil.copyfile(source, package / source.name)
    (output / "run.py").write_text(bootstrap or f"from business_runtime.adapter import main\nraise SystemExit(main({slug!r}))\n",
                                   encoding="utf-8", newline="\n")
    (output / "README.md").write_text(f"# YushuOS {slug}\n\nPython 3.11+ / Core 0.3.1. See SKILL.md for JSON invocation.\n"
                                      "Private SQLite belongs to this plugin. Uninstall preserves data.\n", encoding="utf-8", newline="\n")
    (output / "SKILL.md").write_text(f"---\nname: yushuos-{slug}\ndescription: Use installed YushuOS {slug} capabilities via Core.\n---\n\n"
        "Read `yushuos catalog`. Use declared query/command intent and JSON fields. "
        f"Bind target.store_id to {slug}_store. Preview before authorized writes. "
        "Keep request_id stable. Unknown writes require Core resume, never a new mutation. "
        "Host-required abilities accept explicit host judgement and evidence; no hidden model call.\n", encoding="utf-8", newline="\n")
    (output / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    checked = lock_plugin(output)
    if checked.get("status") != "succeeded" or not load_manifest(output / "plugin.yaml").package_hash_verified:
        raise ValueError("Package lock failed")
    lock = output / "plugin.lock.json"
    lock.write_bytes(lock.read_bytes().replace(b"\r\n", b"\n"))
    return output


def archive(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as zipped:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError("Package symlink rejected")
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            entry = ZipInfo("plugin/" + path.relative_to(source).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            entry.create_system = 3
            zipped.writestr(entry, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def contract_files(slug, caps, event_names):
    path = ROOT / "contracts" / slug
    path.mkdir(parents=True, exist_ok=True)
    values = {"capabilities": {"contract_version": 3, "plugin": slug, "provider_id": "yushuos." + slug,
                                "capabilities": list(caps.values())},
              "events": {"plugin": slug, "events": [{"name": name, "payload": "reference-only"} for name in event_names]},
              "resources": {"plugin": slug, "reference": {"required": ["provider", "kind", "id", "store_id"]},
                            "scope": {"target.store_id": slug + "_store"}}}
    for name, value in values.items():
        (path / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def build(output, *, apps=True):
    output = Path(output)
    index = []
    for slug in REGISTRY:
        plugin = stage(slug, ROOT / "plugins" / slug / "plugin")
        contract_files(slug, REGISTRY[slug], events(slug))
        destination = output / ("yushuos-" + slug.replace("_", "-") + "-0.1.0.zip")
        digest = archive(plugin, destination)
        index.append({"plugin": "yushuos." + slug, "version": "0.1.0", "file": destination.name,
                      "sha256": digest, "validation": {"mock": "pending", "live_read": "not_applicable", "live_write": "not_applicable"}})
    task = ROOT / "plugins" / "task"
    sys.path.insert(0, str(task / "src"))
    from yushuos_task.contracts import CAPABILITIES, TASK_EVENTS
    contract_files("task", CAPABILITIES, TASK_EVENTS)
    subprocess.run([sys.executable, str(task / "scripts" / "build_plugin.py"), "--output", str(output / "yushuos-task-0.2.0.zip")], check=True)
    index.append({"plugin": "yushuos.task", "version": "0.2.0", "file": "yushuos-task-0.2.0.zip",
                  "sha256": hashlib.sha256((output / "yushuos-task-0.2.0.zip").read_bytes()).hexdigest(),
                  "validation": {"mock": "pending", "live_read": "not_applicable", "live_write": "not_applicable"}})
    if apps:
        for slug in ("feishu", "ima"):
            folder = ROOT / "plugins" / slug
            module = importlib.import_module("plugins." + slug + ".adapter")
            plugin = folder / "plugin"
            plugin.mkdir(exist_ok=True)
            for source in folder.glob("*.py"):
                shutil.copyfile(source, plugin / source.name)
            for name in ("README.md", "SKILL.md"):
                if (folder / name).is_file():
                    shutil.copyfile(folder / name, plugin / name)
            shutil.copyfile(ROOT / "LICENSE", plugin / "LICENSE")
            (plugin / "app-descriptor.json").write_text(json.dumps(module.descriptor(), indent=2) + "\n", encoding="utf-8", newline="\n")
            (plugin / "plugin.yaml").write_text(yaml.safe_dump(module.MANIFEST, sort_keys=False), encoding="utf-8", newline="\n")
            (plugin / "run.py").write_text("from adapter import main\nraise SystemExit(main())\n", encoding="utf-8", newline="\n")
            lock_plugin(plugin)
            if not load_manifest(plugin / "plugin.yaml").package_hash_verified:
                raise ValueError("App package failed lock validation")
            (plugin / "plugin.lock.json").write_bytes((plugin / "plugin.lock.json").read_bytes().replace(b"\r\n", b"\n"))
            destination = output / ("yushuos-" + slug + "-0.1.0.zip")
            contract_files(slug, {cap["name"]: cap for cap in module.MANIFEST["capabilities"]}, [])
            index.append({"plugin": "yushuos." + slug, "version": "0.1.0", "file": destination.name,
                          "sha256": archive(plugin, destination),
                          "validation": {"mock": "pending", "live_read": "not_verified", "live_write": "not_verified"}})
    (output / "suite-manifest.json").write_text(json.dumps({"format": 1, "core": "0.3.1", "plugins": index}, indent=2) + "\n", encoding="utf-8", newline="\n")
    (output / "SHA256SUMS").write_text("\n".join(f"{p['sha256']}  {p['file']}" for p in index) + "\n", encoding="utf-8", newline="\n")
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--without-apps", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.output, apps=not args.without_apps)))

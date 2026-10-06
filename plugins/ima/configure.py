"""Bind this App to an existing private Core root, without granting permissions."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
import yaml
from yushuos.config import load_config
from yushuos.deployment import install_plugin
from yushuos.manifest import load_manifest
from yushuos.registry import provider_digest
try:
    from .adapter import APP, PLUGIN_ID
except ImportError:
    from adapter import APP, PLUGIN_ID


def _ordinary(path):
    path = Path(path).expanduser().absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("linked_path")
    return path


def _outside_repository(path):
    path = _ordinary(path)
    if any((ancestor / ".git").exists() for ancestor in (path, *path.parents)):
        raise ValueError("repository_configuration_forbidden")
    return path


def _json(path, data):
    _atomic(path, json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _atomic(path, content):
    path = _ordinary(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.is_file():
            temporary.unlink()


def _files(root):
    files = {}
    for path in root.rglob("*"):
        _ordinary(path)
        if (not path.is_file() or path.name == "manifest.json" or "__pycache__" in path.relative_to(root).parts
                or path.suffix in (".pyc", ".pyo")):
            continue
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def configure(core_root, *, plugin_root=None, binding_file=None):
    """Install an immutable package and native pointer; retain all account/host grants."""
    root = _outside_repository(core_root)
    source = _ordinary(plugin_root or Path(__file__).resolve().parent)
    if root.is_relative_to(source):
        raise ValueError("configuration_inside_release")
    if not (root / "config.yaml").is_file():
        raise ValueError("existing_core_configuration_required")
    _ordinary(root / "config.yaml")
    loaded = load_config(root)
    spec = load_manifest(source / "plugin.yaml")
    if spec.plugin_id != PLUGIN_ID or spec.plugin_type != "app" or not spec.package_hash_verified:
        raise ValueError("verified_app_package_required")
    configured_ledger = loaded["state"]["ledger_path"]
    if not configured_ledger:
        raise ValueError("existing_core_ledger_required")
    ledger = _ordinary(Path(configured_ledger) if Path(configured_ledger).is_absolute() else root / configured_ledger)
    if not ledger.is_file():
        raise ValueError("existing_core_ledger_required")
    # venv/system Python normally uses symlinks on POSIX. Resolve only this
    # explicitly selected executable; private data/package paths still reject links.
    python = Path(loaded["runtime"]["python_executable"] or sys.executable).expanduser().resolve(strict=True)
    if not python.is_file():
        raise ValueError("python_unavailable")
    account_file = _outside_repository(binding_file or root / "app-bindings" / (APP + ".json"))
    if account_file.is_relative_to(source):
        raise ValueError("binding_inside_release")
    if binding_file is not None:
        data = json.loads(account_file.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("app") != APP:
            raise ValueError("account_binding_invalid")
    elif account_file.exists():
        data = json.loads(account_file.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("app") != APP:
            raise ValueError("account_binding_invalid")
    else:
        data = {"app": APP, "configured": False, "account_ref": "",
                "verified_capabilities": [], "authorized_capabilities": [], "grants": [], "credentials": {}}
    # All validation precedes writes. Never infer or add permissions/resources/account membership.
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8-sig")) or {}
    install_plugin(source, root)
    release = _ordinary(root / "native-apps" / PLUGIN_ID / spec.version)
    expected = {"format": 1, "app": spec.plugin_id.removeprefix("app-"), "version": spec.version, "files": _files(source)}
    if release.exists():
        if not release.is_dir() or _files(release) != expected["files"]:
            raise ValueError("immutable_native_release_mismatch")
        actual_manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8-sig"))
        if actual_manifest != expected:
            raise ValueError("native_manifest_mismatch")
    else:
        release.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, release, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        _json(release / "manifest.json", expected)
    if not account_file.exists():
        _json(account_file, data)
    pointer = _ordinary(root / "native-apps" / PLUGIN_ID / "active.json")
    _json(pointer, {"app": spec.plugin_id.removeprefix("app-"), "version": spec.version,
                    "release": str(release), "config_file": str(account_file), "ledger_path": str(ledger),
                    "python_executable": str(python)})
    raw.setdefault("plugins", {}).setdefault("versions", {})[PLUGIN_ID] = spec.version
    raw["plugins"].setdefault("config", {})[PLUGIN_ID] = {"binding_file": str(account_file)}
    raw.setdefault("bindings", {}).setdefault("apps", {})[PLUGIN_ID] = {"active_pointer": str(pointer)}
    # No write to permissions, resources, project_ref, credential contents, or state ledger.
    _atomic(root / "config.yaml", yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    verified_config = load_config(root)
    installed = load_manifest(root / "plugins" / PLUGIN_ID / spec.version / "plugin.yaml")
    provider_digest(installed, verified_config)
    return {"status": "succeeded", "plugin_id": PLUGIN_ID, "version": spec.version,
            "binding_file": str(account_file), "grants_changed": False, "live_verification": "not_verified"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-root", required=True)
    parser.add_argument("--plugin-root")
    parser.add_argument("--binding-file")
    args = parser.parse_args()
    try:
        out = configure(args.core_root, plugin_root=args.plugin_root, binding_file=args.binding_file)
    except (OSError, ValueError, TypeError, KeyError):
        out = {"status": "failed", "error": "configuration_failed"}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate hashes/locks and optionally install in a fresh isolated environment."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import venv
from zipfile import ZipFile

from yushuos.manifest import load_manifest


def verify(artifacts):
    artifacts = Path(artifacts).resolve()
    index = json.loads((artifacts / "suite-manifest.json").read_text(encoding="utf-8"))
    assert len(index["plugins"]) == 20
    assert len({p["plugin"] for p in index["plugins"]}) == 20
    sums = dict((name, digest) for digest, name in
                (line.split("  ", 1) for line in (artifacts / "SHA256SUMS").read_text().splitlines()))
    with tempfile.TemporaryDirectory(prefix="suite-verify-") as temporary:
        for item in index["plugins"]:
            name = item["file"]
            assert Path(name).name == name and name.endswith(".zip")
            archive = artifacts / name
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == item["sha256"] == sums[name]
            destination = Path(temporary) / item["plugin"]
            with ZipFile(archive) as zipped:
                for entry in zipped.infolist():
                    path = PurePosixPath(entry.filename)
                    assert not path.is_absolute() and ".." not in path.parts and "\\" not in entry.filename
                    assert path.parts[0] == "plugin"
                    assert (entry.external_attr >> 16) & 0o170000 != 0o120000
                zipped.extractall(destination)
            manifest = load_manifest(destination / "plugin/plugin.yaml")
            assert manifest.package_hash_verified and manifest.plugin_id == item["plugin"]
            assert manifest.version == item["version"]
    return index


def clean_install(core_source):
    core_source = Path(core_source).resolve()
    with tempfile.TemporaryDirectory(prefix="suite-clean-install-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(core_source),
                        "-e", str(Path(__file__).resolve().parents[1])], check=True)
        def call(*args):
            value = subprocess.run([str(python), "-m", "yushuos", "--config-root", str(root / "data"), *args],
                                   capture_output=True, text=True, encoding="utf-8", check=True)
            response = json.loads(value.stdout)
            assert response.get("status") not in {"failed", "error"}, response
            return response
        subprocess.run([str(python), "-m", "yushuos", "--help"], check=True, capture_output=True)
        call("deploy", "--source", str(core_source), "--version", "0.3.1", "--preview")
        call("verify", "--version", "0.3.1")
        call("activate", "--version", "0.3.1")
        call("doctor")
        call("catalog", "--details")
        for host in ("codex", "workbuddy"):
            location = root / host
            call("install-host", "--host", host, "--host-config-root", str(location))
            call("uninstall-host", "--host", host, "--host-config-root", str(location))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=Path("dist"))
    parser.add_argument("--core-source", type=Path)
    parser.add_argument("--clean-install", action="store_true")
    options = parser.parse_args()
    result = verify(options.artifacts)
    if options.clean_install:
        if not options.core_source:
            parser.error("--core-source is required for --clean-install")
        clean_install(options.core_source)
    print(json.dumps({"verified_packages": len(result["plugins"]), "clean_install": options.clean_install}))

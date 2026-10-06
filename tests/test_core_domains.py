import sqlite3
import yaml
from yushuos.runtime import CoreRuntime
from yushuos.deployment import install_plugin
from yushuos_sdk import StateStore
from business_runtime.contracts import REGISTRY
from scripts.build_suite import stage


def installed(tmp_path, slug="capture", bootstrap=None, grants=None):
    source = stage(slug, tmp_path / "source", bootstrap=bootstrap)
    root = tmp_path / "core"
    root.mkdir()
    config = {"schema_version": 1, "plugins": {"versions": {"yushuos." + slug: "0.1.0"}},
              "state": {"ledger_path": "operations.sqlite3"}, "bindings": {"resources": {slug + "_store": "personal"}},
              "permissions": {"grants": grants if grants is not None else [slug + ".read", slug + ".write"]}}
    (root / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    value = install_plugin(source, root)
    assert value["status"] == "succeeded", value
    with StateStore(root / "operations.sqlite3").connect(write=True):
        pass
    return CoreRuntime(root), root


def request(cap, fields, rid="call1", scope="personal"):
    return {"request_id": rid, "capability": cap, "intent": "query" if REGISTRY[cap.split('.')[0].replace('-','_')][cap]["effect"] == "read_only" else "command",
            "fields": fields, "target": {"store_id": scope}}


def invoke(runtime, cap, fields, rid="call1"):
    return runtime.invoke(request(cap, fields, rid), mode="execute", host_mode="execute").to_dict()


def test_core_roundtrip_and_original_replay(tmp_path):
    runtime, root = installed(tmp_path)
    q = request("capture.create", {"content": "PRIVATE-CONTENT", "source": "manual"})
    first = runtime.invoke(q, mode="execute", host_mode="execute").to_dict()
    assert first["status"] == "succeeded", first
    entity = first["data"]["entity"]
    updated = invoke(runtime, "capture.route", {"id": entity["id"], "expected_version": 1, "classification": "task"}, "route1")
    assert updated["status"] == "succeeded", updated
    replay = runtime.invoke(q, mode="execute", host_mode="execute").to_dict()
    assert replay["data"] == first["data"]
    ledger = (root / "operations.sqlite3").read_bytes()
    assert b"PRIVATE-CONTENT" not in ledger


def test_permission_scope_preview_no_private_db(tmp_path):
    runtime, root = installed(tmp_path, grants=["capture.read"])
    q = request("capture.create", {"content": "x", "source": "manual"})
    assert runtime.invoke(q, mode="execute", host_mode="execute").status != "succeeded"
    assert not list((root / "plugin-data").rglob("*.sqlite3"))
    runtime2, root2 = installed(tmp_path / "preview")
    assert runtime2.invoke(q, mode="preview", host_mode="readonly").status == "preview"
    assert not list((root2 / "plugin-data").rglob("*.sqlite3"))
    assert runtime2.invoke(request("capture.create", q["fields"], scope="other"), mode="execute", host_mode="execute").status != "succeeded"


def test_commit_before_confirmation_recovers_once(tmp_path):
    bootstrap = '''import json, sys, os, io
raw=sys.stdin.read(); envelope=json.loads(raw)
if envelope.get("action","invoke")=="invoke":
 from yushuos_sdk import StateStore
 def crash(*args,**kwargs): os._exit(71)
 StateStore.record_with_events=crash
sys.stdin=io.StringIO(raw)
from business_runtime.adapter import main
raise SystemExit(main("capture"))
'''
    runtime, root = installed(tmp_path, bootstrap=bootstrap)
    q = request("capture.create", {"content": "one", "source": "manual"})
    assert runtime.invoke(q, mode="execute", host_mode="execute").status == "unknown"
    resumed = runtime.resume(q, host_mode="execute")
    assert resumed.status == "succeeded", resumed
    private = next((root / "plugin-data").rglob("capture.sqlite3"))
    with sqlite3.connect(private) as db:
        assert db.execute("select count(*) from entities").fetchone()[0] == 1
        assert db.execute("select count(*) from commits").fetchone()[0] == 1
    assert runtime.resume(q, host_mode="execute").status == "succeeded"

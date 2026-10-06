"""Each packaged local provider proves its own Core commit/recovery join."""
import sqlite3
import pytest
from tests.test_core_domains import installed, request
from tests.test_domain_lifecycles import SAMPLES


@pytest.mark.parametrize("slug", SAMPLES)
def test_each_provider_confirm_crash_duplicate_and_privacy(tmp_path, slug):
    bootstrap = '''import json,sys,os,io
raw=sys.stdin.read(); envelope=json.loads(raw)
if envelope.get("action","invoke")=="invoke":
 from yushuos_sdk import StateStore
 def crash(*args,**kwargs): os._exit(71)
 StateStore.record_with_events=crash
sys.stdin=io.StringIO(raw)
from business_runtime.adapter import main
raise SystemExit(main(SLUG))
'''.replace("SLUG", repr(slug))
    runtime, root = installed(tmp_path, slug, bootstrap=bootstrap)
    cap, fields, _ = SAMPLES[slug]
    q = request(cap, fields, "join-crash")
    assert runtime.invoke(q, mode="execute", host_mode="execute").status == "unknown"
    recovered = runtime.resume(q, host_mode="execute")
    assert recovered.status == "succeeded", recovered
    assert runtime.resume(q, host_mode="execute").to_dict()["data"] == recovered.to_dict()["data"]
    database = next((root / "plugin-data").rglob(slug + ".sqlite3"))
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from entities").fetchone()[0] == 1
        assert db.execute("select count(*) from commits").fetchone()[0] == 1
    wrong = {**q, "fields": {**fields, next(iter(fields)): "DIFFERENT"}}
    assert runtime.resume(wrong, host_mode="execute").status != "succeeded"
    with sqlite3.connect(root / "operations.sqlite3") as db:
        # Core stores hashes and resource references, never the business snapshot.
        sql = " ".join(str(row) for row in db.iterdump())
        assert '"fields"' not in sql and '"entity"' not in sql


@pytest.mark.parametrize("slug", SAMPLES)
def test_each_provider_grant_scope_preview(tmp_path, slug):
    runtime, root = installed(tmp_path, slug, grants=[slug + ".read"])
    cap, fields, _ = SAMPLES[slug]
    q = request(cap, fields)
    assert runtime.invoke(q, mode="execute", host_mode="execute").status != "succeeded"
    assert not list((root / "plugin-data").rglob("*.sqlite3"))
    runtime, root = installed(tmp_path / "preview", slug)
    assert runtime.invoke(q, mode="preview", host_mode="readonly").status == "preview"
    assert not list((root / "plugin-data").rglob("*.sqlite3"))
    assert runtime.invoke(request(cap, fields, scope="other"), mode="execute", host_mode="execute").status != "succeeded"

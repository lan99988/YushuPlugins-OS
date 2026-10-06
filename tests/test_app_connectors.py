import importlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["feishu", "ima"])
def test_adapter_exists(name):
    assert (ROOT / "plugins" / name / "adapter.py").is_file(), "App adapter 尚未实现"


@pytest.fixture(params=["feishu", "ima"])
def app(request):
    path = ROOT / "plugins" / request.param / "adapter.py"
    assert path.is_file(), "App adapter 尚未实现"
    return importlib.import_module("plugins." + request.param + ".adapter")


def envelope(app, name, fields, tmp_path, action="invoke"):
    request = {
        "request_id": "same-id",
        "capability": name,
        "intent": app.CAPABILITIES[name]["intents"][0],
        "fields": fields,
        "target": {},
        "project_ref": "demo",
    }
    context = {
        "schema_version": 1,
        "plugin_id": app.PLUGIN_ID,
        "plugin_version": "0.1.0",
        "provider_digest": "a" * 64,
        "project_ref": "demo",
        "request_id": "same-id",
        "state_ledger_path": "",
        "data_path": str(tmp_path),
        "emitted_events": [],
        "run_id": "",
        "root_event_id": "",
        "causation_id": "",
        "depth": 0,
        "mode": "execute",
        "host_mode": "execute",
        "resources": {
            "tasklist": ["tl"],
            "calendar": ["cal"],
            "recipient": ["chat"],
            "note": ["n"],
            "notebook": ["folder"],
            "kb": ["kb"],
        },
    }
    return {
        "protocol": "json-stdio-v2",
        "action": action,
        "plugin_id": app.PLUGIN_ID,
        "plugin_version": "0.1.0",
        "capability": name,
        "capability_effect": app.CAPABILITIES[name]["effect"],
        "request": request,
        "context": context,
        "mode": "execute",
        "host_mode": "execute",
    }


def binding(app):
    return {
        "configured": True,
        "account_ref": "self",
        "verified_capabilities": list(app.CAPABILITIES),
        "authorized_capabilities": list(app.CAPABILITIES),
        "grants": list(
            {p for c in app.CAPABILITIES.values() for p in c["permissions"]}
        ),
        "credentials": {"client_id": "PRIVATECLIENT", "api_key": "PRIVATEKEY"},
    }


def write_request(app, tmp_path):
    if app.APP == "feishu":
        return envelope(
            app,
            "feishu.message.send",
            {"receive_id": "chat", "receive_id_type": "chat_id", "text": "你好"},
            tmp_path,
        )
    return envelope(
        app, "ima.note.create", {"folder_id": "folder", "content": "你好"}, tmp_path
    )


def reply(app):
    return (
        {"message": {"message_id": "m"}}
        if app.APP == "feishu"
        else {"code": 0, "data": {"note_id": "n"}}
    )


def test_descriptor_is_core_valid_and_no_domain_names(app):
    from yushuos.app_descriptor import validate_app_descriptor

    descriptor = validate_app_descriptor(app.descriptor())
    assert all(c["id"].startswith(app.APP + ".") for c in descriptor["capabilities"])
    assert all(
        c["effect"] in ("read_only", "external_write")
        for c in descriptor["capabilities"]
    )
    assert not any("document." in c["id"] for c in descriptor["capabilities"])


def test_unconfigured_or_unverified_is_unavailable_without_call(app, tmp_path):
    req = write_request(app, tmp_path)
    for config in ({}, {**binding(app), "verified_capabilities": []}):
        calls = []
        out = app.handle_envelope(
            req, binding=config, transport=lambda *a, calls=calls, **k: calls.append(a)
        )
        assert out["status"] == "unavailable"
        assert calls == []


def test_remote_receipt_preclaims_and_replays_once(app, tmp_path):
    req = write_request(app, tmp_path)
    calls = []

    def remote(*args, **kw):
        calls.append(args)
        import sqlite3

        with sqlite3.connect(tmp_path / "external-receipts.sqlite3") as db:
            assert db.execute("select state from receipts").fetchone()[0] == "claimed"
        return reply(app)

    first = app.handle_envelope(req, binding=binding(app), transport=remote)
    assert first["status"] == "succeeded"
    assert first["data"]["operation_status"] == "confirmed"
    assert app.handle_envelope(req, binding=binding(app), transport=remote) == first
    assert len(calls) == 1
    req["request"]["fields"]["text" if app.APP == "feishu" else "content"] = "不同"
    assert (
        app.handle_envelope(req, binding=binding(app), transport=remote)["error"][
            "code"
        ]
        == "request_conflict"
    )
    assert len(calls) == 1


def test_timeout_unknown_recover_does_not_resubmit(app, tmp_path):
    req = write_request(app, tmp_path)
    calls = []

    def timeout(*a, **kw):
        calls.append(a)
        raise TimeoutError("PRIVATEKEY")

    out = app.handle_envelope(req, binding=binding(app), transport=timeout)
    assert out["status"] == "unknown"
    req["action"] = "recover"
    recovered = app.handle_envelope(req, binding=binding(app), transport=timeout)
    assert recovered["status"] == "unknown"
    assert len(calls) == 1
    assert "PRIVATEKEY" not in json.dumps(out)


def test_scope_and_permissions_fail_closed(app, tmp_path):
    req = write_request(app, tmp_path)
    req["context"]["resources"] = {}
    out = app.handle_envelope(
        req, binding=binding(app), transport=lambda *a: pytest.fail("scope bypass")
    )
    assert out["error"]["code"] == "permission_denied"
    req = write_request(app, tmp_path)
    out = app.handle_envelope(req, binding={**binding(app), "grants": []})
    assert out["status"] == "unavailable"


def test_context_mode_mismatch_and_local_commit_rejected(app, tmp_path):
    req = write_request(app, tmp_path)
    req["context"]["host_mode"] = "readonly"
    assert (
        app.handle_envelope(req, binding=binding(app))["error"]["code"]
        == "invalid_input"
    )
    req = write_request(app, tmp_path)
    req["context"].update(
        schema_version=2,
        intent=req["request"]["intent"],
        operation_support="local_commit_v1",
        fingerprint_scheme="jcs-operation-v1",
    )
    assert (
        app.handle_envelope(req, binding=binding(app))["error"]["code"]
        == "invalid_input"
    )


def test_secret_redaction_and_malformed_success_unknown(app, tmp_path):
    req = write_request(app, tmp_path)
    out = app.handle_envelope(req, binding=binding(app), transport=lambda *a, **k: {})
    assert out["status"] == "unknown"
    assert "PRIVATEKEY" not in json.dumps(out)


def test_preview_has_no_claim_or_transport(app, tmp_path):
    req = write_request(app, tmp_path)
    req["mode"] = req["context"]["mode"] = "preview"
    out = app.handle_envelope(
        req, binding=binding(app), transport=lambda *a: pytest.fail("preview wrote")
    )
    assert out["status"] == "preview"
    assert not (tmp_path / "external-receipts.sqlite3").exists()


def test_pagination_and_rate_limit(app, tmp_path):
    if app.APP == "feishu":
        req = envelope(
            app, "feishu.task.list", {"tasklist_guid": "tl", "limit": 2}, tmp_path
        )
        pages = [
            {
                "items": [{"guid": "t", "summary": "任务"}],
                "has_more": True,
                "page_token": "next",
            },
            {"items": [], "has_more": False},
        ]
    else:
        req = envelope(
            app, "ima.note.search", {"query": "词", "limit": 2, "start": 0}, tmp_path
        )
        req["context"]["resources"]["account"] = ["self"]
        pages = [
            {
                "code": 0,
                "data": {
                    "search_note_infos": [{"note_id": "n", "title": "题"}],
                    "is_end": False,
                },
            },
            {"code": 0, "data": {"search_note_infos": [], "is_end": True}},
        ]
    calls = []

    def transport(*args, **kwargs):
        calls.append(args)
        return pages.pop(0)

    first = app.handle_envelope(req, binding=binding(app), transport=transport)
    assert first["status"] == "succeeded"
    assert first["data"]["next_cursor"]
    req["request"]["fields"]["cursor"] = first["data"]["next_cursor"]
    second = app.handle_envelope(req, binding=binding(app), transport=transport)
    assert second["data"]["next_cursor"] is None

    def limited(*a, **k):
        raise app.ProviderError("rate_limited", retryable=True)

    out = app.handle_envelope(req, binding=binding(app), transport=limited)
    assert out["error"]["code"] == "rate_limited" and out["error"]["retryable"]
    assert len(calls) == 2


def test_cli_is_json_and_unknown_capability_unavailable(app):
    proc = subprocess.run(
        [__import__("sys").executable, str(ROOT / "plugins" / app.APP / "adapter.py")],
        input="{}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    out = json.loads(proc.stdout)
    assert out["status"] == "failed"
    assert set(out) == {"status", "request_id", "message", "resource", "data", "error"}


def test_interrupt_leaves_claim_and_never_resubmits(app, tmp_path):
    req = write_request(app, tmp_path)

    def interrupt(*args):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        app.handle_envelope(req, binding=binding(app), transport=interrupt)
    req["action"] = "recover"
    out = app.handle_envelope(
        req,
        binding=binding(app),
        transport=lambda *a: pytest.fail("rewrite after crash"),
    )
    assert out["status"] == "unknown"


def test_receipt_failure_after_remote_success_stays_unknown(app, tmp_path, monkeypatch):
    import sqlite3

    req = write_request(app, tmp_path)

    def cannot_persist(*args):
        raise sqlite3.OperationalError("disk full PRIVATEKEY")

    monkeypatch.setattr(app.p.Receipts, "finish", cannot_persist)
    out = app.handle_envelope(
        req, binding=binding(app), transport=lambda *a: reply(app)
    )
    assert out["status"] == "unknown"
    assert "PRIVATEKEY" not in json.dumps(out)
    out = app.handle_envelope(
        req,
        binding=binding(app),
        transport=lambda *a: pytest.fail("rewrite without receipt"),
    )
    assert out["status"] == "unknown"


def test_account_scope_matches_binding(app, tmp_path):
    if app.APP != "ima":
        return
    req = envelope(app, "ima.note.search", {"query": "词"}, tmp_path)
    req["context"]["resources"]["account"] = ["other-account"]
    out = app.handle_envelope(
        req, binding=binding(app), transport=lambda *a: pytest.fail("wrong account")
    )
    assert out["error"]["code"] == "permission_denied"


def test_secret_provider_fields_are_redacted(app, tmp_path):
    if app.APP == "ima":
        req = envelope(app, "ima.note.get", {"note_id": "n"}, tmp_path)
        response = {
            "code": 0,
            "data": {
                "content": "PRIVATEKEY PRIVATECLIENT",
                "url_info": {"url": "https://private"},
            },
        }
    else:
        req = envelope(
            app, "feishu.task.get", {"tasklist_guid": "tl", "task_guid": "t"}, tmp_path
        )
        response = {
            "task": {
                "guid": "t",
                "tasklists": [{"tasklist_guid": "tl"}],
                "summary": "PRIVATEKEY",
                "authorization": "PRIVATEKEY",
            }
        }
    out = app.handle_envelope(req, binding=binding(app), transport=lambda *a: response)
    assert out["status"] == "succeeded"
    assert "PRIVATEKEY" not in json.dumps(out) and "PRIVATECLIENT" not in json.dumps(
        out
    )


def test_real_transport_contract_with_fake_cli_http(app, tmp_path, monkeypatch):
    req = write_request(app, tmp_path)
    if app.APP == "feishu":
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            assert kwargs["shell"] is False
            assert args[1:4] == ["im", "messages", "create"]
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"code": 0, "data": {"message_id": "m"}}), ""
            )

        monkeypatch.setattr(app.subprocess, "run", fake_run)
    else:
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return json.dumps(reply(app)).encode()

        def fake_open(request, **kwargs):
            calls.append(request)
            assert request.full_url == "https://ima.qq.com/openapi/note/v1/import_doc"
            assert json.loads(request.data)["folder_id"] == "folder"
            return Response()

        monkeypatch.setattr(app, "urlopen", fake_open)
    out = app.handle_envelope(req, binding=binding(app))
    assert out["status"] == "succeeded" and len(calls) == 1


def test_external_binding_file_loads_and_cli_is_fail_closed(app, tmp_path):
    req = write_request(app, tmp_path)
    config = tmp_path / "binding.json"
    config.write_text(
        json.dumps({**binding(app), "app": app.APP, "verified_capabilities": []}),
        encoding="utf-8",
    )
    req["plugin_config"] = {"binding_file": str(config)}
    proc = subprocess.run(
        [__import__("sys").executable, str(ROOT / "plugins" / app.APP / "adapter.py")],
        input=json.dumps(req) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    out = json.loads(proc.stdout)
    assert out["status"] == "unavailable" and out["error"]["code"] == "not_verified"
    assert "PRIVATEKEY" not in proc.stdout + proc.stderr


def test_manifest_is_core_loadable_with_lock(app, tmp_path):
    import yaml
    from yushuos.deployment import lock_plugin
    from yushuos.manifest import load_manifest

    manifest = tmp_path / "plugin.yaml"
    manifest.write_text(yaml.safe_dump(app.MANIFEST, sort_keys=False), encoding="utf-8")
    assert lock_plugin(tmp_path)["verified"]
    spec = load_manifest(manifest)
    assert spec.package_hash_verified
    assert spec.operation_support is None
    assert all(c.effect in ("external_write", "read_only") for c in spec.capabilities)


def test_actual_core_runner_passes_binding_and_scope_context(app, tmp_path):
    import shutil

    import yaml
    from yushuos.deployment import lock_plugin
    from yushuos.manifest import load_manifest
    from yushuos.registry import CapabilityBinding
    from yushuos.runtime import PluginRunner
    from yushuos_sdk import Request

    release = tmp_path / "release"
    release.mkdir()
    for name in ("adapter.py", "definition.py", "protocol.py"):
        shutil.copy(ROOT / "plugins" / app.APP / name, release / name)
    (release / "run.py").write_text(
        "from adapter import main\nmain()\n", encoding="utf-8"
    )
    (release / "plugin.yaml").write_text(yaml.safe_dump(app.MANIFEST), encoding="utf-8")
    assert lock_plugin(release)["verified"]
    spec = load_manifest(release / "plugin.yaml")
    request = write_request(app, tmp_path)
    config_path = tmp_path / "private-binding.json"
    config_path.write_text(
        json.dumps({**binding(app), "app": app.APP, "verified_capabilities": []}),
        encoding="utf-8",
    )
    cap = next(c for c in spec.capabilities if c.name == request["capability"])
    config = {
        "_config_root": str(tmp_path / "core"),
        "plugins": {"config": {app.PLUGIN_ID: {"binding_file": str(config_path)}}},
        "bindings": {"resources": request["context"]["resources"]},
    }
    runner = PluginRunner(config)
    out = runner.invoke(
        CapabilityBinding(spec, cap, True, ()),
        Request(**request["request"]),
        mode="execute",
        host_mode="execute",
    )
    assert out.status == "unavailable" and out.error["code"] == "not_verified"


def test_invalid_calendar_range_never_claims_or_calls(tmp_path):
    app = importlib.import_module("plugins.feishu.adapter")
    req = envelope(
        app,
        "feishu.calendar.event.create",
        {
            "calendar_id": "cal",
            "summary": "会",
            "start_at": "2026-10-06T11:00:00+08:00",
            "end_at": "2026-10-06T10:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
        tmp_path,
    )
    out = app.handle_envelope(
        req, binding=binding(app), transport=lambda *a: pytest.fail("invalid wrote")
    )
    assert out["status"] == "failed" and out["error"]["code"] == "invalid_input"
    assert not (tmp_path / "external-receipts.sqlite3").exists()


def test_feishu_binding_report_is_actual_context(tmp_path):
    app = importlib.import_module("plugins.feishu.adapter")
    assert "feishu.resource.bindings.get" in app.CAPABILITIES
    req = envelope(app, "feishu.resource.bindings.get", {}, tmp_path)
    out = app.handle_envelope(
        req,
        binding=binding(app),
        transport=lambda *a: pytest.fail("bindings made remote call"),
    )
    assert out["data"]["bindings"]["tasklist"] == ["tl"]


def test_concurrent_duplicates_have_only_one_remote_attempt(app, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    req = write_request(app, tmp_path)
    entered, release = Event(), Event()
    calls = []

    def remote(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return reply(app)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            app.handle_envelope, req, binding=binding(app), transport=remote
        )
        assert entered.wait(3)
        second = app.handle_envelope(req, binding=binding(app), transport=remote)
        assert second["status"] == "unknown"
        release.set()
        assert first.result()["status"] == "succeeded"
    assert len(calls) == 1


def test_feishu_provider_key_is_bounded_and_project_scoped(tmp_path):
    app = importlib.import_module("plugins.feishu.adapter")
    req = write_request(app, tmp_path)
    req["request"]["request_id"] = req["context"]["request_id"] = "x" * 100
    keys = []

    def remote(command, params, body, write):
        keys.append(body["uuid"])
        return reply(app)

    assert (
        app.handle_envelope(req, binding=binding(app), transport=remote)["status"]
        == "succeeded"
    )
    req["request"]["project_ref"] = req["context"]["project_ref"] = "other-project"
    assert (
        app.handle_envelope(req, binding=binding(app), transport=remote)["status"]
        == "succeeded"
    )
    assert all(len(key) <= 50 for key in keys)
    assert keys[0] != keys[1]

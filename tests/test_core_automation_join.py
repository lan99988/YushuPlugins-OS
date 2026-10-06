import json
from pathlib import Path
import pytest
from yushuos.automation import AutomationEngine
from tests.test_core_domains import installed


def automation(tmp_path, slug="capture", host=False):
    runtime, root = installed(tmp_path, slug)
    actions = root / "plugin-data" / ("yushuos." + slug) / "data/actions"
    actions.mkdir(parents=True)
    if host:
        action = {"capability": "review.interpret", "intent": "command", "fields": {"id": "r", "expected_version": 1,
                  "reason": "explicit", "judgement": "tentative", "evidence_refs": [{"provider": "yushuos.review", "kind": "review", "id": "r", "store_id": "personal"}]}, "target": {"store_id": "personal"}}
    else:
        action = {"capability": "capture.create", "intent": "command", "fields": {"content": "AUTOMATION-PRIVATE-BODY", "source": "manual"}, "target": {"store_id": "personal"}}
    (actions / "sample.json").write_text(json.dumps(action), encoding="utf-8")
    engine = AutomationEngine(runtime, clock=lambda: "2026-10-06T00:00:00Z")
    rule = {"schema_version": 1, "id": "join-rule", "trigger": {"type": "manual"}, "action": {"plugin_id": "yushuos." + slug, "action_ref": "sample"}}
    return engine, rule, root


def test_core_automation_same_occurrence_and_expired_authorization(tmp_path):
    engine, rule, root = automation(tmp_path)
    engine.add(rule)
    assert engine.run(rule["id"], invocation_id="denied")["status"] == "blocked"
    engine.grant(rule["id"])
    first = engine.run(rule["id"], invocation_id="once")
    assert first["status"] == "succeeded", first
    assert engine.run(rule["id"], invocation_id="once")["run_id"] == first["run_id"]
    engine.clock = lambda: "2026-12-06T00:00:00Z"
    assert engine.run(rule["id"], invocation_id="expired")["status"] == "blocked"
    assert b"AUTOMATION-PRIVATE-BODY" not in Path(engine.store.path).read_bytes()
    assert b"AUTOMATION-PRIVATE-BODY" not in (root / "operations.sqlite3").read_bytes()


def test_core_event_delivery_dedupe_and_overlap(tmp_path):
    engine, rule, _ = automation(tmp_path)
    rule.update(enabled=True, trigger={"type": "event", "source": "core.cli", "event_type": "demo.created"})
    engine.add(rule)
    engine.grant(rule["id"])
    with pytest.raises(ValueError):
        engine.publish({"type": "demo.created", "source_plugin": "yushuos.task"})
    event = engine.publish({"id": "same-event", "type": "demo.created"})
    queued = engine.tick(execute=False)["runs"][0]
    assert engine.store.claim_run(queued["run_id"], "busy-worker", engine.now())
    engine.publish({"id": "second-event", "type": "demo.created"})
    assert engine.tick(execute=False)["runs"][0]["status"] == "overlap_skipped"
    engine.store.publish_event(event)
    assert engine.tick(execute=False)["runs"] == []


def test_host_required_automation_never_runs_plugin_offline(tmp_path):
    engine, rule, root = automation(tmp_path, "review", host=True)
    engine.add(rule)
    engine.grant(rule["id"])
    assert engine.run(rule["id"], invocation_id="host")["status"] == "host_pending"
    assert not list((root / "plugin-data").rglob("review.sqlite3"))

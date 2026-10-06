from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "task": "task.create task.get task.list task.update task.complete task.reopen task.delete task.cancel task.archive",
    "capture": "capture.create capture.get capture.list capture.route capture.mark_processed capture.archive capture.restore",
    "idea": "idea.create idea.get idea.list idea.update idea.archive idea.discard idea.mark_converted",
    "bug": "bug.create bug.get bug.list bug.update bug.resolve bug.reopen bug.close",
    "finance": "finance.statement.create finance.statement.get finance.statement.list finance.snapshot.create finance.snapshot.get finance.month.close finance.summary.get",
    "relationship": "relationship.person.create relationship.person.get relationship.person.list relationship.person.update relationship.person.archive relationship.interaction.add relationship.interaction.list relationship.status.get",
    "habit": "habit.create habit.get habit.list habit.update habit.archive habit.checkin habit.undo_checkin habit.pause habit.resume",
    "body": "body.state.get body.record.get body.record.get_latest body.history body.sleep.record body.training.record body.recovery.record",
    "lifeadmin": "lifeadmin.create lifeadmin.get lifeadmin.list lifeadmin.update lifeadmin.complete lifeadmin.renewal.record",
    "decision": "decision.create decision.get decision.list decision.update decision.archive decision.option.add decision.choose decision.outcome.record decision.review",
    "feishu": "feishu.calendar.event.create feishu.calendar.event.get feishu.calendar.event.list feishu.calendar.event.update feishu.calendar.event.delete feishu.calendar.busy feishu.task.create feishu.task.get feishu.task.list feishu.task.update feishu.task.complete feishu.binding.create feishu.binding.list feishu.message.send",
    "ima": "ima.note.create ima.note.get ima.note.list ima.note.append ima.note.search ima.notebook.list ima.kb.get ima.kb.list ima.kb.search ima.media.get ima.binding.create",
    "planner": "planner.plan.create planner.plan.get planner.plan.list planner.schedule.generate planner.schedule.preview planner.schedule.apply planner.schedule.replan planner.next_action.suggest",
    "deepwork": "deepwork.start deepwork.pause deepwork.resume deepwork.finish deepwork.cancel deepwork.session.get deepwork.session.list deepwork.interruption.record",
    "competition": "competition.create competition.get competition.list competition.update competition.archive competition.milestone.add competition.progress.record competition.assessment.record",
    "knowledge": "knowledge.create knowledge.get knowledge.list knowledge.update knowledge.archive knowledge.concept.create knowledge.concept.link knowledge.insight.create knowledge.search",
    "creation": "creation.create creation.get creation.list creation.update creation.advance_stage creation.publish creation.archive",
    "review": "review.generate review.get review.list review.finalize review.compare",
    "personal_model": "personal_model.hypothesis.create personal_model.hypothesis.get personal_model.hypothesis.list personal_model.hypothesis.update personal_model.hypothesis.archive personal_model.evidence.attach personal_model.evaluate",
    "cognition": "cognition.analyze cognition.pattern.create cognition.pattern.get cognition.pattern.list cognition.review",
}
SECRET_KEYS = {"secret", "token", "access_token", "refresh_token", "password", "api_key", "client_secret"}
REQUIRED_DESCRIPTOR = {"name", "input", "output", "effect", "intents", "execution_mode", "permissions", "transaction", "errors"}

def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

def test_contract_inventory_has_exact_plugins_and_capabilities():
    assert {p.name for p in (ROOT / "contracts").iterdir() if p.is_dir()} == set(EXPECTED)
    seen = set()
    for slug, names in EXPECTED.items():
        folder = ROOT / "contracts" / slug
        capdoc = json.loads((folder / "capabilities.json").read_text(encoding="utf-8-sig"))
        assert capdoc["plugin"] == slug
        assert capdoc["provider_id"] == f"yushuos.{slug}"
        actual = [cap["name"] for cap in capdoc["capabilities"]]
        assert actual == names.split()
        for cap in capdoc["capabilities"]:
            assert REQUIRED_DESCRIPTOR <= cap.keys()
            assert cap["name"] not in seen
            seen.add(cap["name"])
            for side in ("input", "output"):
                schema = cap[side]
                assert schema["type"] == "object"
                assert schema.get("additionalProperties") is False
                assert set(schema.get("required", [])) <= set(schema.get("properties", {}))
                assert schema.get("properties")
                for obj in walk(schema):
                    if obj.get("type") == "object":
                        assert obj.get("properties")
                        assert obj.get("additionalProperties") is False
            serialized = json.dumps(cap, ensure_ascii=False).lower()
            assert not any(f'"{secret}"' in serialized for secret in SECRET_KEYS)
        for filename, key in (("events.json", "events"), ("resources.json", "resources")):
            document = json.loads((folder / filename).read_text(encoding="utf-8-sig"))
            assert document["plugin"] == slug and document[key]
            assert all(entry.get("name") and entry.get("payload" if key == "events" else "schema") for entry in document[key])

def test_required_special_capability_contracts_are_explicit():
    def get(slug, name):
        doc = json.loads((ROOT / "contracts" / slug / "capabilities.json").read_text(encoding="utf-8-sig"))
        return next(c for c in doc["capabilities"] if c["name"] == name)
    route = get("capture", "capture.route")
    assert route["input"]["properties"]["classification"]["type"] == "string"
    processed = get("capture", "capture.mark_processed")
    assert "reference" in processed["input"]["properties"] and "ignored_reason" in processed["input"]["properties"]
    for name in ("personal_model.evaluate", "cognition.analyze", "cognition.review", "review.generate", "review.compare"):
        slug = name.split(".", 1)[0]
        cap = get(slug, name)
        assert cap["execution_mode"] == "host_required"
    for slug in ("feishu", "ima"):
        doc=json.loads((ROOT/"contracts"/slug/"capabilities.json").read_text(encoding="utf-8-sig"))
        assert all(c["name"].startswith(slug+".") for c in doc["capabilities"])

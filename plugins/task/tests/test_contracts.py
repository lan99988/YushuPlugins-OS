import yaml
import json
from pathlib import Path

import pytest

from yushuos_task.contracts import (
    CAPABILITIES,
    PLUGIN_MANIFEST,
    TARGET_SCHEMAS,
    render_manifest,
    validate_result_data,
)
from yushuos_task.domain import task_from_fields
from yushuos.manifest import load_manifest, validate_schema


def test_contract_declares_exact_capabilities_permissions_and_events():
    assert set(CAPABILITIES) == {
        "task.create", "task.get", "task.list", "task.update",
        "task.complete", "task.reopen", "task.delete", "task.cancel", "task.archive",
    }
    assert CAPABILITIES["task.create"]["permissions"] == ["task.write"]
    assert CAPABILITIES["task.get"]["permissions"] == ["task.read"]
    assert CAPABILITIES["task.update"]["permissions"] == ["task.read", "task.write"]
    assert CAPABILITIES["task.delete"]["permissions"] == ["task.read", "task.delete"]
    assert PLUGIN_MANIFEST["permissions"] == []
    assert PLUGIN_MANIFEST["operation_support"] == "local_commit_v1"
    assert PLUGIN_MANIFEST["emitted_events"] == [
        "task.created", "task.updated", "task.completed", "task.reopened", "task.deleted", "task.cancelled", "task.archived",
    ]
    assert all(item["execution_mode"] == "standalone" for item in CAPABILITIES.values())


def test_manifest_is_inline_generated_from_authoritative_contract_source():
    parsed = yaml.safe_load(render_manifest())
    assert parsed == PLUGIN_MANIFEST
    assert all(item["resource_scopes"] == {"store_id": "task_store"} for item in parsed["capabilities"])
    manifest_path = Path(__file__).resolve().parents[1] / "plugin" / "plugin.yaml"
    assert manifest_path.read_text(encoding="utf-8") == render_manifest()


def test_nullable_output_and_update_fields_are_explicit_in_schema():
    task_schema = CAPABILITIES["task.get"]["outputs"]["properties"]["task"]
    assert task_schema["type"] == "object"
    assert task_schema["properties"]["notes"]["type"] == ["string", "null"]
    update_fields = CAPABILITIES["task.update"]["inputs"]["properties"]["changes"]
    assert update_fields["additionalProperties"] is False
    assert set(update_fields["properties"]) == {
        "title", "notes", "priority", "project_ref", "source_ref", "due_at", "estimate_minutes", "tags",
    }
    assert set(TARGET_SCHEMAS["task.create"]["required"]) == {"store_id"}
    assert set(TARGET_SCHEMAS["task.update"]["required"]) == {"store_id", "task_id"}


def test_core_gate_schema_and_domain_agree_on_trimmed_input_boundaries():
    plugin_manifest = Path(__file__).resolve().parents[1] / "plugin" / "plugin.yaml"
    manifest = load_manifest(plugin_manifest, verify_lock=False)
    create_capability = next(cap for cap in manifest.capabilities if cap.name == "task.create")
    title = "  " + ("x" * 500) + "  "
    project_ref = " " + ("p" * 200) + " "
    tag = " " + ("t" * 64) + " "

    request_fields = {"title": title, "project_ref": project_ref, "tags": [tag]}
    assert validate_schema(create_capability.inputs, request_fields) == ""
    task = task_from_fields("store-a", request_fields, now="2026-01-01T00:00:00Z")
    assert task.title == "x" * 500
    assert task.project_ref == "p" * 200
    assert task.tags == ("t" * 64,)

    update_capability = next(cap for cap in manifest.capabilities if cap.name == "task.update")
    changes_schema = update_capability.inputs["properties"]["changes"]
    assert validate_schema(changes_schema, {"title": title, "project_ref": project_ref, "tags": [tag]}) == ""


def test_output_schemas_require_task_result_shapes_and_fixtures_match_contract():
    fixture_dir = Path(__file__).resolve().parents[1] / "contracts" / "fixtures"
    create_fields = json.loads((fixture_dir / "task-create-fields.json").read_text(encoding="utf-8"))
    available = json.loads((fixture_dir / "task-write-result-available.json").read_text(encoding="utf-8"))
    expired = json.loads((fixture_dir / "task-write-result-expired.json").read_text(encoding="utf-8"))

    assert validate_schema(CAPABILITIES["task.create"]["inputs"], create_fields) == ""
    assert validate_schema(CAPABILITIES["task.create"]["outputs"], available) == ""
    assert validate_schema(CAPABILITIES["task.create"]["outputs"], expired) == ""
    validate_result_data("task.create", available)
    validate_result_data("task.create", expired)
    domain_task = task_from_fields(
        "fixture-store",
        create_fields,
        now="2026-10-05T08:00:00Z",
        task_id=available["task"]["id"],
    )
    assert domain_task.to_dict() == available["task"]

    assert CAPABILITIES["task.create"]["outputs"]["required"] == ["operation_status", "result_state"]
    assert CAPABILITIES["task.get"]["outputs"]["required"] == ["task"]
    assert CAPABILITIES["task.list"]["outputs"]["required"] == ["tasks", "next_cursor"]
    assert validate_schema(CAPABILITIES["task.get"]["outputs"], {})
    assert validate_schema(CAPABILITIES["task.list"]["outputs"], {})


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"operation_status": "committed", "result_state": "available", "task": {}},
        {
            "operation_status": "committed", "result_state": "expired",
            "code": "task.result_expired", "task_id": "tsk_0123456789abcdef0123456789abcdef",
            "original_request_id": "req-1", "changed": False,
        },
        {
            "operation_status": "committed", "result_state": "expired",
            "code": "task.result_expired", "task_id": "tsk_0123456789abcdef0123456789abcdef",
        },
        {
            "operation_status": "committed", "result_state": "expired",
            "code": "task.result_expired", "task_id": "tsk_0123456789abcdef0123456789abcdef",
            "original_request_id": "req-1", "task": {},
        },
    ],
)
def test_write_result_validator_rejects_missing_or_mixed_branches(data):
    with pytest.raises(ValueError):
        validate_result_data("task.create", data)

"""Authoritative Task Contract v3 schemas and inline plugin manifest."""

from copy import deepcopy
from typing import Any

import yaml


PLUGIN_ID = "yushuos.task"
PLUGIN_VERSION = "0.2.0"
FINGERPRINT_SCHEME = "jcs-operation-v1"
OPERATION_SUPPORT = "local_commit_v1"

CAPABILITY_NAMES = (
    "task.create",
    "task.get",
    "task.list",
    "task.update",
    "task.complete",
    "task.reopen",
    "task.delete",
    "task.cancel",
    "task.archive",
)
TASK_EVENTS = (
    "task.created",
    "task.updated",
    "task.completed",
    "task.reopened",
    "task.deleted",
    "task.cancelled",
    "task.archived",
)
TASK_FIELDS = (
    "id",
    "title",
    "notes",
    "status",
    "priority",
    "project_ref",
    "due_at",
    "estimate_minutes",
    "tags",
    "source_ref",
    "created_at",
    "updated_at",
    "completed_at",
    "deleted_at",
    "archived_at",
    "version",
)
UPDATE_FIELDS = (
    "title",
    "notes",
    "priority",
    "project_ref",
    "source_ref",
    "due_at",
    "estimate_minutes",
    "tags",
)


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
    additional: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": additional,
        "properties": properties,
    }


def _string(minimum: int = 0, maximum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "minLength": minimum}
    if maximum is not None:
        schema["maxLength"] = maximum
    return schema


def _nullable_string(minimum: int = 1, maximum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": ["string", "null"], "minLength": minimum}
    if maximum is not None:
        schema["maxLength"] = maximum
    return schema


def _nullable_integer(minimum: int, maximum: int) -> dict[str, Any]:
    return {"type": ["integer", "null"], "minimum": minimum, "maximum": maximum}


def _nullable_utc_timestamp() -> dict[str, Any]:
    return {"type": ["string", "null"], "minLength": 27, "maxLength": 27}


def _task_schema() -> dict[str, Any]:
    props = {
        "id": {"type": "string", "minLength": 36, "maxLength": 36},
        "title": _string(1, 500),
        "notes": _nullable_string(0, 20000),
        "status": {"type": "string", "enum": ["open", "completed", "cancelled", "deleted"]},
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "project_ref": _nullable_string(1, 200),
        "due_at": _nullable_utc_timestamp(),
        "estimate_minutes": _nullable_integer(1, 10080),
        "tags": {
            "type": "array",
            "maxItems": 32,
            "items": _string(1, 64),
        },
        "source_ref": _nullable_string(1, 200),
        "created_at": {"type": "string", "minLength": 27, "maxLength": 27},
        "updated_at": {"type": "string", "minLength": 27, "maxLength": 27},
        "completed_at": _nullable_utc_timestamp(),
        "deleted_at": _nullable_utc_timestamp(),
        "archived_at": _nullable_utc_timestamp(),
        "version": {"type": "integer", "minimum": 1},
    }
    return _object(props, required=TASK_FIELDS)


TASK_SCHEMA = _task_schema()
_TASK_ID_INPUT = {"type": "string", "minLength": 36, "maxLength": 36}
_EXPECTED_VERSION = {"type": "integer", "minimum": 1}
_PRIORITY = {"type": "string", "enum": ["low", "normal", "high", "urgent"]}
_TAGS = {"type": "array", "maxItems": 32, "items": _string(1, 64)}
_INPUT_FIELDS: dict[str, Any] = {
    # Trim-length constraints are applied by the Task domain after Core's schema
    # gate. Raw input may contain surrounding whitespace, so string bounds here
    # intentionally describe type/shape rather than normalized business limits.
    "title": {"type": "string"},
    "notes": _nullable_string(0, 20000),
    "priority": _PRIORITY,
    "project_ref": {"type": ["string", "null"]},
    "source_ref": {"type": ["string", "null"]},
    "due_at": _nullable_string(1, 35),
    "estimate_minutes": _nullable_integer(1, 10080),
    "tags": {"type": "array", "maxItems": 32, "items": {"type": "string"}},
}
_OUTPUT_FIELDS: dict[str, Any] = {
    "title": _string(1, 500),
    "notes": _nullable_string(0, 20000),
    "priority": _PRIORITY,
    "project_ref": _nullable_string(1, 200),
    "source_ref": _nullable_string(1, 200),
    # Domain validation applies the strict RFC3339 grammar before normalization.
    "due_at": _nullable_string(1, 35),
    "estimate_minutes": _nullable_integer(1, 10080),
    "tags": _TAGS,
}
_PAGE_CURSOR = _string(1, 4096)


CREATE_INPUT = _object(_INPUT_FIELDS, required=("title",))
GET_INPUT = _object(
    {
        "task_id": _TASK_ID_INPUT,
        "include_deleted": {"type": "boolean"},
    },
    required=("task_id",),
)
LIST_INPUT = _object(
    {
        "status": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {"type": "string", "enum": ["open", "completed", "cancelled", "deleted"]},
        },
        "include_deleted": {"type": "boolean"},
        "include_archived": {"type": "boolean"},
        "project_ref": {"type": ["string", "null"]},
        "priority": _PRIORITY,
        "due_before": _string(1, 35),
        "due_after": _string(1, 35),
        "tag": {"type": "string"},
        "updated_after": _string(1, 35),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": _PAGE_CURSOR,
    }
)
UPDATE_CHANGES = _object(_INPUT_FIELDS, additional=False)
UPDATE_INPUT = _object(
    {
        "task_id": _TASK_ID_INPUT,
        "expected_version": _EXPECTED_VERSION,
        "changes": UPDATE_CHANGES,
    },
    required=("task_id", "expected_version", "changes"),
)
TRANSITION_INPUT = _object(
    {"task_id": _TASK_ID_INPUT, "expected_version": _EXPECTED_VERSION},
    required=("task_id", "expected_version"),
)
STORE_TARGET_SCHEMA = _object({"store_id": _string(1, 200)}, required=("store_id",))
TASK_TARGET_SCHEMA = _object(
    {"store_id": _string(1, 200), "task_id": _TASK_ID_INPUT},
    required=("store_id", "task_id"),
)
TARGET_SCHEMAS = {
    "task.create": STORE_TARGET_SCHEMA,
    "task.get": TASK_TARGET_SCHEMA,
    "task.list": STORE_TARGET_SCHEMA,
    "task.update": TASK_TARGET_SCHEMA,
    "task.complete": TASK_TARGET_SCHEMA,
    "task.reopen": TASK_TARGET_SCHEMA,
    "task.delete": TASK_TARGET_SCHEMA,
    "task.cancel": TASK_TARGET_SCHEMA,
    "task.archive": TASK_TARGET_SCHEMA,
}

WRITE_OUTPUT = _object(
    {
        "task": TASK_SCHEMA,
        "changed": {"type": "boolean"},
        "operation_status": {"type": "string", "enum": ["committed", "preview"]},
        "result_state": {"type": "string", "enum": ["available", "planned", "expired"]},
        "code": {"type": "string", "minLength": 1, "maxLength": 100},
        "task_id": _TASK_ID_INPUT,
        "original_request_id": _string(1, 200),
        "planned": _object(_OUTPUT_FIELDS, additional=False),
    },
    required=("operation_status", "result_state"),
)
GET_OUTPUT = _object({"task": TASK_SCHEMA}, required=("task",))
LIST_OUTPUT = _object(
    {
        "tasks": {"type": "array", "items": TASK_SCHEMA},
        "next_cursor": {"type": ["string", "null"], "minLength": 1, "maxLength": 4096},
    },
    required=("tasks", "next_cursor"),
)


def validate_result_data(capability: str, data: Any) -> None:
    """Validate required Task result branches not expressible in Core's schema subset.

    Core validates the declared output JSON Schema. This supplements it with the
    mutually exclusive available/expired write result shape.
    """
    if capability not in CAPABILITIES:
        raise ValueError("未知 Task capability")
    if not isinstance(data, dict):
        raise ValueError("Task result data 必须是对象")
    if capability in {"task.create", "task.update", "task.complete", "task.reopen", "task.delete", "task.cancel", "task.archive"}:
        if data.get("operation_status") != "committed":
            raise ValueError("Task 写结果 operation_status 必须为 committed")
        state = data.get("result_state")
        if state == "available":
            if "task" not in data or type(data.get("changed")) is not bool:
                raise ValueError("可用 Task 写结果必须含 task 和布尔 changed")
            if any(key in data for key in ("code", "task_id", "original_request_id", "planned")):
                raise ValueError("可用 Task 写结果不能含过期或预览字段")
            return
        if state == "expired":
            if data.get("code") != "task.result_expired":
                raise ValueError("过期 Task 写结果 code 无效")
            if not isinstance(data.get("task_id"), str) or not data["task_id"]:
                raise ValueError("过期 Task 写结果必须含 task_id")
            if not isinstance(data.get("original_request_id"), str) or not data["original_request_id"]:
                raise ValueError("过期 Task 写结果必须含 original_request_id")
            if any(key in data for key in ("task", "changed", "planned")):
                raise ValueError("过期 Task 写结果不能含 task、changed 或 planned")
            return
        raise ValueError("Task 写结果 result_state 必须为 available 或 expired")
    if capability == "task.get":
        if "task" not in data:
            raise ValueError("Task get 结果必须含 task")
        return
    if "tasks" not in data or not isinstance(data["tasks"], list) or "next_cursor" not in data:
        raise ValueError("Task list 结果必须含 tasks 数组和 next_cursor")
    if data["next_cursor"] is not None and not isinstance(data["next_cursor"], str):
        raise ValueError("Task list next_cursor 必须是字符串或 null")


def _capability(name: str, effect: str, inputs: dict[str, Any], outputs: dict[str, Any],
                permissions: tuple[str, ...], intent: str) -> dict[str, Any]:
    return {
        "name": name,
        "effect": effect,
        "inputs": inputs,
        "outputs": outputs,
        "dependencies": [],
        "permissions": list(permissions),
        "intents": [intent],
        "implemented": True,
        "verified": True,
        "authorized": True,
        "enabled": True,
        "execution_mode": "standalone",
        "description": f"YushuOS Task {name.removeprefix('task.')} capability.",
        "resource_scopes": {"store_id": "task_store"},
    }


CAPABILITIES: dict[str, dict[str, Any]] = {
    "task.create": _capability("task.create", "internal_write", CREATE_INPUT, WRITE_OUTPUT, ("task.write",), "command"),
    "task.get": _capability("task.get", "read_only", GET_INPUT, GET_OUTPUT, ("task.read",), "query"),
    "task.list": _capability("task.list", "read_only", LIST_INPUT, LIST_OUTPUT, ("task.read",), "query"),
    "task.update": _capability("task.update", "internal_write", UPDATE_INPUT, WRITE_OUTPUT, ("task.read", "task.write"), "command"),
    "task.complete": _capability("task.complete", "internal_write", TRANSITION_INPUT, WRITE_OUTPUT, ("task.read", "task.write"), "command"),
    "task.reopen": _capability("task.reopen", "internal_write", TRANSITION_INPUT, WRITE_OUTPUT, ("task.read", "task.write"), "command"),
    "task.delete": _capability("task.delete", "internal_write", TRANSITION_INPUT, WRITE_OUTPUT, ("task.read", "task.delete"), "command"),
    "task.cancel": _capability("task.cancel", "internal_write", TRANSITION_INPUT, WRITE_OUTPUT, ("task.read", "task.write"), "command"),
    "task.archive": _capability("task.archive", "internal_write", TRANSITION_INPUT, WRITE_OUTPUT, ("task.read", "task.write"), "command"),
}


PLUGIN_MANIFEST: dict[str, Any] = {
    "id": PLUGIN_ID,
    "name": "YushuOS Task",
    "version": PLUGIN_VERSION,
    "contract_version": 3,
    "operation_support": OPERATION_SUPPORT,
    "type": "domain",
    "description": "A local task domain plugin for creating, querying, updating, and completing tasks.",
    "enabled": True,
    "dependencies": [],
    "optional_dependencies": [],
    "permissions": [],
    "data_path": "data",
    "supported_runtimes": ["python>=3.11"],
    "configuration": {"type": "object", "additionalProperties": False},
    "error_policy": "fail_closed",
    "audit_policy": "metadata_only",
    "runner": {
        "command": ["{python}", "{plugin_root}/run.py"],
        "timeout_seconds": 30,
        "protocol": "json-stdio-v2",
    },
    "capabilities": [deepcopy(CAPABILITIES[name]) for name in CAPABILITY_NAMES],
    "routes": [],
    "skill_names": [PLUGIN_ID],
    "emitted_events": list(TASK_EVENTS),
    "dependency_versions": {},
}


def render_manifest() -> str:
    """Render the inline v3 plugin manifest from this authoritative source."""
    return yaml.safe_dump(PLUGIN_MANIFEST, allow_unicode=True, sort_keys=False, width=100)


def capability_contract(name: str) -> dict[str, Any]:
    """Return a defensive copy of a capability's manifest contract."""
    return deepcopy(CAPABILITIES[name])


__all__ = [
    "CAPABILITIES",
    "CAPABILITY_NAMES",
    "CREATE_INPUT",
    "FINGERPRINT_SCHEME",
    "GET_INPUT",
    "GET_OUTPUT",
    "LIST_INPUT",
    "LIST_OUTPUT",
    "OPERATION_SUPPORT",
    "PLUGIN_ID",
    "PLUGIN_MANIFEST",
    "PLUGIN_VERSION",
    "TASK_EVENTS",
    "TASK_FIELDS",
    "TASK_SCHEMA",
    "TASK_TARGET_SCHEMA",
    "TARGET_SCHEMAS",
    "STORE_TARGET_SCHEMA",
    "TRANSITION_INPUT",
    "UPDATE_CHANGES",
    "UPDATE_FIELDS",
    "UPDATE_INPUT",
    "WRITE_OUTPUT",
    "capability_contract",
    "render_manifest",
    "validate_result_data",
]

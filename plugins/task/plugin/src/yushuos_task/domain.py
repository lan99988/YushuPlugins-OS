"""Task domain records, normalization, filters, and state transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping
import uuid

from .contracts import TASK_FIELDS, UPDATE_FIELDS


_STORE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_TASK_ID = re.compile(r"^tsk_[0-9a-f]{32}$")
_RFC3339 = re.compile(
    r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[Tt]"
    r"(?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.(?P<fraction>[0-9]{1,6}))?"
    r"(?P<zone>[Zz]|[+-][0-9]{2}:[0-9]{2})$"
)
_STATUSES = frozenset({"open", "completed", "deleted"})
_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
_UPDATE_FIELDS = frozenset(UPDATE_FIELDS)
_CREATE_FIELDS = _UPDATE_FIELDS
_TRANSITIONS = {
    "task.complete": ("open", "completed", "task.completed"),
    "task.reopen": ("completed", "open", "task.reopened"),
    "task.delete": (None, "deleted", "task.deleted"),
}


class TaskError(ValueError):
    """Base class for stable domain errors consumed by the plugin adapter."""

    code = "task.invalid"


class TaskValidationError(TaskError):
    code = "task.validation_error"


class TaskStoreIdentityError(TaskError):
    code = "task.store_scope_mismatch"


class TaskSchemaVersionError(TaskError):
    code = "task.schema_version_unsupported"


class TaskNotFound(TaskError):
    code = "task.not_found"


class TaskDeletedError(TaskError):
    code = "task.deleted_terminal"


class TaskVersionConflict(TaskError):
    code = "task.version_conflict"

    def __init__(self, expected: int, current: int):
        self.expected_version = expected
        self.current_version = current
        super().__init__("Task version does not match expected_version")


class TaskRequestConflict(TaskError):
    code = "task.request_conflict"


def utc_timestamp(value: datetime | str) -> str:
    """Normalize a timezone-aware datetime/string to UTC with six decimals."""
    if isinstance(value, str):
        match = _RFC3339.fullmatch(value)
        if match is None:
            raise TaskValidationError("时间必须是严格带时区的 RFC3339 时刻")
        zone = match.group("zone")
        if zone in {"Z", "z"}:
            zone = "+00:00"
        else:
            offset_hours = int(zone[1:3])
            offset_minutes = int(zone[4:6])
            if offset_hours > 23 or offset_minutes > 59:
                raise TaskValidationError("RFC3339 时区偏移小时必须不超过 23、分钟必须不超过 59")
        fraction = match.group("fraction") or ""
        source = f"{match.group('date')}T{match.group('time')}.{fraction.ljust(6, '0')}{zone}"
        try:
            value = datetime.fromisoformat(source)
        except (ValueError, OverflowError):
            raise TaskValidationError("时间不是有效的 RFC3339 时刻") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TaskValidationError("时间必须是严格带时区的 RFC3339 时刻")
    try:
        utc = value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise TaskValidationError("时间超出可表示范围") from None
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_optional_ref(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskValidationError(f"{field} 必须是字符串或 null")
    normalized = value.strip()
    if not 1 <= len(normalized) <= 200:
        raise TaskValidationError(f"{field} trim 后长度必须为 1 到 200")
    return normalized


def _normalize_title(value: Any) -> str:
    if not isinstance(value, str):
        raise TaskValidationError("title 必须是字符串")
    normalized = value.strip()
    if not 1 <= len(normalized) <= 500:
        raise TaskValidationError("title trim 后长度必须为 1 到 500")
    return normalized


def _normalize_notes(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 20000:
        raise TaskValidationError("notes 必须是 null 或不超过 20000 字符的字符串")
    return value


def _normalize_priority(value: Any) -> str:
    if not isinstance(value, str) or value not in _PRIORITIES:
        raise TaskValidationError("priority 必须是 low、normal、high 或 urgent")
    return value


def _normalize_estimate(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 10080:
        raise TaskValidationError("estimate_minutes 必须是 null 或 1 到 10080 的整数")
    return value


def _normalize_due_at(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskValidationError("due_at 必须是带时区 RFC3339 字符串或 null")
    return utc_timestamp(value)


def _normalize_tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 32:
        raise TaskValidationError("tags 必须是最多 32 项的字符串数组")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise TaskValidationError("tag 必须是字符串")
        tag = item.strip()
        if not 1 <= len(tag) <= 64:
            raise TaskValidationError("tag trim 后长度必须为 1 到 64")
        if tag not in seen:
            seen.add(tag)
            normalized.append(tag)
    return tuple(normalized)


def _normalize_fields(fields: Mapping[str, Any], *, creating: bool) -> dict[str, Any]:
    if not isinstance(fields, Mapping):
        raise TaskValidationError("Task 字段必须是对象")
    allowed = _CREATE_FIELDS if creating else _UPDATE_FIELDS
    extra = set(fields) - allowed
    if extra:
        raise TaskValidationError("Task 字段包含不允许的键：" + ", ".join(sorted(extra)))
    normalized: dict[str, Any] = {}
    for field, value in fields.items():
        if field == "title":
            normalized[field] = _normalize_title(value)
        elif field == "notes":
            normalized[field] = _normalize_notes(value)
        elif field == "priority":
            normalized[field] = _normalize_priority(value)
        elif field in {"project_ref", "source_ref"}:
            normalized[field] = _normalize_optional_ref(value, field)
        elif field == "due_at":
            normalized[field] = _normalize_due_at(value)
        elif field == "estimate_minutes":
            normalized[field] = _normalize_estimate(value)
        elif field == "tags":
            normalized[field] = _normalize_tags(value)
    return normalized


def _validate_store_id(store_id: Any) -> str:
    if not isinstance(store_id, str) or not _STORE_ID.fullmatch(store_id):
        raise TaskValidationError("store_id 必须是稳定的非空标识")
    return store_id


def _validate_task_id(task_id: Any) -> str:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise TaskValidationError("task_id 必须是 tsk_ 加 32 位小写 UUID hex")
    return task_id


def new_task_id() -> str:
    """Generate a normal-release Task ID using only the Python standard library."""
    return "tsk_" + uuid.uuid4().hex


@dataclass(frozen=True)
class Task:
    store_id: str
    id: str
    title: str
    notes: str | None
    status: str
    priority: str
    project_ref: str | None
    due_at: str | None
    estimate_minutes: int | None
    tags: tuple[str, ...]
    source_ref: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    deleted_at: str | None
    version: int

    def to_dict(self) -> dict[str, Any]:
        """Return the public Task model without the private store identifier."""
        return {
            "id": self.id,
            "title": self.title,
            "notes": self.notes,
            "status": self.status,
            "priority": self.priority,
            "project_ref": self.project_ref,
            "due_at": self.due_at,
            "estimate_minutes": self.estimate_minutes,
            "tags": list(self.tags),
            "source_ref": self.source_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "deleted_at": self.deleted_at,
            "version": self.version,
        }


@dataclass(frozen=True)
class Mutation:
    task: Task
    changed: bool
    event_intents: tuple[dict[str, Any], ...]


def task_from_fields(
    store_id: str,
    fields: Mapping[str, Any],
    *,
    now: datetime | str,
    task_id: str | None = None,
) -> Task:
    """Validate and normalize a create request into its initial Task record."""
    store_id = _validate_store_id(store_id)
    normalized = _normalize_fields(fields, creating=True)
    if "title" not in normalized:
        raise TaskValidationError("create 请求缺少 title")
    if task_id is None:
        task_id = new_task_id()
    else:
        _validate_task_id(task_id)
    created = utc_timestamp(now)
    return Task(
        store_id=store_id,
        id=task_id,
        title=normalized["title"],
        notes=normalized.get("notes"),
        status="open",
        priority=normalized.get("priority", "normal"),
        project_ref=normalized.get("project_ref"),
        due_at=normalized.get("due_at"),
        estimate_minutes=normalized.get("estimate_minutes"),
        tags=normalized.get("tags", ()),
        source_ref=normalized.get("source_ref"),
        created_at=created,
        updated_at=created,
        completed_at=None,
        deleted_at=None,
        version=1,
    )


def validate_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Validate update's explicit field whitelist while preserving omission."""
    return _normalize_fields(changes, creating=False)


def _event(event_type: str, task_id: str) -> dict[str, Any]:
    return {"type": event_type, "resource_refs": {"task_id": task_id}}


def apply_update(task: Task, changes: Mapping[str, Any], *, now: datetime | str) -> Mutation:
    if task.status == "deleted":
        raise TaskDeletedError("已删除的 Task 不能修改")
    normalized = validate_changes(changes)
    updated = {key: value for key, value in normalized.items() if getattr(task, key) != value}
    if not updated:
        return Mutation(task=task, changed=False, event_intents=())
    new_task = replace(
        task,
        **updated,
        updated_at=utc_timestamp(now),
        version=task.version + 1,
    )
    return Mutation(new_task, True, (_event("task.updated", task.id),))


def apply_transition(task: Task, operation_name: str, *, now: datetime | str) -> Mutation:
    if operation_name not in _TRANSITIONS:
        raise TaskValidationError("不支持的 Task 状态操作")
    if task.status == "deleted":
        if operation_name == "task.delete":
            return Mutation(task=task, changed=False, event_intents=())
        raise TaskDeletedError("已删除的 Task 为终态")
    previous_status, target_status, event_type = _TRANSITIONS[operation_name]
    if previous_status is not None and task.status != previous_status:
        return Mutation(task=task, changed=False, event_intents=())

    changed_at = utc_timestamp(now)
    changes: dict[str, Any] = {
        "status": target_status,
        "updated_at": changed_at,
        "version": task.version + 1,
    }
    if operation_name == "task.complete":
        changes["completed_at"] = changed_at
    elif operation_name == "task.reopen":
        changes["completed_at"] = None
    else:
        changes["deleted_at"] = changed_at
    new_task = replace(task, **changes)
    return Mutation(new_task, True, (_event(event_type, task.id),))


@dataclass(frozen=True)
class TaskFilters:
    statuses: tuple[str, ...] | None = None
    include_deleted: bool = False
    project_ref: str | None = None
    project_ref_specified: bool = False
    priority: str | None = None
    due_before: str | None = None
    due_after: str | None = None
    tag: str | None = None
    updated_after: str | None = None

    def __post_init__(self) -> None:
        if type(self.include_deleted) is not bool:
            raise TaskValidationError("include_deleted 必须是布尔值")
        if type(self.project_ref_specified) is not bool:
            raise TaskValidationError("project_ref_specified 必须是布尔值")
        if self.statuses is not None:
            if (not isinstance(self.statuses, tuple) or not 1 <= len(self.statuses) <= 3
                    or any(not isinstance(status, str) or status not in _STATUSES for status in self.statuses)
                    or len(self.statuses) != len(set(self.statuses))):
                raise TaskValidationError("TaskFilters.statuses 无效")
            if "deleted" in self.statuses and not self.include_deleted:
                raise TaskValidationError("查询 deleted Task 必须显式设置 include_deleted=true")
        if self.project_ref_specified:
            if _normalize_optional_ref(self.project_ref, "project_ref") != self.project_ref:
                raise TaskValidationError("TaskFilters.project_ref 必须已 trim")
        elif self.project_ref is not None:
            raise TaskValidationError("未指定 project_ref 时不能提供过滤值")
        if self.priority is not None and _normalize_priority(self.priority) != self.priority:
            raise TaskValidationError("TaskFilters.priority 无效")
        if self.tag is not None and (not isinstance(self.tag, str) or not 1 <= len(self.tag) <= 64 or self.tag.strip() != self.tag):
            raise TaskValidationError("TaskFilters.tag 必须已 trim 且长度为 1 到 64")
        for name in ("due_before", "due_after", "updated_after"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or utc_timestamp(value) != value):
                raise TaskValidationError(f"TaskFilters.{name} 必须是规范 UTC 时刻")

    @classmethod
    def from_fields(cls, fields: Mapping[str, Any]) -> "TaskFilters":
        allowed = {
            "status", "include_deleted", "project_ref", "priority", "due_before",
            "due_after", "tag", "updated_after",
        }
        if not isinstance(fields, Mapping) or set(fields) - allowed:
            raise TaskValidationError("list 过滤条件包含不支持字段")
        include_deleted = fields.get("include_deleted", False)
        if type(include_deleted) is not bool:
            raise TaskValidationError("include_deleted 必须是布尔值")

        raw_status = fields.get("status")
        statuses: tuple[str, ...] | None
        if "status" not in fields:
            statuses = None
        else:
            if not isinstance(raw_status, (list, tuple)) or not 1 <= len(raw_status) <= 3:
                raise TaskValidationError("status 必须是包含 1 到 3 个值的数组")
            if any(not isinstance(item, str) or item not in _STATUSES for item in raw_status):
                raise TaskValidationError("status 包含无效状态")
            if len(raw_status) != len(set(raw_status)):
                raise TaskValidationError("status 数组不能包含重复值")
            statuses = tuple(raw_status)
        if statuses and "deleted" in statuses and not include_deleted:
            raise TaskValidationError("查询 deleted Task 必须显式设置 include_deleted=true")

        project_ref_specified = "project_ref" in fields
        project_ref = None
        if project_ref_specified:
            project_ref = _normalize_optional_ref(fields["project_ref"], "project_ref")

        priority = fields.get("priority")
        if "priority" in fields:
            priority = _normalize_priority(priority)

        tag = fields.get("tag")
        if "tag" in fields:
            if not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 64:
                raise TaskValidationError("tag trim 后长度必须为 1 到 64")
            tag = tag.strip()

        times: dict[str, str | None] = {}
        for key in ("due_before", "due_after", "updated_after"):
            raw = fields.get(key)
            if key in fields:
                if not isinstance(raw, str):
                    raise TaskValidationError(f"{key} 必须是带时区 RFC3339 字符串")
                times[key] = utc_timestamp(raw)
            else:
                times[key] = None

        return cls(
            statuses=statuses,
            include_deleted=include_deleted,
            project_ref=project_ref,
            project_ref_specified=project_ref_specified,
            priority=priority,
            due_before=times["due_before"],
            due_after=times["due_after"],
            tag=tag,
            updated_after=times["updated_after"],
        )

    def resolved_statuses(self) -> tuple[str, ...]:
        if self.statuses is not None:
            return self.statuses
        if self.include_deleted:
            return ("open", "completed", "deleted")
        return ("open", "completed")

    def cursor_payload(self) -> dict[str, Any]:
        return {
            "statuses": list(self.resolved_statuses()),
            "include_deleted": self.include_deleted,
            "project_ref_specified": self.project_ref_specified,
            "project_ref": self.project_ref,
            "priority": self.priority,
            "due_before": self.due_before,
            "due_after": self.due_after,
            "tag": self.tag,
            "updated_after": self.updated_after,
        }


def validate_request_target(capability: str, fields: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[str, str | None]:
    """Validate target store binding and duplicated task identity fields."""
    if capability not in {
        "task.create", "task.get", "task.list", "task.update", "task.complete", "task.reopen", "task.delete",
    }:
        raise TaskValidationError("未知 Task capability")
    if not isinstance(fields, Mapping) or not isinstance(target, Mapping):
        raise TaskValidationError("fields 和 target 必须是对象")
    if set(target) - {"store_id", "task_id"}:
        raise TaskValidationError("target 包含不支持字段")
    if "store_id" not in target:
        raise TaskValidationError("target 缺少 store_id")
    store_id = _validate_store_id(target["store_id"])
    item_action = capability in {"task.get", "task.update", "task.complete", "task.reopen", "task.delete"}
    target_task_id = target.get("task_id")
    field_task_id = fields.get("task_id")
    if item_action:
        _validate_task_id(target_task_id)
        _validate_task_id(field_task_id)
        if target_task_id != field_task_id:
            raise TaskValidationError("fields.task_id 与 target.task_id 必须一致")
        return store_id, target_task_id
    if "task_id" in target or "task_id" in fields:
        raise TaskValidationError("create/list 请求不允许 task_id")
    return store_id, None


__all__ = [
    "Mutation",
    "Task",
    "TaskDeletedError",
    "TaskError",
    "TaskFilters",
    "TaskNotFound",
    "TaskRequestConflict",
    "TaskSchemaVersionError",
    "TaskStoreIdentityError",
    "TaskValidationError",
    "TaskVersionConflict",
    "apply_transition",
    "apply_update",
    "new_task_id",
    "task_from_fields",
    "utc_timestamp",
    "validate_changes",
    "validate_request_target",
]

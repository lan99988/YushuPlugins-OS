from datetime import datetime, timezone

import pytest

from yushuos_task.domain import (
    TaskDeletedError,
    TaskValidationError,
    apply_transition,
    apply_update,
    task_from_fields,
    validate_request_target,
)


NOW = "2026-01-02T03:04:05.123456Z"


def test_create_normalizes_defaults_and_serializes_only_public_fields():
    task = task_from_fields("store-a", {"title": "  write tests  "}, now=NOW)

    assert task.id.startswith("tsk_") and len(task.id) == 36
    assert task.title == "write tests"
    assert task.notes is None
    assert task.priority == "normal"
    assert task.status == "open"
    assert task.version == 1
    assert task.created_at == task.updated_at == NOW
    assert task.to_dict()["tags"] == []
    assert "store_id" not in task.to_dict()


def test_create_normalizes_rfc3339_and_deduplicates_tags_in_first_seen_order():
    task = task_from_fields(
        "store-a",
        {
            "title": "Task",
            "due_at": "2026-01-02T11:04:05+08:00",
            "tags": [" alpha ", "beta", "alpha", " beta "],
            "project_ref": " project-1 ",
        },
        now=NOW,
    )

    assert task.due_at == "2026-01-02T03:04:05.000000Z"
    assert task.tags == ("alpha", "beta")
    assert task.project_ref == "project-1"


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "  "},
        {"title": "x" * 501},
        {"title": "Task", "priority": "critical"},
        {"title": "Task", "estimate_minutes": True},
        {"title": "Task", "estimate_minutes": 0},
        {"title": "Task", "due_at": "2026-01-02T03:04:05"},
        {"title": "Task", "due_at": "2026-01-02T03:04:05.1234567Z"},
        {"title": "Task", "due_at": "2026-01-02T03:04:05+01:60"},
        {"title": "Task", "due_at": "2026-01-02T03:04:05+01:99"},
        {"title": "Task", "due_at": "2026-01-02T03:04:05+24:00"},
        {"title": "Task", "tags": ["   "]},
        {"title": "Task", "unexpected": "value"},
    ],
)
def test_create_rejects_invalid_or_unknown_fields(fields):
    with pytest.raises(TaskValidationError):
        task_from_fields("store-a", fields, now=NOW)


def test_update_uses_whitelist_and_explicit_null_clears_nullable_fields():
    task = task_from_fields(
        "store-a",
        {"title": "Task", "notes": "old", "due_at": "2026-01-03T00:00:00Z"},
        now=NOW,
    )
    outcome = apply_update(task, {"notes": None, "due_at": None}, now="2026-01-02T04:00:00Z")

    assert outcome.changed is True
    assert outcome.task.notes is None
    assert outcome.task.due_at is None
    assert outcome.task.version == 2
    assert outcome.event_intents == ({"type": "task.updated", "resource_refs": {"task_id": task.id}},)


def test_identical_update_is_a_strict_noop_with_no_event_intent():
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    outcome = apply_update(task, {"title": " Task "}, now="2026-01-02T04:00:00Z")

    assert outcome.changed is False
    assert outcome.task == task
    assert outcome.event_intents == ()


@pytest.mark.parametrize("changes", [{"status": "completed"}, {"id": "tsk_x"}, {"created_at": NOW}, {"x": 1}])
def test_update_rejects_fields_outside_first_release_whitelist(changes):
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    with pytest.raises(TaskValidationError):
        apply_update(task, changes, now=NOW)


def test_transition_rules_keep_deleted_terminal_and_make_noop_events_empty():
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    completed = apply_transition(task, "task.complete", now="2026-01-02T04:00:00Z")
    assert completed.task.status == "completed"
    assert completed.task.version == 2
    assert completed.event_intents[0]["type"] == "task.completed"

    repeated = apply_transition(completed.task, "task.complete", now="2026-01-02T05:00:00Z")
    assert repeated.changed is False
    assert repeated.task == completed.task
    assert repeated.event_intents == ()

    deleted = apply_transition(completed.task, "task.delete", now="2026-01-02T06:00:00Z")
    assert deleted.task.status == "deleted"
    assert deleted.task.completed_at == completed.task.completed_at
    assert deleted.event_intents[0]["type"] == "task.deleted"
    with pytest.raises(TaskDeletedError):
        apply_transition(deleted.task, "task.reopen", now="2026-01-02T07:00:00Z")


def test_reopen_clears_completed_time_and_uses_microsecond_utc():
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    completed = apply_transition(task, "task.complete", now=datetime(2026, 1, 2, 11, tzinfo=timezone.utc))
    reopened = apply_transition(completed.task, "task.reopen", now="2026-01-02T12:00:00+08:00")

    assert reopened.task.status == "open"
    assert reopened.task.completed_at is None
    assert reopened.task.updated_at == "2026-01-02T04:00:00.000000Z"


def test_request_target_binds_store_and_requires_matching_duplicate_task_id():
    task_id = "tsk_" + "a" * 32
    assert validate_request_target("task.create", {"title": "Task"}, {"store_id": "store-a"}) == ("store-a", None)
    assert validate_request_target(
        "task.update", {"task_id": task_id}, {"store_id": "store-a", "task_id": task_id}
    ) == ("store-a", task_id)
    for capability, fields, target in (
        ("task.create", {"title": "Task"}, {}),
        ("task.get", {"task_id": task_id}, {"store_id": "store-a", "task_id": "tsk_" + "b" * 32}),
        ("task.list", {}, {"store_id": "store-a", "task_id": task_id}),
        ("task.get", {"task_id": task_id}, {"store_id": "store-a", "task_id": task_id, "other": "x"}),
    ):
        with pytest.raises(TaskValidationError):
            validate_request_target(capability, fields, target)


@pytest.mark.parametrize(
    "filters",
    [
        {"status": None},
        {"status": []},
        {"status": ["open", "open"]},
        {"status": ["deleted"]},
        {"priority": None},
        {"tag": None},
        {"due_before": None},
        {"updated_after": "2026-01-02T00:00:00+01:99"},
    ],
)
def test_list_filter_parser_rejects_explicit_null_duplicate_and_bad_values(filters):
    from yushuos_task.domain import TaskFilters

    with pytest.raises(TaskValidationError):
        TaskFilters.from_fields(filters)

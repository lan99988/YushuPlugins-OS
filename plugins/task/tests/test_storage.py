from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import multiprocessing
from time import perf_counter
import sqlite3

import pytest

from yushuos_task.domain import (
    TaskFilters,
    TaskDeletedError,
    TaskNotFound,
    TaskRequestConflict,
    TaskSchemaVersionError,
    TaskStoreIdentityError,
    TaskValidationError,
    TaskVersionConflict,
    task_from_fields,
    utc_timestamp,
)
from yushuos_task.storage import CommitIdentity, TaskStore


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def identity(store_id="store-a", request_id="req-1", operation="task.create", *, fingerprint=DIGEST_A):
    return CommitIdentity(
        plugin_id="yushuos.task",
        store_id=store_id,
        request_id=request_id,
        request_fingerprint=fingerprint,
        fingerprint_scheme="jcs-operation-v1",
        operation_name=operation,
        core_project_ref="core-project",
        provider_version="0.1.0",
        provider_digest=DIGEST_B,
    )


def create(store: TaskStore, *, request_id="req-1", store_id="store-a", title="Task", now="2026-01-01T00:00:00Z", **fields):
    task = task_from_fields(store_id, {"title": title, **fields}, now=now)
    return store.commit_create(task, identity(store_id, request_id, "task.create"))


def _process_update_once(arguments):
    path, task_id, suffix, hour = arguments
    store = TaskStore(path)
    try:
        outcome = store.commit_mutation(
            task_id,
            1,
            identity("store-a", f"process-update-{suffix}", "task.update", fingerprint=(str(suffix + 1) * 64)),
            changes={"title": f"process-{suffix}"},
            now=f"2026-01-01T{hour:02d}:00:00Z",
        )
    except TaskVersionConflict:
        return ("conflict", None)
    return ("committed", outcome.data["task"]["title"])


def _process_create_same_request(arguments):
    path, title = arguments
    store = TaskStore(path)
    task = task_from_fields("store-a", {"title": title}, now="2026-01-01T00:00:00Z")
    outcome = store.commit_create(task, identity("store-a", "same-process-request", "task.create"))
    return outcome.data["task"]["id"], outcome.data["task"]["title"]


def test_reads_do_not_create_database_and_first_create_commits_fact_proof_and_event_together(tmp_path):
    path = tmp_path / "private" / "task.sqlite"
    store = TaskStore(path)

    assert store.get_task("store-a", "tsk_" + "1" * 32) is None
    assert store.list_tasks("store-a", TaskFilters.from_fields({})).tasks == ()
    assert not path.exists()

    outcome = create(store, tags=["alpha", "beta"])
    assert outcome.data["operation_status"] == "committed"
    assert outcome.data["result_state"] == "available"
    assert outcome.data["changed"] is True
    assert outcome.event_intents == ({"type": "task.created", "resource_refs": {"task_id": outcome.data["task"]["id"]}},)
    assert outcome.proof.event_recovery_state == "pending"
    assert outcome.proof.result_body is not None
    assert store.get_task("store-a", outcome.data["task"]["id"]).tags == ("alpha", "beta")

    with sqlite3.connect(path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"schema_meta", "tasks", "task_tags", "task_request_commits"} <= names
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM task_request_commits").fetchone()[0] == 1


def test_database_is_bound_to_one_store_and_unknown_schema_is_read_write_fail_closed(tmp_path):
    path = tmp_path / "task.sqlite"
    store = TaskStore(path)
    outcome = create(store)
    task_id = outcome.data["task"]["id"]
    with pytest.raises(TaskStoreIdentityError):
        store.get_task("store-b", task_id)
    with pytest.raises(TaskStoreIdentityError):
        store.commit_mutation(task_id, 1, identity("store-b", "req-b", "task.update"), changes={"title": "x"})

    unknown_path = tmp_path / "future.sqlite"
    with sqlite3.connect(unknown_path) as db:
        db.execute("PRAGMA user_version=3")
    with pytest.raises(TaskSchemaVersionError):
        TaskStore(unknown_path).list_tasks("store-a", TaskFilters.from_fields({}))
    with pytest.raises(TaskSchemaVersionError):
        TaskStore(unknown_path).commit_create(
            task_from_fields("store-a", {"title": "No overwrite"}, now="2026-01-01T00:00:00Z"),
            identity(),
        )


def test_duplicate_proof_returns_first_snapshot_and_rejects_reused_request_identity(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    first = create(store, request_id="request-same", title="First")
    second_task = task_from_fields("store-a", {"title": "Second"}, now="2026-01-02T00:00:00Z")
    replay = store.commit_create(second_task, identity("store-a", "request-same", "task.create"))

    assert replay.data == first.data
    assert replay.data["task"]["title"] == "First"
    assert len(store.list_tasks("store-a", TaskFilters.from_fields({})).tasks) == 1

    altered = identity("store-a", "request-same", "task.create", fingerprint="c" * 64)
    with pytest.raises(TaskRequestConflict):
        store.commit_create(second_task, altered)


def test_expected_version_precedes_noop_and_noop_has_empty_events_and_unchanged_version(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    created = create(store)
    task_id = created.data["task"]["id"]
    completed = store.commit_mutation(
        task_id, 1, identity("store-a", "req-complete", "task.complete"), now="2026-01-01T01:00:00Z"
    )
    assert completed.data["task"]["status"] == "completed"
    assert completed.data["task"]["version"] == 2
    assert completed.event_intents[0]["type"] == "task.completed"

    repeated = store.commit_mutation(
        task_id, 2, identity("store-a", "req-complete-again", "task.complete"), now="2026-01-01T02:00:00Z"
    )
    assert repeated.data["changed"] is False
    assert repeated.data["task"]["version"] == 2
    assert repeated.event_intents == ()
    assert repeated.proof.event_recovery_state == "none"

    with pytest.raises(TaskVersionConflict) as error:
        store.commit_mutation(
            task_id, 1, identity("store-a", "req-stale", "task.complete"), now="2026-01-01T03:00:00Z"
        )
    assert error.value.expected_version == 1
    assert error.value.current_version == 2


def test_completed_task_can_be_edited_reopened_then_soft_deleted_without_losing_completion_time(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    task_id = create(store).data["task"]["id"]
    completed = store.commit_mutation(
        task_id, 1, identity("store-a", "complete", "task.complete"), now="2026-01-01T01:00:00Z"
    )
    completed_at = completed.data["task"]["completed_at"]
    edited = store.commit_mutation(
        task_id, 2, identity("store-a", "edit", "task.update"),
        changes={"notes": "  keep spaces  "}, now="2026-01-01T02:00:00Z",
    )
    assert edited.data["task"]["notes"] == "  keep spaces  "
    assert edited.data["task"]["completed_at"] == completed_at
    reopened = store.commit_mutation(
        task_id, 3, identity("store-a", "reopen", "task.reopen"), now="2026-01-01T03:00:00Z"
    )
    assert reopened.data["task"]["status"] == "open"
    assert reopened.data["task"]["completed_at"] is None
    completed_again = store.commit_mutation(
        task_id, 4, identity("store-a", "complete-again", "task.complete"), now="2026-01-01T04:00:00Z"
    )
    deleted = store.commit_mutation(
        task_id, 5, identity("store-a", "delete", "task.delete"), now="2026-01-01T05:00:00Z"
    )
    assert deleted.data["task"]["status"] == "deleted"
    assert deleted.data["task"]["completed_at"] == completed_again.data["task"]["completed_at"]
    assert deleted.data["task"]["deleted_at"] == "2026-01-01T05:00:00.000000Z"
    assert store.get_task("store-a", task_id) is None
    assert store.get_task("store-a", task_id, include_deleted=True).status == "deleted"


def test_deleted_is_terminal_except_matching_version_delete_noop(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    task_id = create(store).data["task"]["id"]
    store.commit_mutation(
        task_id, 1, identity("store-a", "delete", "task.delete"), now="2026-01-01T01:00:00Z"
    )
    repeated = store.commit_mutation(
        task_id, 2, identity("store-a", "delete-again", "task.delete"), now="2026-01-01T02:00:00Z"
    )
    assert repeated.data["changed"] is False
    assert repeated.event_intents == ()
    with pytest.raises(TaskVersionConflict):
        store.commit_mutation(
            task_id, 1, identity("store-a", "stale-delete", "task.delete"), now="2026-01-01T03:00:00Z"
        )
    with pytest.raises(TaskDeletedError):
        store.commit_mutation(
            task_id, 2, identity("store-a", "reopen-deleted", "task.reopen"), now="2026-01-01T03:00:00Z"
        )


def test_event_recovery_marker_is_identity_checked_and_idempotent(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    outcome = create(store)
    proof = store.lookup_commit("yushuos.task", "store-a", "req-1")
    assert proof == outcome.proof
    assert proof.event_recovery_state == "pending"
    assert store.mark_events_recorded(identity()) == "ledger_recorded"
    assert store.mark_events_recorded(identity()) == "ledger_recorded"
    recorded = store.lookup_commit("yushuos.task", "store-a", "req-1")
    assert recorded.event_recovery_state == "ledger_recorded"

    with pytest.raises(TaskRequestConflict):
        store.mark_events_recorded(identity(fingerprint="d" * 64))


def test_result_snapshot_expires_at_180_days_and_only_a_later_real_write_prunes_it(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    created = create(store, now="2026-01-01T00:00:00Z")
    first_proof = created.proof
    expiry = first_proof.result_body_expires_at
    assert expiry == "2026-06-30T00:00:00.000000Z"

    expired_data = first_proof.result_data(now=expiry)
    assert expired_data == {
        "operation_status": "committed",
        "result_state": "expired",
        "code": "task.result_expired",
        "task_id": first_proof.task_id,
        "original_request_id": "req-1",
    }
    assert "task" not in expired_data and "changed" not in expired_data

    # Lookup, get and list are read-only even after the body crossed its deadline.
    read_proof = store.lookup_commit("yushuos.task", "store-a", "req-1")
    assert read_proof.result_body is not None
    store.get_task("store-a", first_proof.task_id)
    store.list_tasks("store-a", TaskFilters.from_fields({}))
    assert store.lookup_commit("yushuos.task", "store-a", "req-1").result_body is not None

    task_id = first_proof.task_id
    store.commit_mutation(
        task_id, 1, identity("store-a", "next-write", "task.update"),
        changes={"title": "Next"}, now="2026-06-30T00:00:01Z",
    )
    pruned = store.lookup_commit("yushuos.task", "store-a", "req-1")
    assert pruned.result_body is None
    assert pruned.result_data(now="2026-06-30T00:00:01Z")["result_state"] == "expired"
    assert store.get_task("store-a", task_id).title == "Next"


def test_noop_real_write_prunes_expired_bodies_but_does_not_create_an_event(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    task_id = create(store, title="same").data["task"]["id"]
    store.commit_mutation(
        task_id, 1, identity("store-a", "noop", "task.update"),
        changes={"title": "same"}, now="2026-06-30T00:00:01Z",
    )
    proof = store.lookup_commit("yushuos.task", "store-a", "noop")
    assert proof.changed is False
    assert proof.event_intents == ()
    assert proof.event_recovery_state == "none"


def test_list_filters_deleted_null_project_tags_and_keyset_cursor_binding(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    a = create(store, request_id="a", title="A", now="2026-01-01T00:00:00Z", tags=["blue"]).data["task"]
    b = create(store, request_id="b", title="B", now="2026-01-02T00:00:00Z", project_ref="project-b", tags=["blue"]).data["task"]
    c = create(store, request_id="c", title="C", now="2026-01-03T00:00:00Z", tags=["red"]).data["task"]
    store.commit_mutation(
        c["id"], 1, identity("store-a", "delete-c", "task.delete"), now="2026-01-03T01:00:00Z"
    )

    default_page = store.list_tasks("store-a", TaskFilters.from_fields({}), limit=1)
    assert [task.title for task in default_page.tasks] == ["B"]
    assert default_page.next_cursor
    next_page = store.list_tasks("store-a", TaskFilters.from_fields({}), limit=1, cursor=default_page.next_cursor)
    assert [task.title for task in next_page.tasks] == ["A"]
    assert next_page.next_cursor is None

    unassigned = TaskFilters.from_fields({"project_ref": None, "tag": "blue"})
    assert [task.id for task in store.list_tasks("store-a", unassigned).tasks] == [a["id"]]
    with pytest.raises(TaskValidationError):
        store.list_tasks("store-a", TaskFilters.from_fields({"project_ref": None, "priority": "high"}), cursor=default_page.next_cursor)
    with pytest.raises(TaskValidationError):
        store.list_tasks("store-b", TaskFilters.from_fields({}), cursor=default_page.next_cursor)
    with pytest.raises(TaskValidationError):
        TaskFilters.from_fields({"status": ["deleted"]})
    assert [task.id for task in store.list_tasks("store-a", TaskFilters.from_fields({"include_deleted": True})).tasks] == [c["id"], b["id"], a["id"]]


def test_inclusive_due_range_excludes_null_due_and_updated_after_is_strict(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    before = create(store, request_id="before", title="before", now="2026-01-01T00:00:00Z", due_at="2026-01-03T00:00:00Z").data["task"]
    exact = create(store, request_id="exact", title="exact", now="2026-01-02T00:00:00Z", due_at="2026-01-04T08:00:00+08:00").data["task"]
    create(store, request_id="none", title="none", now="2026-01-03T00:00:00Z")

    due = TaskFilters.from_fields({"due_before": "2026-01-04T00:00:00Z", "due_after": "2026-01-03T00:00:00Z"})
    assert {task.id for task in store.list_tasks("store-a", due).tasks} == {before["id"], exact["id"]}

    updated = TaskFilters.from_fields({"updated_after": "2026-01-03T00:00:00Z"})
    assert store.list_tasks("store-a", updated).tasks == ()


def test_two_writers_with_same_expected_version_allow_only_one_commit(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    task_id = create(store).data["task"]["id"]

    def update(index: int):
        try:
            return store.commit_mutation(
                task_id,
                1,
                identity("store-a", f"parallel-{index}", "task.update", fingerprint=(str(index + 1) * 64)),
                changes={"title": f"writer-{index}"},
                now=f"2026-01-01T0{index + 1}:00:00Z",
            )
        except TaskVersionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, (0, 1), timeout=10))

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, TaskVersionConflict) for item in results) == 1
    assert store.get_task("store-a", task_id).version == 2


def test_commit_identity_rejects_non_jcs_or_invalid_provider_pins(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    task = task_from_fields("store-a", {"title": "Task"}, now="2026-01-01T00:00:00Z")
    bad_scheme = CommitIdentity(
        "yushuos.task", "store-a", "req-1", DIGEST_A, "legacy-v1", "task.create",
        "core-project", "0.1.0", DIGEST_B,
    )
    with pytest.raises(TaskValidationError):
        store.commit_create(task, bad_scheme)


def test_changed_task_proof_snapshot_is_not_rebuilt_from_current_row_on_replay(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    first = create(store, title="Original")
    task_id = first.data["task"]["id"]
    store.commit_mutation(
        task_id, 1, identity("store-a", "later", "task.update"),
        changes={"title": "Current"}, now="2026-01-02T00:00:00Z",
    )

    proof = store.lookup_commit("yushuos.task", "store-a", "req-1")
    assert proof.result_data(now="2026-01-02T00:00:00Z")["task"]["title"] == "Original"
    assert store.get_task("store-a", task_id).title == "Current"


def test_missing_task_mutation_fails_without_committing_an_operation_proof(tmp_path):
    path = tmp_path / "task.sqlite"
    store = TaskStore(path)
    with pytest.raises(TaskNotFound):
        store.commit_mutation(
            "tsk_" + "9" * 32,
            1,
            identity("store-a", "missing", "task.update"),
            changes={"title": "x"},
            now="2026-01-01T00:00:00Z",
        )
    assert store.lookup_commit("yushuos.task", "store-a", "missing") is None


def test_real_processes_competing_on_expected_version_commit_only_one(tmp_path):
    store = TaskStore(tmp_path / "parallel.sqlite")
    task_id = create(store).data["task"]["id"]
    arguments = [
        (str(store.path), task_id, 0, 1),
        (str(store.path), task_id, 1, 2),
    ]

    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as executor:
        results = list(executor.map(_process_update_once, arguments))

    assert sorted(result[0] for result in results) == ["committed", "conflict"]
    assert store.get_task("store-a", task_id).version == 2
    proofs = [store.lookup_commit("yushuos.task", "store-a", f"process-update-{index}") for index in range(2)]
    assert sum(proof is not None for proof in proofs) == 1


def test_real_processes_reusing_same_request_commit_one_task_and_one_proof(tmp_path):
    store = TaskStore(tmp_path / "same-request.sqlite")
    arguments = [(str(store.path), "First process"), (str(store.path), "Second process")]

    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as executor:
        results = list(executor.map(_process_create_same_request, arguments))

    assert results[0] == results[1]
    assert len(store.list_tasks("store-a", TaskFilters.from_fields({})).tasks) == 1
    assert store.lookup_commit("yushuos.task", "store-a", "same-process-request") is not None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM task_request_commits").fetchone()[0] == 1


def test_first_schema_transaction_rolls_back_all_ddl_on_failure_and_can_retry(tmp_path):
    class FailingInitializationStore(TaskStore):
        @staticmethod
        def _create_schema_v2(db, store_id):
            TaskStore._create_schema_v2(db, store_id)
            raise RuntimeError("injected migration failure")

    path = tmp_path / "rollback.sqlite"
    broken = FailingInitializationStore(path)
    task = task_from_fields("store-a", {"title": "Task"}, now="2026-01-01T00:00:00Z")
    with pytest.raises(RuntimeError, match="injected migration failure"):
        broken.commit_create(task, identity())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall() == []

    created = TaskStore(path).commit_create(task, identity())
    assert created.data["task"]["title"] == "Task"


def test_ten_thousand_task_indexed_query_smoke_reports_elapsed_time_without_timing_gate(tmp_path):
    store = TaskStore(tmp_path / "ten-thousand.sqlite")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    started = perf_counter()
    with store._write_db("store-a") as db:
        for index in range(10_000):
            created = utc_timestamp(base + timedelta(seconds=index))
            task_id = f"tsk_{index:032x}"
            task = task_from_fields(
                "store-a",
                {
                    "title": f"Task {index}",
                    "project_ref": "project-a" if index % 2 == 0 else "project-b",
                    "priority": "high" if index % 3 == 0 else "normal",
                    "due_at": utc_timestamp(base + timedelta(days=1, seconds=index)) if index % 7 else None,
                    "tags": ["batch", "odd" if index % 2 else "even"],
                },
                now=created,
                task_id=task_id,
            )
            store._insert_task(db, task)
    seed_seconds = perf_counter() - started

    filters = TaskFilters.from_fields({"project_ref": "project-a", "priority": "high", "tag": "even"})
    timings = []
    for _ in range(15):
        query_start = perf_counter()
        page = store.list_tasks("store-a", filters, limit=50)
        timings.append(perf_counter() - query_start)
    assert len(page.tasks) == 50
    assert all(task.project_ref == "project-a" and task.priority == "high" and "even" in task.tags for task in page.tasks)
    median_ms = sorted(timings)[len(timings) // 2] * 1000
    print(f"10k seed={seed_seconds:.3f}s; filtered first page median={median_ms:.3f}ms")

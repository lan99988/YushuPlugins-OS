"""Task 0.2 lifecycle and schema migration regression tests."""
import json
from pathlib import Path
import sqlite3

import pytest

from yushuos_task.contracts import CAPABILITIES, PLUGIN_VERSION, TASK_EVENTS
from yushuos_task.domain import TaskDeletedError, TaskFilters, TaskValidationError, TaskVersionConflict, apply_transition, task_from_fields
from yushuos_task.storage import TaskDatabaseError, TaskStore
from test_storage import create, identity

NOW = "2026-10-06T01:00:00Z"


def test_cancel_is_open_only_and_cancelled_repeat_is_exact_noop():
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    cancelled = apply_transition(task, "task.cancel", now=NOW)
    assert cancelled.task.status == "cancelled"
    assert cancelled.task.version == 2
    assert cancelled.event_intents == ({"type": "task.cancelled", "resource_refs": {"task_id": task.id}},)
    repeat = apply_transition(cancelled.task, "task.cancel", now="2026-10-06T02:00:00Z")
    assert repeat.task == cancelled.task and not repeat.changed and repeat.event_intents == ()
    completed = apply_transition(task, "task.complete", now=NOW).task
    with pytest.raises(TaskValidationError):
        apply_transition(completed, "task.cancel", now=NOW)


@pytest.mark.parametrize("status", ["open", "completed", "cancelled"])
def test_archive_preserves_status_and_reopen_clears_archive(status):
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    if status != "open":
        task = apply_transition(task, "task.complete" if status == "completed" else "task.cancel", now=NOW).task
    archived = apply_transition(task, "task.archive", now=NOW)
    assert archived.task.status == status and archived.task.archived_at is not None
    assert archived.task.completed_at == task.completed_at
    assert archived.event_intents[0]["type"] == "task.archived"
    repeat = apply_transition(archived.task, "task.archive", now="2026-10-06T02:00:00Z")
    assert not repeat.changed and repeat.task == archived.task and repeat.event_intents == ()
    reopened = apply_transition(archived.task, "task.reopen", now=NOW)
    assert reopened.task.status == "open" and reopened.task.archived_at is None
    assert reopened.task.completed_at is None and reopened.task.version == archived.task.version + 1
    assert reopened.event_intents[0]["type"] == "task.reopened"


def test_cancel_archived_open_preserves_archive_and_deleted_stays_terminal():
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    archived = apply_transition(task, "task.archive", now=NOW).task
    cancelled = apply_transition(archived, "task.cancel", now=NOW).task
    assert cancelled.status == "cancelled" and cancelled.archived_at == archived.archived_at
    deleted = apply_transition(archived, "task.delete", now=NOW).task
    for operation in ("task.cancel", "task.archive", "task.reopen"):
        with pytest.raises(TaskDeletedError):
            apply_transition(deleted, operation, now=NOW)


def test_cancelled_without_archive_reopens():
    task = task_from_fields("store-a", {"title": "Task"}, now=NOW)
    task = apply_transition(task, "task.cancel", now=NOW).task
    assert apply_transition(task, "task.reopen", now=NOW).task.status == "open"


def test_new_contracts_keep_scopes_permissions_versions_and_refs_only_events():
    assert PLUGIN_VERSION == "0.2.0"
    for operation in ("task.cancel", "task.archive"):
        contract = CAPABILITIES[operation]
        assert contract["permissions"] == ["task.read", "task.write"]
        assert contract["resource_scopes"] == {"store_id": "task_store"}
        assert contract["inputs"]["required"] == ["task_id", "expected_version"]
    assert {"task.cancelled", "task.archived"} <= set(TASK_EVENTS)


def test_list_hides_archive_and_delete_but_includes_cancelled_and_binds_cursor(tmp_path):
    store = TaskStore(tmp_path / "task.sqlite")
    first = create(store, request_id="create-first", now=NOW)
    first_id = first.data["task"]["id"]
    cancelled = store.commit_mutation(first_id, 1, identity(request_id="cancel", operation="task.cancel"), now=NOW)
    assert cancelled.data["task"]["status"] == "cancelled"
    archived = store.commit_mutation(first_id, 2, identity(request_id="archive", operation="task.archive"), now=NOW)
    assert store.list_tasks("store-a", TaskFilters.from_fields({})).tasks == ()
    assert store.get_task("store-a", first_id).archived_at is not None
    included = store.list_tasks("store-a", TaskFilters.from_fields({"include_archived": True})).tasks
    assert len(included) == 1 and included[0].status == "cancelled"
    with pytest.raises(TaskVersionConflict):
        store.commit_mutation(first_id, 2, identity(request_id="stale", operation="task.archive"), now=NOW)
    repeated = store.commit_mutation(first_id, 3, identity(request_id="archive-repeat", operation="task.archive"), now=NOW)
    assert not repeated.data["changed"] and repeated.event_intents == ()
    store.commit_mutation(first_id, 3, identity(request_id="reopen", operation="task.reopen"), now=NOW)
    replay = store.commit_mutation(first_id, 2, identity(request_id="archive", operation="task.archive"), now=NOW)
    assert replay.data == archived.data
    assert store.lookup_commit("yushuos.task", "store-a", "cancel").result_data(now=NOW) == cancelled.data
    create(store, request_id="create-second", now=NOW)
    page = store.list_tasks("store-a", TaskFilters(), limit=1)
    with pytest.raises(TaskValidationError):
        store.list_tasks("store-a", TaskFilters(include_archived=True), limit=1, cursor=page.next_cursor)
    store.commit_mutation(first_id, 4, identity(request_id="delete", operation="task.delete"), now=NOW)
    assert len(store.list_tasks("store-a", TaskFilters()).tasks) == 1
    assert len(store.list_tasks("store-a", TaskFilters(include_deleted=True, include_archived=True)).tasks) == 2


@pytest.mark.parametrize("value", [None, 0, "true"])
def test_include_archived_requires_boolean(value):
    with pytest.raises(TaskValidationError):
        TaskFilters.from_fields({"include_archived": value})


def test_v1_migration_backs_up_and_preserves_original_proofs_and_tags(tmp_path):
    # SQL generated from the immutable imported 0.1.0 baseline bf52db8.
    path = tmp_path / "task.sqlite"
    task_id = "tsk_" + "1" * 32
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/schema-v1.sql").read_text(encoding="utf-8"))
        old_proofs = db.execute("SELECT * FROM task_request_commits").fetchall()
        original = json.loads(db.execute("SELECT result_body FROM task_request_commits").fetchone()[0])
        old_meta = db.execute("SELECT created_at FROM schema_meta").fetchone()
    store = TaskStore(path)
    assert store.get_task("store-a", task_id).archived_at is None
    assert not list(tmp_path.glob("*.schema-v1-*.bak"))
    store.commit_mutation(task_id, 1, identity(request_id="migrated-archive", operation="task.archive"), now=NOW)
    backups = list(tmp_path.glob("*.schema-v1-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT * FROM task_request_commits").fetchall() == old_proofs
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT * FROM task_request_commits WHERE request_id='req-1'").fetchall() == old_proofs
        assert db.execute("SELECT created_at FROM schema_meta").fetchone() == old_meta
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store.get_task("store-a", task_id).tags == ("a", "b")
    assert store.lookup_commit("yushuos.task", "store-a", "req-1").result_data(now=NOW) == original


def test_backup_creation_failure_refuses_migration_without_changing_v1(tmp_path, monkeypatch):
    path = tmp_path / "task.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/schema-v1.sql").read_text(encoding="utf-8"))
    original = path.read_bytes()
    real_open = Path.open
    def blocked_backup(self, *args, **kwargs):
        if self.name.endswith(".bak.incomplete"):
            raise PermissionError("injected backup failure")
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", blocked_backup)
    with pytest.raises(TaskDatabaseError):
        TaskStore(path).commit_mutation("tsk_" + "1" * 32, 1, identity(request_id="archive", operation="task.archive"), now=NOW)
    assert path.read_bytes() == original


def test_migration_transaction_rolls_back_when_v2_creation_fails(tmp_path, monkeypatch):
    path = tmp_path / "task.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/schema-v1.sql").read_text(encoding="utf-8"))
    original = path.read_bytes()
    def fail_schema(*args):
        raise sqlite3.OperationalError("injected schema failure")
    monkeypatch.setattr(TaskStore, "_create_schema_v2", fail_schema)
    with pytest.raises(TaskDatabaseError):
        TaskStore(path).commit_mutation("tsk_" + "1" * 32, 1, identity(request_id="archive", operation="task.archive"), now=NOW)
    assert path.read_bytes() == original
    assert len(list(tmp_path.glob("*.schema-v1-*.bak"))) == 1


def test_incomplete_backup_is_removed_when_sqlite_backup_fails(tmp_path, monkeypatch):
    path = tmp_path / "task.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/schema-v1.sql").read_text(encoding="utf-8"))
    original = path.read_bytes()
    real_connect = sqlite3.connect
    class BrokenBackupSource:
        def __init__(self, connection):
            self.connection = connection
        def backup(self, target):
            raise sqlite3.OperationalError("injected incomplete backup")
        def close(self):
            self.connection.close()
    def connect(source, *args, **kwargs):
        connection = real_connect(source, *args, **kwargs)
        if isinstance(source, str) and source.endswith("?mode=ro"):
            return BrokenBackupSource(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(TaskDatabaseError):
        TaskStore(path).commit_mutation("tsk_" + "1" * 32, 1, identity(request_id="archive", operation="task.archive"), now=NOW)
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.schema-v1-*.bak")) == []

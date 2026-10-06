"""End-to-end tests for the Task/Core local-commit protocol.

These tests deliberately launch the installed plugin through the real Core
runner. Crash hooks live in a test-only bootstrap wrapper and are activated by
a marker beside the temporary Core configuration, outside the locked package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import textwrap

import pytest
import yaml


TASK_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = Path(os.environ.get("YUSHUOS_CORE_SOURCE", TASK_ROOT.parent / "yushuos-core-03")).resolve()
sys.path.insert(0, str(CORE_ROOT))
sys.path.insert(0, str(TASK_ROOT / "src"))

from yushuos.runtime import CoreRuntime  # noqa: E402
from yushuos_sdk import StateStore  # noqa: E402


STORE_ID = "task-store-test"
PLUGIN_ID = "yushuos.task"
TASK_DB_RELATIVE = Path("plugin-data") / PLUGIN_ID / "data" / "task.sqlite3"


def _python_env() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    roots = [str(CORE_ROOT), str(TASK_ROOT / "src")]
    if current:
        roots.append(current)
    env["PYTHONPATH"] = os.pathsep.join(roots)
    env["PYTHONUTF8"] = "1"
    return env


def _core_cli(config_root: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "yushuos", "--config-root", str(config_root), *args],
        cwd=CORE_ROOT,
        env=_python_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def _write_core_config(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": 1,
        "plugins": {"versions": {PLUGIN_ID: "0.2.0"}},
        "state": {"ledger_path": "operations.sqlite3"},
        "bindings": {"resources": {"task_store": STORE_ID}},
        "permissions": {"grants": ["task.read", "task.write", "task.delete"]},
    }
    (root / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _read_core_config(root: Path) -> dict:
    return yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))


def _save_core_config(root: Path, config: dict) -> None:
    (root / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _fault_bootstrap(stage: str) -> str:
    """Stable, test-only runner that exits at a real process boundary."""
    return textwrap.dedent(
        f"""\
        import io
        import json
        import os
        import sys
        from pathlib import Path

        plugin_source = Path(__file__).resolve().parent / "src"
        sys.path.insert(0, str(plugin_source))
        raw = sys.stdin.read()
        envelope = json.loads(raw)
        marker = Path(envelope["config_root"]) / ".task-crash-stage"
        active = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""

        if active == {stage!r}:
            from yushuos_sdk.state import StateStore
            from yushuos_task.storage import TaskStore

            action = envelope.get("action", "invoke")
            if active == "task_commit_then_reconcile_exception" and action == "invoke":
                def crash_before_ledger(self, *args, **kwargs):
                    os._exit(71)
                StateStore.record_with_events = crash_before_ledger
            elif active == "task_commit_then_reconcile_exception" and action == "recover":
                def fail_reconciliation(self, *args, **kwargs):
                    raise ValueError("injected Core reconcile failure")
                StateStore.reconcile_confirmed = fail_reconciliation
            elif active == "before_task_commit":
                def crash_before_commit(self, *args, **kwargs):
                    os._exit(70)
                TaskStore.commit_create = crash_before_commit
            elif active == "mutation_commit_before_ledger":
                original = TaskStore.commit_mutation
                def commit_then_crash(self, *args, **kwargs):
                    original(self, *args, **kwargs)
                    os._exit(73)
                TaskStore.commit_mutation = commit_then_crash
            elif active == "task_commit_before_ledger":
                def crash_before_ledger(self, *args, **kwargs):
                    os._exit(71)
                StateStore.record_with_events = crash_before_ledger
            elif active == "ledger_confirmation_exception":
                def fail_ledger_confirmation(self, *args, **kwargs):
                    raise ValueError("injected Core ledger confirmation failure")
                StateStore.record_with_events = fail_ledger_confirmation
            elif active == "ledger_before_response":
                original = StateStore.record_with_events
                def crash_after_ledger(self, *args, **kwargs):
                    original(self, *args, **kwargs)
                    os._exit(72)
                StateStore.record_with_events = crash_after_ledger

        sys.stdin = io.StringIO(raw)
        from yushuos_task.plugin import main
        raise SystemExit(main())
        """
    )


def _install_task(tmp_path: Path, *, crash_stage: str | None = None) -> tuple[CoreRuntime, Path]:
    config_root = tmp_path / "core"
    _write_core_config(config_root)

    source = tmp_path / "task-source"
    source.mkdir()
    shutil.copyfile(TASK_ROOT / "plugin" / "plugin.yaml", source / "plugin.yaml")
    shutil.copyfile(TASK_ROOT / "plugin" / "run.py", source / "run.py")
    shutil.copytree(TASK_ROOT / "src" / "yushuos_task", source / "src" / "yushuos_task")
    if crash_stage:
        (source / "run.py").write_text(_fault_bootstrap(crash_stage), encoding="utf-8", newline="\n")

    locked = _core_cli(config_root, "lock-plugin", "--path", str(source))
    assert locked["status"] == "succeeded" and locked["verified"] is True
    installed = _core_cli(config_root, "install-plugin", "--path", str(source))
    assert installed["status"] == "succeeded" and installed["installed"] is True

    ledger = config_root / "operations.sqlite3"
    core_state = StateStore(ledger)
    with core_state.connect(write=True):
        pass
    return CoreRuntime(config_root), config_root


def _request(request_id: str, capability: str, fields: dict, *, target_task_id: str | None = None) -> dict:
    target = {"store_id": STORE_ID}
    if target_task_id is not None:
        target["task_id"] = target_task_id
    return {
        "request_id": request_id,
        "capability": capability,
        "intent": "query" if capability in {"task.get", "task.list"} else "command",
        "fields": fields,
        "target": target,
    }


def _event_rows(runtime: CoreRuntime) -> list[dict]:
    with runtime.state.connect() as db:
        rows = db.execute("SELECT envelope FROM event_outbox ORDER BY request_id,event_id").fetchall()
    return [json.loads(row[0]) for row in rows]


def test_create_claim_commit_receipt_event_and_replay_use_original_snapshot(tmp_path):
    runtime, config_root = _install_task(tmp_path)
    original_request = _request("create-replay-1", "task.create", {"title": "original"})

    created = runtime.invoke(original_request, mode="execute", host_mode="execute")
    assert created.status == "succeeded"
    assert created.data["changed"] is True
    original_task = created.data["task"]
    assert original_task["title"] == "original"
    assert original_task["version"] == 1

    updated = runtime.invoke(
        _request(
            "update-after-create-1",
            "task.update",
            {"task_id": original_task["id"], "expected_version": 1, "changes": {"title": "new title"}},
            target_task_id=original_task["id"],
        ),
        mode="execute",
        host_mode="execute",
    )
    assert updated.status == "succeeded"
    assert updated.data["task"]["title"] == "new title"
    assert updated.data["task"]["version"] == 2

    replayed = runtime.invoke(original_request, mode="execute", host_mode="execute")
    assert replayed.status == "succeeded"
    assert replayed.data == created.data
    assert sorted(row["type"] for row in _event_rows(runtime)) == ["task.created", "task.updated"]
    receipt = runtime.state.receipt(original_request["request_id"])
    assert receipt["status"] == "succeeded"
    assert "data" not in receipt and "title" not in json.dumps(receipt, ensure_ascii=False)
    assert "original" not in json.dumps(_event_rows(runtime), ensure_ascii=False)

    from yushuos_task.storage import TaskStore

    task_store = TaskStore(config_root / TASK_DB_RELATIVE)
    latest = task_store.get_task(STORE_ID, original_task["id"])
    assert latest is not None and latest.title == "new title" and latest.version == 2


def test_task_read_and_preview_do_not_create_database_claim_or_uuid(tmp_path):
    runtime, config_root = _install_task(tmp_path)
    task_db = config_root / TASK_DB_RELATIVE
    list_request = _request("list-before-write-1", "task.list", {})

    listed = runtime.invoke(list_request, mode="execute", host_mode="execute")
    assert listed.status == "succeeded"
    assert listed.data == {"tasks": [], "next_cursor": None}
    assert not task_db.exists()
    assert runtime.state.receipt(list_request["request_id"]) is None

    missing_get = runtime.invoke(
        _request(
            "get-before-write-1", "task.get",
            {"task_id": "tsk_" + "0" * 32}, target_task_id="tsk_" + "0" * 32,
        ),
        mode="execute", host_mode="execute",
    )
    assert missing_get.status == "failed" and missing_get.error["code"] == "task.not_found"
    assert not task_db.exists()

    create_request = _request("preview-create-1", "task.create", {"title": "planned"})
    binding = runtime._binding("task.create")
    request = runtime._request(create_request)
    preview = runtime.runner.invoke(binding, request, mode="preview", host_mode="execute")
    assert preview.status == "preview"
    assert preview.data["operation_status"] == "preview"
    assert preview.data["result_state"] == "planned"
    assert preview.data["planned"]["title"] == "planned"
    assert "task" not in preview.data and "task_id" not in preview.data
    assert not task_db.exists()
    assert runtime.state.receipt(create_request["request_id"]) is None


@pytest.mark.parametrize(
    ("stage", "recovery_action", "expected_status", "proof_exists"),
    [
        ("before_task_commit", "resume", "unknown", False),
        ("task_commit_before_ledger", "resume", "succeeded", True),
        ("ledger_before_response", "invoke", "succeeded", True),
        ("ledger_confirmation_exception", "resume", "succeeded", True),
    ],
)
def test_real_child_process_crash_windows_recover_without_duplicate_business_write(
    tmp_path, stage, recovery_action, expected_status, proof_exists
):
    runtime, config_root = _install_task(tmp_path, crash_stage=stage)
    request = _request(f"crash-{stage}", "task.create", {"title": f"{stage} task"})
    marker = config_root / ".task-crash-stage"
    marker.write_text(stage, encoding="utf-8")

    crashed = runtime.invoke(request, mode="execute", host_mode="execute")
    assert crashed.status == "unknown"
    marker.unlink()

    if recovery_action == "resume":
        recovered = runtime.resume(request, host_mode="execute")
    else:
        recovered = runtime.invoke(request, mode="execute", host_mode="execute")
    assert recovered.status == expected_status

    from yushuos_task.storage import TaskStore

    task_db = config_root / TASK_DB_RELATIVE
    proof = TaskStore(task_db).lookup_commit(PLUGIN_ID, STORE_ID, request["request_id"])
    assert (proof is not None) is proof_exists
    from yushuos_task.domain import TaskFilters

    task_rows = TaskStore(task_db).list_tasks(STORE_ID, TaskFilters()) if task_db.exists() else None
    expected_count = 0 if not proof_exists else 1
    assert (len(task_rows.tasks) if task_rows is not None else 0) == expected_count
    assert len(_event_rows(runtime)) == expected_count
    if proof_exists and recovered.status == "succeeded":
        assert recovered.data["task"]["title"] == f"{stage} task"
        with sqlite3.connect(task_db) as db:
            state = db.execute(
                "SELECT event_recovery_state FROM task_request_commits WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()[0]
        assert state == ("pending" if stage == "ledger_before_response" else "ledger_recorded")


def test_update_commit_crash_recovers_original_mutation_once(tmp_path):
    runtime, config_root = _install_task(tmp_path, crash_stage="mutation_commit_before_ledger")
    created = runtime.invoke(
        _request("update-crash-setup", "task.create", {"title": "before", "tags": ["old"]}),
        mode="execute", host_mode="execute",
    )
    assert created.status == "succeeded"
    task_id = created.data["task"]["id"]
    update_request = _request(
        "update-crash-operation", "task.update",
        {"task_id": task_id, "expected_version": 1, "changes": {"title": "after", "tags": ["new"]}},
        target_task_id=task_id,
    )
    marker = config_root / ".task-crash-stage"
    marker.write_text("mutation_commit_before_ledger", encoding="utf-8")

    crashed = runtime.invoke(update_request, mode="execute", host_mode="execute")
    assert crashed.status == "unknown"
    marker.unlink()
    recovered = runtime.resume(update_request, host_mode="execute")
    assert recovered.status == "succeeded"
    assert recovered.data["task"]["title"] == "after"
    assert recovered.data["task"]["tags"] == ["new"]
    assert recovered.data["task"]["version"] == 2

    from yushuos_task.storage import TaskStore

    store = TaskStore(config_root / TASK_DB_RELATIVE)
    task = store.get_task(STORE_ID, task_id)
    assert task is not None and task.version == 2 and task.title == "after"
    assert sorted(row["type"] for row in _event_rows(runtime)) == ["task.created", "task.updated"]


def test_reconcile_exception_stays_unknown_until_explicit_resume_succeeds(tmp_path):
    runtime, config_root = _install_task(tmp_path, crash_stage="task_commit_then_reconcile_exception")
    request = _request("reconcile-retry-create", "task.create", {"title": "reconcile me"})
    marker = config_root / ".task-crash-stage"
    marker.write_text("task_commit_then_reconcile_exception", encoding="utf-8")

    crashed = runtime.invoke(request, mode="execute", host_mode="execute")
    assert crashed.status == "unknown"
    first_resume = runtime.resume(request, host_mode="execute")
    assert first_resume.status == "unknown"
    assert runtime.state.effective_receipt(request["request_id"]).get("confirmation") is None
    assert _event_rows(runtime) == []

    marker.unlink()
    recovered = runtime.resume(request, host_mode="execute")
    assert recovered.status == "succeeded"
    assert len(_event_rows(runtime)) == 1


def test_all_write_transitions_get_and_list_preserve_version_and_noop_event_rules(tmp_path):
    runtime, _ = _install_task(tmp_path)
    created = runtime.invoke(
        _request("lifecycle-create", "task.create", {"title": "Lifecycle", "tags": ["demo"]}),
        mode="execute",
        host_mode="execute",
    )
    assert created.status == "succeeded"
    task = created.data["task"]
    task_id = task["id"]

    got = runtime.invoke(
        _request("lifecycle-get", "task.get", {"task_id": task_id}, target_task_id=task_id),
        mode="execute",
        host_mode="execute",
    )
    assert got.status == "succeeded" and got.data["task"] == task

    no_change = runtime.invoke(
        _request(
            "lifecycle-noop-update", "task.update",
            {"task_id": task_id, "expected_version": 1, "changes": {"title": "Lifecycle"}},
            target_task_id=task_id,
        ),
        mode="execute",
        host_mode="execute",
    )
    assert no_change.status == "succeeded"
    assert no_change.data["changed"] is False and no_change.data["task"]["version"] == 1
    assert len(_event_rows(runtime)) == 1

    completed = runtime.invoke(
        _request("lifecycle-complete", "task.complete", {"task_id": task_id, "expected_version": 1}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert completed.status == "succeeded" and completed.data["task"]["status"] == "completed"
    assert completed.data["task"]["version"] == 2 and completed.data["task"]["completed_at"]

    repeated_complete = runtime.invoke(
        _request("lifecycle-complete-noop", "task.complete", {"task_id": task_id, "expected_version": 2}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert repeated_complete.status == "succeeded" and repeated_complete.data["changed"] is False
    assert len(_event_rows(runtime)) == 2

    reopened = runtime.invoke(
        _request("lifecycle-reopen", "task.reopen", {"task_id": task_id, "expected_version": 2}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert reopened.status == "succeeded" and reopened.data["task"]["status"] == "open"
    assert reopened.data["task"]["version"] == 3 and reopened.data["task"]["completed_at"] is None

    deleted = runtime.invoke(
        _request("lifecycle-delete", "task.delete", {"task_id": task_id, "expected_version": 3}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert deleted.status == "succeeded" and deleted.data["task"]["status"] == "deleted"
    assert deleted.data["task"]["version"] == 4 and deleted.data["task"]["deleted_at"]

    repeated_delete = runtime.invoke(
        _request("lifecycle-delete-noop", "task.delete", {"task_id": task_id, "expected_version": 4}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert repeated_delete.status == "succeeded" and repeated_delete.data["changed"] is False
    assert len(_event_rows(runtime)) == 4
    assert {row["type"] for row in _event_rows(runtime)} == {
        "task.created", "task.completed", "task.reopened", "task.deleted",
    }

    hidden = runtime.invoke(
        _request("lifecycle-get-deleted-hidden", "task.get", {"task_id": task_id}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert hidden.status == "failed" and hidden.error["code"] == "task.not_found"
    visible = runtime.invoke(
        _request("lifecycle-get-deleted-visible", "task.get", {"task_id": task_id, "include_deleted": True}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert visible.status == "succeeded" and visible.data["task"]["status"] == "deleted"
    listed = runtime.invoke(
        _request("lifecycle-list-deleted", "task.list", {"status": ["deleted"], "include_deleted": True}),
        mode="execute", host_mode="execute",
    )
    assert [item["id"] for item in listed.data["tasks"]] == [task_id]


def test_list_filter_and_cursor_pagination_are_bound_to_the_same_query(tmp_path):
    runtime, _ = _install_task(tmp_path)
    for request_id, title, project, tag in (
        ("page-create-a", "A", "project-a", "keep"),
        ("page-create-b", "B", "project-b", "keep"),
        ("page-create-c", "C", "project-a", "keep"),
    ):
        result = runtime.invoke(
            _request(request_id, "task.create", {"title": title, "project_ref": project, "tags": [tag]}),
            mode="execute", host_mode="execute",
        )
        assert result.status == "succeeded"

    first = runtime.invoke(
        _request("page-list-first", "task.list", {"project_ref": "project-a", "tag": "keep", "limit": 1}),
        mode="execute", host_mode="execute",
    )
    assert first.status == "succeeded" and len(first.data["tasks"]) == 1
    assert first.data["next_cursor"]
    second = runtime.invoke(
        _request(
            "page-list-second", "task.list",
            {"project_ref": "project-a", "tag": "keep", "limit": 1, "cursor": first.data["next_cursor"]},
        ),
        mode="execute", host_mode="execute",
    )
    assert second.status == "succeeded" and len(second.data["tasks"]) == 1
    assert first.data["tasks"][0]["id"] != second.data["tasks"][0]["id"]
    assert first.data["tasks"][0]["project_ref"] == second.data["tasks"][0]["project_ref"] == "project-a"
    assert second.data["next_cursor"] is None


def test_expired_result_replays_minimal_success_and_read_replay_do_not_purge(tmp_path):
    runtime, config_root = _install_task(tmp_path)
    first_request = _request("expiry-original", "task.create", {"title": "expired body"})
    first = runtime.invoke(first_request, mode="execute", host_mode="execute")
    assert first.status == "succeeded"
    task_id = first.data["task"]["id"]
    task_db = config_root / TASK_DB_RELATIVE

    with sqlite3.connect(task_db) as db:
        db.execute(
            "UPDATE task_request_commits SET result_body_expires_at='2000-01-01T00:00:00.000000Z' WHERE request_id=?",
            (first_request["request_id"],),
        )

    got = runtime.invoke(
        _request("expiry-get", "task.get", {"task_id": task_id}, target_task_id=task_id),
        mode="execute", host_mode="execute",
    )
    assert got.status == "succeeded"
    with sqlite3.connect(task_db) as db:
        assert db.execute(
            "SELECT result_body IS NOT NULL FROM task_request_commits WHERE request_id=?",
            (first_request["request_id"],),
        ).fetchone()[0] == 1

    replayed = runtime.invoke(first_request, mode="execute", host_mode="execute")
    assert replayed.status == "succeeded"
    assert replayed.data == {
        "operation_status": "committed",
        "result_state": "expired",
        "code": "task.result_expired",
        "task_id": task_id,
        "original_request_id": first_request["request_id"],
    }

    second = runtime.invoke(
        _request("expiry-trigger-purge", "task.create", {"title": "new write"}),
        mode="execute", host_mode="execute",
    )
    assert second.status == "succeeded"
    with sqlite3.connect(task_db) as db:
        retained = db.execute(
            "SELECT result_body,task_id,event_recovery_state FROM task_request_commits WHERE request_id=?",
            (first_request["request_id"],),
        ).fetchone()
    assert retained[0] is None and retained[1] == task_id and retained[2] == "ledger_recorded"


def test_permissions_scope_and_missing_core_binding_fail_closed_without_task_storage(tmp_path):
    runtime, config_root = _install_task(tmp_path)
    task_db = config_root / TASK_DB_RELATIVE
    request = _request("denied-write", "task.create", {"title": "must not be written"})

    config = _read_core_config(config_root)
    config["permissions"]["denials"] = ["task.write"]
    _save_core_config(config_root, config)
    denied = CoreRuntime(config_root).invoke(request, mode="execute", host_mode="execute")
    assert denied.status == "unavailable"
    assert not task_db.exists()
    assert runtime.state.receipt(request["request_id"]) is None

    config["permissions"]["denials"] = []
    config["bindings"]["resources"]["task_store"] = "different-store"
    _save_core_config(config_root, config)
    wrong_store = CoreRuntime(config_root).invoke(request, mode="execute", host_mode="execute")
    assert wrong_store.status == "unavailable"
    assert not task_db.exists()


def test_local_runner_refuses_write_without_core_bound_operation_context(tmp_path, monkeypatch, capsys):
    runtime, config_root = _install_task(tmp_path)
    request_value = _request("direct-unbound-write", "task.create", {"title": "must not commit"})
    binding = runtime._binding("task.create")
    request = runtime._request(request_value)
    captured: dict = {}

    def capture(args, **kwargs):
        captured.update(json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "status": "unknown", "request_id": request.request_id, "message": "test capture",
            "resource": {}, "data": None, "error": None,
        }), "")

    runtime.runner.run = capture
    runtime.runner.invoke(binding, request, mode="execute", host_mode="execute")
    unbound_request_id = "unbound-no-core-context"
    captured["request"]["request_id"] = unbound_request_id
    captured["context"]["request_id"] = unbound_request_id
    import io
    from yushuos_task.plugin import main

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(captured)))
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "task.validation_error"
    assert runtime.state.receipt(unbound_request_id) is None
    assert not (config_root / TASK_DB_RELATIVE).exists()


def test_core_version_gate_and_real_cli_disable_uninstall_preserve_shared_data(tmp_path, monkeypatch):
    from yushuos_task import plugin

    core_version_supported = plugin.core_version_supported

    assert core_version_supported("0.3.1")
    assert core_version_supported("0.3.19+candidate")
    assert not core_version_supported("0.3.0")
    assert not core_version_supported("0.4.0")
    assert not core_version_supported("0.3.1rc1")
    monkeypatch.setattr(plugin, "CORE_VERSION", "0.4.0")
    with pytest.raises(ValueError, match="Core 版本"):
        plugin.execute({})

    runtime, config_root = _install_task(tmp_path)
    created = runtime.invoke(
        _request("lifecycle-install-write", "task.create", {"title": "keep data on uninstall"}),
        mode="execute", host_mode="execute",
    )
    assert created.status == "succeeded"
    catalog = _core_cli(config_root, "catalog", "--details")
    plugin = next(item for item in catalog["plugins"] if item["id"] == PLUGIN_ID)
    assert plugin["lifecycle_state"] == "ready"

    config = _read_core_config(config_root)
    config["user_disabled"] = [PLUGIN_ID]
    _save_core_config(config_root, config)
    disabled_catalog = _core_cli(config_root, "catalog", "--details")
    disabled = next(item for item in disabled_catalog["plugins"] if item["id"] == PLUGIN_ID)
    assert disabled["lifecycle_state"] == "disabled_by_user"

    # Core has no uninstall-plugin command. After disabling, remove only the
    # resolved immutable version directory; retain shared ledger and plugin data.
    installed_version = config_root / "plugins" / PLUGIN_ID / "0.2.0"
    assert installed_version.is_dir()
    shutil.rmtree(installed_version)
    doctor = _core_cli(config_root, "doctor")
    assert doctor["manifest_errors"] == []
    assert doctor["plugin_count"] == 0
    assert (config_root / "operations.sqlite3").is_file()
    assert (config_root / TASK_DB_RELATIVE).is_file()


def test_task_outbox_imports_one_persistent_core_event_and_is_idempotent(tmp_path):
    runtime, config_root = _install_task(tmp_path)
    request = _request("event-import-create", "task.create", {"title": "private task title"})
    created = runtime.invoke(request, mode="execute", host_mode="execute")
    assert created.status == "succeeded"
    task_id = created.data["task"]["id"]
    core_event = _event_rows(runtime)[0]
    assert core_event["type"] == "task.created"

    from yushuos.automation import AutomationEngine
    from yushuos.registry import provider_digest

    engine = AutomationEngine(runtime)
    assert engine.import_outbox() == 1
    assert engine.import_outbox() == 0
    imported = engine.store.list_events()
    assert len(imported) == 1
    event = imported[0]
    assert event["id"] == core_event["id"]
    assert event["type"] == "task.created"
    assert event["source_plugin"] == PLUGIN_ID
    assert event["source_version"] == "0.2.0"
    assert event["provider_digest"] == provider_digest(runtime.registry.plugins[PLUGIN_ID], runtime.config)
    assert event["resource_refs"] == {"task_id": task_id}
    assert "private task title" not in json.dumps(event, ensure_ascii=False)
    with runtime.state.connect() as db:
        assert db.execute("SELECT imported FROM event_outbox WHERE event_id=?", (event["id"],)).fetchone()[0] == 1


def test_abandoned_recovery_keeps_lock_and_does_not_confirm_local_commit(tmp_path):
    runtime, config_root = _install_task(tmp_path, crash_stage="task_commit_before_ledger")
    request = _request("abandoned-commit-proof", "task.create", {"title": "leave for operator"})
    marker = config_root / ".task-crash-stage"
    marker.write_text("task_commit_before_ledger", encoding="utf-8")
    assert runtime.invoke(request, mode="execute", host_mode="execute").status == "unknown"
    marker.unlink()

    runtime.state.resolve(
        request["request_id"], "abandoned", actor="operator", reason_code="manual_review_required",
    )
    resumed = runtime.resume(request, host_mode="execute")
    assert resumed.status == "unavailable"
    assert runtime.state.effective_receipt(request["request_id"])["status"] == "abandoned"
    assert runtime.state.effective_receipt(request["request_id"]).get("confirmation") is None
    with runtime.state.connect() as db:
        assert db.execute("SELECT 1 FROM locks WHERE request_id=?", (request["request_id"],)).fetchone()
        assert db.execute("SELECT 1 FROM event_outbox WHERE request_id=?", (request["request_id"],)).fetchone() is None
    from yushuos_task.storage import TaskStore

    proof = TaskStore(config_root / TASK_DB_RELATIVE).lookup_commit(PLUGIN_ID, STORE_ID, request["request_id"])
    assert proof is not None


def test_provider_version_drift_blocks_recovery_and_keeps_the_original_lock(tmp_path):
    runtime, config_root = _install_task(tmp_path, crash_stage="task_commit_before_ledger")
    request = _request("provider-drift-create", "task.create", {"title": "must remain locked"})
    marker = config_root / ".task-crash-stage"
    marker.write_text("task_commit_before_ledger", encoding="utf-8")
    crashed = runtime.invoke(request, mode="execute", host_mode="execute")
    assert crashed.status == "unknown"
    marker.unlink()

    source_v2 = tmp_path / "task-source-0.2.1"
    shutil.copytree(config_root / "plugins" / PLUGIN_ID / "0.2.0", source_v2)
    manifest_path = source_v2 / "plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "0.2.1"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    locked = _core_cli(config_root, "lock-plugin", "--path", str(source_v2))
    assert locked["status"] == "succeeded"
    installed = _core_cli(config_root, "install-plugin", "--path", str(source_v2))
    assert installed["status"] == "succeeded"

    config = _read_core_config(config_root)
    config["plugins"]["versions"][PLUGIN_ID] = "0.2.1"
    _save_core_config(config_root, config)
    changed_provider_runtime = CoreRuntime(config_root)
    blocked = changed_provider_runtime.resume(request, host_mode="execute")
    assert blocked.status == "unavailable"
    assert "provider" in blocked.message.lower()
    assert changed_provider_runtime.state.effective_receipt(request["request_id"])["status"] == "dispatched"
    with changed_provider_runtime.state.connect() as db:
        assert db.execute("SELECT 1 FROM locks WHERE request_id=?", (request["request_id"],)).fetchone()
    assert _event_rows(changed_provider_runtime) == []


@pytest.mark.parametrize("operation,event_type", [("task.cancel", "task.cancelled"), ("task.archive", "task.archived")])
def test_extension_crash_restart_recovers_once_and_replay_keeps_original_result(tmp_path, operation, event_type):
    runtime, config_root = _install_task(tmp_path, crash_stage="mutation_commit_before_ledger")
    created = runtime.invoke(_request("ext-create", "task.create", {"title": "private original"}), mode="execute", host_mode="execute")
    assert created.status == "succeeded"
    task_id = created.data["task"]["id"]
    request = _request("ext-operation", operation, {"task_id": task_id, "expected_version": 1}, target_task_id=task_id)
    marker = config_root / ".task-crash-stage"
    marker.write_text("mutation_commit_before_ledger", encoding="utf-8")
    assert runtime.invoke(request, mode="execute", host_mode="execute").status == "unknown"
    original_receipt = runtime.state.receipt("ext-operation")
    from yushuos_task.storage import TaskStore
    store = TaskStore(config_root / TASK_DB_RELATIVE)
    proof_before = store.lookup_commit(PLUGIN_ID, STORE_ID, "ext-operation")
    assert proof_before is not None and proof_before.event_recovery_state == "pending"
    marker.unlink()
    restarted = CoreRuntime(config_root)
    recovered = restarted.resume(request, host_mode="execute")
    assert recovered.status == "succeeded" and recovered.data["task"]["version"] == 2
    assert restarted.state.receipt("ext-operation") == original_receipt
    assert restarted.resume(request, host_mode="execute").data == recovered.data
    events_before = _event_rows(restarted)
    assert [row["type"] for row in events_before].count(event_type) == 1
    assert all(row["resource_refs"] == {"task_id": task_id} for row in events_before)
    reopen = restarted.invoke(_request("ext-reopen", "task.reopen", {"task_id": task_id, "expected_version": 2}, target_task_id=task_id), mode="execute", host_mode="execute")
    assert reopen.status == "succeeded" and reopen.data["task"]["status"] == "open"
    assert reopen.data["task"]["archived_at"] is None
    replay = restarted.invoke(request, mode="execute", host_mode="execute")
    assert replay.data == recovered.data
    proof_after = store.lookup_commit(PLUGIN_ID, STORE_ID, "ext-operation")
    assert proof_after.result_body == proof_before.result_body
    assert proof_after.event_intents == proof_before.event_intents
    assert [row for row in _event_rows(restarted) if row["request_id"] == "ext-operation"] == [row for row in events_before if row["request_id"] == "ext-operation"]
    assert "private original" not in json.dumps(_event_rows(restarted))


@pytest.mark.parametrize("operation", ["task.cancel", "task.archive"])
def test_extension_preview_permissions_scope_noop_and_version_conflict(tmp_path, operation):
    runtime, config_root = _install_task(tmp_path)
    task_id = "tsk_" + "1" * 32
    request = _request("ext-gates", operation, {"task_id": task_id, "expected_version": 1}, target_task_id=task_id)
    preview = runtime.invoke(request, mode="preview", host_mode="execute")
    assert preview.status == "preview" and not (config_root / TASK_DB_RELATIVE).exists()
    assert runtime.state.receipt("ext-gates") is None
    config = _read_core_config(config_root)
    for permission in ("task.read", "task.write"):
        config["permissions"]["denials"] = [permission]
        _save_core_config(config_root, config)
        assert CoreRuntime(config_root).invoke(request, mode="execute", host_mode="execute").status == "unavailable"
    config["permissions"]["denials"] = []
    config["bindings"]["resources"]["task_store"] = "wrong-store"
    _save_core_config(config_root, config)
    assert CoreRuntime(config_root).invoke(request, mode="execute", host_mode="execute").status == "unavailable"
    assert not (config_root / TASK_DB_RELATIVE).exists()
    config["bindings"]["resources"]["task_store"] = STORE_ID
    _save_core_config(config_root, config)
    runtime = CoreRuntime(config_root)
    created = runtime.invoke(_request("ext-gates-create", "task.create", {"title": "test"}), mode="execute", host_mode="execute")
    task_id = created.data["task"]["id"]
    request = _request("ext-gates-write", operation, {"task_id": task_id, "expected_version": 1}, target_task_id=task_id)
    changed = runtime.invoke(request, mode="execute", host_mode="execute")
    assert changed.status == "succeeded" and changed.data["changed"]
    stale = runtime.invoke(_request("ext-gates-stale", operation, {"task_id": task_id, "expected_version": 1}, target_task_id=task_id), mode="execute", host_mode="execute")
    assert stale.status == "failed" and stale.error["code"] == "task.version_conflict"
    repeated = runtime.invoke(_request("ext-gates-repeat", operation, {"task_id": task_id, "expected_version": 2}, target_task_id=task_id), mode="execute", host_mode="execute")
    assert repeated.status == "succeeded" and repeated.data["changed"] is False
    assert repeated.data["task"] == changed.data["task"]
    assert len(_event_rows(runtime)) == 2
    listed = runtime.invoke(_request("ext-gates-list", "task.list", {}), mode="execute", host_mode="execute")
    assert len(listed.data["tasks"]) == (0 if operation == "task.archive" else 1)
    included = runtime.invoke(_request("ext-gates-list-include", "task.list", {"include_archived": True}), mode="execute", host_mode="execute")
    assert len(included.data["tasks"]) == 1

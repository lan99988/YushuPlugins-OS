"""SQLite repository for Task facts and their local commit proofs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import binascii
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Iterator, Mapping

from .contracts import FINGERPRINT_SCHEME, PLUGIN_ID
from .domain import (
    Mutation,
    Task,
    TaskDeletedError,
    TaskFilters,
    TaskNotFound,
    TaskRequestConflict,
    TaskSchemaVersionError,
    TaskStoreIdentityError,
    TaskValidationError,
    TaskVersionConflict,
    apply_transition,
    apply_update,
    task_from_fields,
    utc_timestamp,
)


SCHEMA_VERSION = 2
RESULT_RETENTION_DAYS = 180
_STORE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9_-]*)*$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_TASK_ID = re.compile(r"^tsk_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
_WRITE_OPERATIONS = frozenset({
    "task.create", "task.update", "task.complete", "task.reopen", "task.delete", "task.cancel", "task.archive",
})
_EVENT_TYPES = frozenset({
    "task.created", "task.updated", "task.completed", "task.reopened", "task.deleted", "task.cancelled", "task.archived",
})
_EVENT_STATES = frozenset({"none", "pending", "ledger_recorded"})


class TaskStorageError(RuntimeError):
    """Base class for storage failures that are safe to expose by stable code."""

    code = "task.storage_error"


class TaskDatabaseError(TaskStorageError):
    code = "task.database_error"


@dataclass(frozen=True)
class CommitIdentity:
    """Core-bound identity persisted with one committed Task operation."""

    plugin_id: str
    store_id: str
    request_id: str
    request_fingerprint: str
    fingerprint_scheme: str
    operation_name: str
    core_project_ref: str
    provider_version: str
    provider_digest: str

    def validate(self, expected_plugin_id: str = PLUGIN_ID) -> None:
        if not isinstance(self.plugin_id, str) or not _PLUGIN_ID.fullmatch(self.plugin_id):
            raise TaskValidationError("plugin_id 格式无效")
        if self.plugin_id != expected_plugin_id:
            raise TaskStoreIdentityError("提交证明 plugin_id 与当前 Task 插件不匹配")
        if not isinstance(self.store_id, str) or not _STORE_ID.fullmatch(self.store_id):
            raise TaskValidationError("store_id 格式无效")
        if not isinstance(self.request_id, str) or not _REQUEST_ID.fullmatch(self.request_id):
            raise TaskValidationError("request_id 格式无效")
        if not isinstance(self.request_fingerprint, str) or not _SHA256.fullmatch(self.request_fingerprint):
            raise TaskValidationError("request_fingerprint 必须是小写 SHA-256")
        if self.fingerprint_scheme != FINGERPRINT_SCHEME:
            raise TaskValidationError("Task 内部写入只接受 Core 绑定的 jcs-operation-v1 指纹")
        if self.operation_name not in _WRITE_OPERATIONS:
            raise TaskValidationError("operation_name 不是已登记的 Task 写能力")
        if not isinstance(self.core_project_ref, str) or len(self.core_project_ref) > 200:
            raise TaskValidationError("Core project_ref 格式无效")
        if not isinstance(self.provider_version, str) or not _PROVIDER_VERSION.fullmatch(self.provider_version):
            raise TaskValidationError("provider_version 格式无效")
        if not isinstance(self.provider_digest, str) or not _SHA256.fullmatch(self.provider_digest):
            raise TaskValidationError("provider_digest 必须是小写 SHA-256")


@dataclass(frozen=True)
class TaskPage:
    tasks: tuple[Task, ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"tasks": [task.to_dict() for task in self.tasks], "next_cursor": self.next_cursor}


@dataclass(frozen=True)
class TaskCommitProof:
    plugin_id: str
    store_id: str
    request_id: str
    request_fingerprint: str
    fingerprint_scheme: str
    operation_name: str
    core_project_ref: str
    provider_version: str
    provider_digest: str
    task_id: str
    operation_status: str
    changed: bool
    error_code: str | None
    result_body: str | None
    committed_at: str
    result_body_expires_at: str
    event_intents: tuple[dict[str, Any], ...]
    event_recovery_state: str

    def result_data(self, *, now: datetime | str | None = None) -> dict[str, Any]:
        """Return the original snapshot or its intentionally minimal expiry result."""
        now_value = utc_timestamp(now or datetime.now(timezone.utc))
        if self.result_body is None or self.result_body_expires_at <= now_value:
            return {
                "operation_status": "committed",
                "result_state": "expired",
                "code": "task.result_expired",
                "task_id": self.task_id,
                "original_request_id": self.request_id,
            }
        try:
            body = json.loads(self.result_body)
        except (TypeError, ValueError):
            raise TaskDatabaseError("已保存的操作结果快照无法解析") from None
        if not isinstance(body, dict) or body.get("operation_status") != "committed" or body.get("result_state") != "available":
            raise TaskDatabaseError("已保存的操作结果快照结构无效")
        if "task" not in body or type(body.get("changed")) is not bool:
            raise TaskDatabaseError("已保存的 Task 操作快照缺少原始结果")
        return body


@dataclass(frozen=True)
class CommitOutcome:
    data: dict[str, Any]
    event_intents: tuple[dict[str, Any], ...]
    proof: TaskCommitProof


class TaskStore:
    """Own one private Task SQLite file and its single-store binding."""

    def __init__(
        self,
        path: str | Path,
        *,
        plugin_id: str = PLUGIN_ID,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(plugin_id, str) or not _PLUGIN_ID.fullmatch(plugin_id):
            raise TaskValidationError("plugin_id 格式无效")
        if (not isinstance(busy_timeout_seconds, (int, float)) or isinstance(busy_timeout_seconds, bool)
                or not math.isfinite(busy_timeout_seconds) or busy_timeout_seconds <= 0):
            raise TaskValidationError("busy_timeout_seconds 必须是正数")
        self.path = Path(path).expanduser().absolute()
        self.plugin_id = plugin_id
        self.busy_timeout_seconds = float(busy_timeout_seconds)

    @contextmanager
    def _write_db(self, store_id: str) -> Iterator[sqlite3.Connection]:
        if not isinstance(store_id, str) or not _STORE_ID.fullmatch(store_id):
            raise TaskValidationError("store_id 格式无效")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            db = sqlite3.connect(self.path, timeout=self.busy_timeout_seconds, isolation_level=None)
        except sqlite3.Error as exc:
            raise TaskDatabaseError("无法打开 Task 数据库") from exc
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={max(1, int(self.busy_timeout_seconds * 1000))}")
        try:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_schema(db, store_id, create=True)
            yield db
            db.commit()
        except sqlite3.OperationalError as exc:
            db.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise TaskDatabaseError("Task 数据库当前繁忙，已在有界等待后停止") from exc
            raise TaskDatabaseError("Task 数据库操作失败") from exc
        except sqlite3.Error as exc:
            db.rollback()
            raise TaskDatabaseError("Task 数据库约束或事务操作失败") from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _read_db(self, store_id: str) -> Iterator[sqlite3.Connection | None]:
        if not isinstance(store_id, str) or not _STORE_ID.fullmatch(store_id):
            raise TaskValidationError("store_id 格式无效")
        if not self.path.exists():
            yield None
            return
        try:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=self.busy_timeout_seconds)
        except sqlite3.Error as exc:
            raise TaskDatabaseError("无法以只读方式打开 Task 数据库") from exc
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = self._tables(db)
            if user_version == 0 and not tables:
                # A rolled-back first write may leave a zero-table SQLite file.
                # Treat it like an absent store for read-only operations.
                yield None
                return
            self._ensure_schema(db, store_id, create=False)
            yield db
        except sqlite3.Error as exc:
            raise TaskDatabaseError("Task 数据库读取失败") from exc
        finally:
            db.close()

    @staticmethod
    def _tables(db: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }

    def _ensure_schema(self, db: sqlite3.Connection, store_id: str, *, create: bool) -> None:
        try:
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            raise TaskSchemaVersionError("无法读取 Task schema 版本") from exc
        tables = self._tables(db)
        if user_version > SCHEMA_VERSION or user_version < 0:
            raise TaskSchemaVersionError("Task 数据库 schema 高于当前实现或版本无效")

        if user_version == 0:
            if tables:
                raise TaskSchemaVersionError("Task 数据库没有已知 schema 版本，拒绝覆盖")
            if not create:
                raise TaskSchemaVersionError("Task 数据库尚未初始化")
            self._create_schema_v2(db, store_id)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return

        required_tables = {"schema_meta", "tasks", "task_tags", "task_request_commits"}
        if not required_tables <= tables:
            raise TaskSchemaVersionError("Task 数据库缺少 schema v1 表")
        row = db.execute(
            "SELECT schema_version, store_id FROM schema_meta WHERE singleton=1"
        ).fetchone()
        if row is None or row["schema_version"] != user_version:
            raise TaskSchemaVersionError("Task schema_meta 与实现版本不匹配")
        if row["store_id"] != store_id:
            raise TaskStoreIdentityError("当前 Task 数据库绑定到其他 store_id")
        if user_version == 1 and create:
            self._migrate_v1(db, store_id)

    def _migrate_v1(self, db: sqlite3.Connection, store_id: str) -> None:
        """Keep an online v1 backup before a transactional v2 table rebuild.

        BEGIN IMMEDIATE already excludes concurrent writers. A separate read
        connection takes the committed snapshot without backing up this active
        transaction. Core's ledger and all serialized proof fields are untouched.
        """
        backup_path = self.path.with_name(self.path.name + ".schema-v1-" + uuid.uuid4().hex + ".bak")
        pending_path = backup_path.with_name(backup_path.name + ".incomplete")
        created = False
        source = target = None
        try:
            with pending_path.open("xb"):
                pass
            created = True
            source = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=self.busy_timeout_seconds)
            target = sqlite3.connect(pending_path)
            source.backup(target)
            target.close()
            target = None
            source.close()
            source = None
            pending_path.rename(backup_path)
        except (OSError, sqlite3.Error) as exc:
            raise TaskDatabaseError("Task schema v1 备份失败，拒绝迁移") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
            if created and pending_path.exists():
                try:
                    pending_path.unlink()
                except OSError:
                    # An incomplete file is never named as a usable .bak.
                    pass

        # Copy raw values: do not reinterpret original JSON snapshots or pins.
        rows = {name: db.execute("SELECT * FROM " + name).fetchall()
                for name in ("schema_meta", "tasks", "task_tags", "task_request_commits")}
        columns = {name: [column[1] for column in db.execute("PRAGMA table_info(" + name + ")")]
                   for name in rows}
        for name in ("task_request_commits", "task_tags", "tasks", "schema_meta"):
            db.execute("DROP TABLE " + name)
        self._create_schema_v2(db, store_id)
        db.execute("DELETE FROM schema_meta")
        for name in ("schema_meta", "tasks", "task_tags", "task_request_commits"):
            values = [tuple(row) for row in rows[name]]
            if name == "schema_meta":
                version_index = columns[name].index("schema_version")
                values = [tuple(SCHEMA_VERSION if index == version_index else value
                                for index, value in enumerate(row)) for row in values]
            db.executemany("INSERT INTO " + name + "(" + ",".join(columns[name]) + ") VALUES(" +
                           ",".join("?" for _ in columns[name]) + ")", values)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise TaskDatabaseError("Task schema v2 外键校验失败")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _create_schema_v2(db: sqlite3.Connection, store_id: str) -> None:
        ddl = (
            """
            CREATE TABLE schema_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                schema_version INTEGER NOT NULL CHECK(schema_version = 2),
                store_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE tasks (
                store_id TEXT NOT NULL,
                id TEXT NOT NULL,
                title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 500),
                notes TEXT CHECK(notes IS NULL OR length(notes) <= 20000),
                status TEXT NOT NULL CHECK(status IN ('open', 'completed', 'cancelled', 'deleted')),
                priority TEXT NOT NULL CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
                project_ref TEXT CHECK(project_ref IS NULL OR (length(trim(project_ref)) BETWEEN 1 AND 200 AND project_ref = trim(project_ref))),
                due_at TEXT,
                estimate_minutes INTEGER CHECK(estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND 10080),
                source_ref TEXT CHECK(source_ref IS NULL OR (length(trim(source_ref)) BETWEEN 1 AND 200 AND source_ref = trim(source_ref))),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                deleted_at TEXT,
                archived_at TEXT,
                version INTEGER NOT NULL CHECK(version > 0),
                PRIMARY KEY(store_id, id),
                UNIQUE(id),
                CHECK(length(id) = 36 AND substr(id, 1, 4) = 'tsk_'),
                CHECK(substr(id, 5) NOT GLOB '*[^0-9a-f]*'),
                CHECK(length(created_at) = 27 AND substr(created_at, 27, 1) = 'Z'),
                CHECK(length(updated_at) = 27 AND substr(updated_at, 27, 1) = 'Z'),
                CHECK(due_at IS NULL OR (length(due_at) = 27 AND substr(due_at, 27, 1) = 'Z')),
                CHECK(completed_at IS NULL OR (length(completed_at) = 27 AND substr(completed_at, 27, 1) = 'Z')),
                CHECK(deleted_at IS NULL OR (length(deleted_at) = 27 AND substr(deleted_at, 27, 1) = 'Z')),
                CHECK(archived_at IS NULL OR (length(archived_at) = 27 AND substr(archived_at, 27, 1) = 'Z')),
                CHECK(status != 'completed' OR completed_at IS NOT NULL),
                CHECK(status != 'open' OR completed_at IS NULL),
                CHECK(status != 'deleted' OR deleted_at IS NOT NULL),
                CHECK(status = 'deleted' OR deleted_at IS NULL)
            )
            """,
            """
            CREATE TABLE task_tags (
                store_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                tag TEXT NOT NULL CHECK(length(trim(tag)) BETWEEN 1 AND 64 AND tag = trim(tag)),
                position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 31),
                PRIMARY KEY(store_id, task_id, tag),
                UNIQUE(store_id, task_id, position),
                FOREIGN KEY(store_id, task_id) REFERENCES tasks(store_id, id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE task_request_commits (
                plugin_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64),
                fingerprint_scheme TEXT NOT NULL CHECK(fingerprint_scheme = 'jcs-operation-v1'),
                operation_name TEXT NOT NULL CHECK(operation_name IN (
                    'task.create','task.update','task.complete','task.reopen','task.delete','task.cancel','task.archive'
                )),
                core_project_ref TEXT NOT NULL CHECK(length(core_project_ref) <= 200),
                provider_version TEXT NOT NULL,
                provider_digest TEXT NOT NULL CHECK(length(provider_digest) = 64),
                task_id TEXT NOT NULL,
                operation_status TEXT NOT NULL CHECK(operation_status = 'committed'),
                changed INTEGER NOT NULL CHECK(changed IN (0, 1)),
                error_code TEXT,
                result_body TEXT,
                committed_at TEXT NOT NULL,
                result_body_expires_at TEXT NOT NULL,
                event_intents TEXT NOT NULL,
                event_recovery_state TEXT NOT NULL CHECK(event_recovery_state IN ('none','pending','ledger_recorded')),
                PRIMARY KEY(plugin_id, store_id, request_id),
                FOREIGN KEY(store_id, task_id) REFERENCES tasks(store_id, id),
                CHECK((event_recovery_state = 'none' AND event_intents = '[]') OR
                      (event_recovery_state IN ('pending','ledger_recorded') AND event_intents != '[]'))
            )
            """,
            "CREATE INDEX tasks_created_order ON tasks(store_id, created_at DESC, id DESC)",
            "CREATE INDEX tasks_status_order ON tasks(store_id, status, created_at DESC, id DESC)",
            "CREATE INDEX tasks_project_order ON tasks(store_id, project_ref, created_at DESC, id DESC)",
            "CREATE INDEX tasks_priority_order ON tasks(store_id, priority, created_at DESC, id DESC)",
            "CREATE INDEX tasks_due_order ON tasks(store_id, due_at, created_at DESC, id DESC)",
            "CREATE INDEX tasks_updated_order ON tasks(store_id, updated_at, created_at DESC, id DESC)",
            "CREATE INDEX task_tags_by_value ON task_tags(store_id, tag, task_id)",
            "CREATE INDEX task_commits_expiry ON task_request_commits(store_id, result_body_expires_at) WHERE result_body IS NOT NULL",
        )
        for statement in ddl:
            db.execute(statement)
        db.execute(
            "INSERT INTO schema_meta(singleton, schema_version, store_id, created_at) VALUES(1, ?, ?, ?)",
            (SCHEMA_VERSION, store_id, _now()),
        )

    @staticmethod
    def _task_from_row(db: sqlite3.Connection, row: sqlite3.Row) -> Task:
        tags = db.execute(
            "SELECT tag FROM task_tags WHERE store_id=? AND task_id=? ORDER BY position",
            (row["store_id"], row["id"]),
        ).fetchall()
        return Task(
            store_id=row["store_id"],
            id=row["id"],
            title=row["title"],
            notes=row["notes"],
            status=row["status"],
            priority=row["priority"],
            project_ref=row["project_ref"],
            due_at=row["due_at"],
            estimate_minutes=row["estimate_minutes"],
            tags=tuple(tag["tag"] for tag in tags),
            source_ref=row["source_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            deleted_at=row["deleted_at"],
            archived_at=row["archived_at"] if "archived_at" in row.keys() else None,
            version=row["version"],
        )

    def get_task(self, store_id: str, task_id: str, *, include_deleted: bool = False) -> Task | None:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise TaskValidationError("task_id 必须是 tsk_ 加 32 位小写 UUID hex")
        if type(include_deleted) is not bool:
            raise TaskValidationError("include_deleted 必须是布尔值")
        with self._read_db(store_id) as db:
            if db is None:
                return None
            sql = "SELECT * FROM tasks WHERE store_id=? AND id=?"
            params: list[Any] = [store_id, task_id]
            if not include_deleted:
                sql += " AND status != 'deleted'"
            row = db.execute(sql, params).fetchone()
            return self._task_from_row(db, row) if row else None

    def list_tasks(
        self,
        store_id: str,
        filters: TaskFilters,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TaskPage:
        if not isinstance(filters, TaskFilters):
            raise TaskValidationError("filters 必须是 TaskFilters")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise TaskValidationError("limit 必须是 1 到 200 的整数")
        _validate_store_id(store_id)
        cursor_key = _decode_cursor(cursor, store_id, filters) if cursor is not None else None
        with self._read_db(store_id) as db:
            if db is None:
                return TaskPage((), None)
            where: list[str] = ["t.store_id=?"]
            params: list[Any] = [store_id]
            statuses = filters.resolved_statuses()
            where.append("t.status IN (" + ",".join("?" for _ in statuses) + ")")
            params.extend(statuses)
            if not filters.include_archived and db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                where.append("t.archived_at IS NULL")
            if filters.project_ref_specified:
                if filters.project_ref is None:
                    where.append("t.project_ref IS NULL")
                else:
                    where.append("t.project_ref=?")
                    params.append(filters.project_ref)
            if filters.priority is not None:
                where.append("t.priority=?")
                params.append(filters.priority)
            if filters.due_before is not None:
                where.append("t.due_at IS NOT NULL AND t.due_at<=?")
                params.append(filters.due_before)
            if filters.due_after is not None:
                where.append("t.due_at IS NOT NULL AND t.due_at>=?")
                params.append(filters.due_after)
            if filters.updated_after is not None:
                where.append("t.updated_at>?")
                params.append(filters.updated_after)
            if filters.tag is not None:
                where.append(
                    "EXISTS (SELECT 1 FROM task_tags AS tag_filter "
                    "WHERE tag_filter.store_id=t.store_id AND tag_filter.task_id=t.id AND tag_filter.tag=?)"
                )
                params.append(filters.tag)
            if cursor_key is not None:
                where.append("(t.created_at<? OR (t.created_at=? AND t.id<?))")
                params.extend((cursor_key[0], cursor_key[0], cursor_key[1]))
            sql = "SELECT t.* FROM tasks AS t WHERE " + " AND ".join(where)
            sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT ?"
            params.append(limit + 1)
            rows = db.execute(sql, params).fetchall()
            has_more = len(rows) > limit
            visible = rows[:limit]
            tasks = tuple(self._task_from_row(db, row) for row in visible)
            next_cursor = _encode_cursor(store_id, filters, tasks[-1]) if has_more and tasks else None
            return TaskPage(tasks, next_cursor)

    def commit_create(self, task: Task, identity: CommitIdentity) -> CommitOutcome:
        identity.validate(self.plugin_id)
        if identity.operation_name != "task.create":
            raise TaskValidationError("commit_create 只能提交 task.create")
        if task.store_id != identity.store_id:
            raise TaskStoreIdentityError("Task 与提交身份的 store_id 不一致")
        _validate_task(task)
        now = task.created_at
        outcome: CommitOutcome | None = None
        with self._write_db(identity.store_id) as db:
            old = _find_proof(db, identity.plugin_id, identity.store_id, identity.request_id)
            if old is not None:
                outcome = _existing_outcome(old, identity, now=now)
            else:
                self._purge_expired(db, identity.store_id, now)
                event_intents = (_event("task.created", task.id),)
                data = _result_data(task, True)
                self._insert_task(db, task)
                _insert_proof(
                    db,
                    identity,
                    task,
                    changed=True,
                    result_data=data,
                    event_intents=event_intents,
                    committed_at=now,
                )
                proof_row = _find_proof(db, identity.plugin_id, identity.store_id, identity.request_id)
                assert proof_row is not None
                proof = _proof_from_row(proof_row)
                outcome = CommitOutcome(proof.result_data(now=now), proof.event_intents, proof)
        assert outcome is not None
        return outcome

    def commit_mutation(
        self,
        task_id: str,
        expected_version: int,
        identity: CommitIdentity,
        *,
        changes: Mapping[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> CommitOutcome:
        identity.validate(self.plugin_id)
        if identity.operation_name not in _WRITE_OPERATIONS - {"task.create"}:
            raise TaskValidationError("commit_mutation 只能提交 update 或状态操作")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise TaskValidationError("task_id 必须是 tsk_ 加 32 位小写 UUID hex")
        if type(expected_version) is not int or expected_version < 1:
            raise TaskValidationError("expected_version 必须是正整数")
        if identity.operation_name == "task.update":
            if changes is None:
                raise TaskValidationError("task.update 缺少 changes 对象")
        elif changes is not None:
            raise TaskValidationError("状态操作不接受 changes")
        commit_time = utc_timestamp(now or datetime.now(timezone.utc))
        if not self.path.exists():
            raise TaskNotFound("找不到指定 Task 数据库")
        outcome: CommitOutcome | None = None
        with self._write_db(identity.store_id) as db:
            old = _find_proof(db, identity.plugin_id, identity.store_id, identity.request_id)
            if old is not None:
                outcome = _existing_outcome(old, identity, now=commit_time)
            else:
                row = db.execute(
                    "SELECT * FROM tasks WHERE store_id=? AND id=?",
                    (identity.store_id, task_id),
                ).fetchone()
                if row is None:
                    raise TaskNotFound("找不到指定 Task")
                current = self._task_from_row(db, row)
                # Version conflicts precede no-op and state-transition checks.
                if current.version != expected_version:
                    raise TaskVersionConflict(expected_version, current.version)
                if identity.operation_name == "task.update":
                    mutation = apply_update(current, changes or {}, now=commit_time)
                else:
                    mutation = apply_transition(current, identity.operation_name, now=commit_time)
                self._purge_expired(db, identity.store_id, commit_time)
                if mutation.changed:
                    self._update_task(db, mutation.task, expected_version=expected_version)
                data = _result_data(mutation.task, mutation.changed)
                events = mutation.event_intents if mutation.changed else ()
                _insert_proof(
                    db,
                    identity,
                    mutation.task,
                    changed=mutation.changed,
                    result_data=data,
                    event_intents=events,
                    committed_at=commit_time,
                )
                proof_row = _find_proof(db, identity.plugin_id, identity.store_id, identity.request_id)
                assert proof_row is not None
                proof = _proof_from_row(proof_row)
                outcome = CommitOutcome(proof.result_data(now=commit_time), proof.event_intents, proof)
        assert outcome is not None
        return outcome

    def lookup_commit(self, plugin_id: str, store_id: str, request_id: str) -> TaskCommitProof | None:
        if not isinstance(plugin_id, str) or not _PLUGIN_ID.fullmatch(plugin_id):
            raise TaskValidationError("plugin_id 格式无效")
        if plugin_id != self.plugin_id:
            raise TaskStoreIdentityError("提交证明 plugin_id 与当前 Task 插件不匹配")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise TaskValidationError("request_id 格式无效")
        with self._read_db(store_id) as db:
            if db is None:
                return None
            row = _find_proof(db, plugin_id, store_id, request_id)
            return _proof_from_row(row) if row else None

    def mark_events_recorded(self, identity: CommitIdentity) -> str:
        identity.validate(self.plugin_id)
        if not self.path.exists():
            raise TaskNotFound("找不到对应的 Task 提交证明")
        with self._write_db(identity.store_id) as db:
            row = _find_proof(db, identity.plugin_id, identity.store_id, identity.request_id)
            if row is None:
                raise TaskNotFound("找不到对应的 Task 提交证明")
            _assert_identity_matches(_proof_from_row(row), identity)
            state = row["event_recovery_state"]
            if state == "none":
                if row["event_intents"] != "[]":
                    raise TaskDatabaseError("无事件证明包含事件意图")
                return "none"
            if state == "pending":
                db.execute(
                    "UPDATE task_request_commits SET event_recovery_state='ledger_recorded' "
                    "WHERE plugin_id=? AND store_id=? AND request_id=? AND event_recovery_state='pending'",
                    (identity.plugin_id, identity.store_id, identity.request_id),
                )
                return "ledger_recorded"
            if state == "ledger_recorded":
                return state
            raise TaskDatabaseError("提交证明 event_recovery_state 无效")

    def _purge_expired(self, db: sqlite3.Connection, store_id: str, now: str) -> None:
        db.execute(
            "UPDATE task_request_commits SET result_body=NULL "
            "WHERE store_id=? AND result_body IS NOT NULL AND result_body_expires_at<=?",
            (store_id, now),
        )

    def _insert_task(self, db: sqlite3.Connection, task: Task) -> None:
        db.execute(
            """INSERT INTO tasks(
                store_id,id,title,notes,status,priority,project_ref,due_at,estimate_minutes,source_ref,
                created_at,updated_at,completed_at,deleted_at,archived_at,version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.store_id, task.id, task.title, task.notes, task.status, task.priority,
                task.project_ref, task.due_at, task.estimate_minutes, task.source_ref,
                task.created_at, task.updated_at, task.completed_at, task.deleted_at, task.archived_at, task.version,
            ),
        )
        db.executemany(
            "INSERT INTO task_tags(store_id,task_id,tag,position) VALUES(?,?,?,?)",
            [(task.store_id, task.id, tag, position) for position, tag in enumerate(task.tags)],
        )

    def _update_task(self, db: sqlite3.Connection, task: Task, *, expected_version: int) -> None:
        cursor = db.execute(
            """UPDATE tasks SET title=?,notes=?,status=?,priority=?,project_ref=?,due_at=?,estimate_minutes=?,
                source_ref=?,updated_at=?,completed_at=?,deleted_at=?,archived_at=?,version=?
                WHERE store_id=? AND id=? AND version=?""",
            (
                task.title, task.notes, task.status, task.priority, task.project_ref, task.due_at,
                task.estimate_minutes, task.source_ref, task.updated_at, task.completed_at,
                task.deleted_at, task.archived_at, task.version, task.store_id, task.id, expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise TaskVersionConflict(expected_version, task.version - 1)
        db.execute("DELETE FROM task_tags WHERE store_id=? AND task_id=?", (task.store_id, task.id))
        db.executemany(
            "INSERT INTO task_tags(store_id,task_id,tag,position) VALUES(?,?,?,?)",
            [(task.store_id, task.id, tag, position) for position, tag in enumerate(task.tags)],
        )


def _validate_store_id(store_id: str) -> None:
    if not isinstance(store_id, str) or not _STORE_ID.fullmatch(store_id):
        raise TaskValidationError("store_id 格式无效")


def _validate_task(task: Task) -> None:
    if not isinstance(task, Task) or not _TASK_ID.fullmatch(task.id):
        raise TaskValidationError("Task 记录或 id 格式无效")
    if not _STORE_ID.fullmatch(task.store_id):
        raise TaskValidationError("Task store_id 格式无效")
    if task.status != "open" or task.version != 1 or task.completed_at is not None or task.deleted_at is not None or task.archived_at is not None:
        raise TaskValidationError("新建 Task 必须处于 open 状态且 version=1")
    expected = task_from_fields(
        task.store_id,
        {
            "title": task.title,
            "notes": task.notes,
            "priority": task.priority,
            "project_ref": task.project_ref,
            "due_at": task.due_at,
            "estimate_minutes": task.estimate_minutes,
            "tags": list(task.tags),
            "source_ref": task.source_ref,
        },
        now=task.created_at,
        task_id=task.id,
    )
    if expected != task:
        raise TaskValidationError("新建 Task 字段必须已经通过 Domain 规范化")


def _event(event_type: str, task_id: str) -> dict[str, Any]:
    if event_type not in _EVENT_TYPES:
        raise TaskValidationError("事件类型未登记")
    return {"type": event_type, "resource_refs": {"task_id": task_id}}


def _result_data(task: Task, changed: bool) -> dict[str, Any]:
    return {
        "task": task.to_dict(),
        "changed": changed,
        "operation_status": "committed",
        "result_state": "available",
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity_columns(identity: CommitIdentity) -> tuple[Any, ...]:
    return (
        identity.plugin_id,
        identity.store_id,
        identity.request_id,
        identity.request_fingerprint,
        identity.fingerprint_scheme,
        identity.operation_name,
        identity.core_project_ref,
        identity.provider_version,
        identity.provider_digest,
    )


def _insert_proof(
    db: sqlite3.Connection,
    identity: CommitIdentity,
    task: Task,
    *,
    changed: bool,
    result_data: dict[str, Any],
    event_intents: tuple[dict[str, Any], ...],
    committed_at: str,
) -> None:
    if not changed and event_intents:
        raise TaskDatabaseError("changed=false 时禁止创建事件意图")
    if any(set(event) != {"type", "resource_refs"} or set(event.get("resource_refs", {})) != {"task_id"}
           for event in event_intents):
        raise TaskDatabaseError("事件意图只能引用 task_id")
    if any(event.get("type") not in _EVENT_TYPES or event["resource_refs"].get("task_id") != task.id
           for event in event_intents):
        raise TaskDatabaseError("事件意图与提交 Task 不匹配")
    expires = _expiry(committed_at)
    state = "pending" if event_intents else "none"
    db.execute(
        """INSERT INTO task_request_commits(
            plugin_id,store_id,request_id,request_fingerprint,fingerprint_scheme,operation_name,
            core_project_ref,provider_version,provider_digest,task_id,operation_status,changed,
            error_code,result_body,committed_at,result_body_expires_at,event_intents,event_recovery_state
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            *_identity_columns(identity),
            task.id,
            "committed",
            int(changed),
            None,
            _canonical_json(result_data),
            committed_at,
            expires,
            _canonical_json(list(event_intents)),
            state,
        ),
    )


def _expiry(committed_at: str) -> str:
    source = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
    return utc_timestamp(source + timedelta(days=RESULT_RETENTION_DAYS))


def _find_proof(db: sqlite3.Connection, plugin_id: str, store_id: str, request_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM task_request_commits WHERE plugin_id=? AND store_id=? AND request_id=?",
        (plugin_id, store_id, request_id),
    ).fetchone()


def _proof_from_row(row: sqlite3.Row) -> TaskCommitProof:
    try:
        raw_events = json.loads(row["event_intents"])
    except (TypeError, ValueError):
        raise TaskDatabaseError("提交证明事件意图无法解析") from None
    if not isinstance(raw_events, list):
        raise TaskDatabaseError("提交证明事件意图结构无效")
    events: list[dict[str, Any]] = []
    for event in raw_events:
        if (not isinstance(event, dict) or set(event) != {"type", "resource_refs"}
                or event.get("type") not in _EVENT_TYPES
                or not isinstance(event.get("resource_refs"), dict)
                or set(event["resource_refs"]) != {"task_id"}
                or event["resource_refs"].get("task_id") != row["task_id"]):
            raise TaskDatabaseError("提交证明包含不安全事件意图")
        events.append(event)
    if row["operation_status"] != "committed" or row["event_recovery_state"] not in _EVENT_STATES:
        raise TaskDatabaseError("提交证明终态或事件恢复状态无效")
    if row["event_recovery_state"] == "none" and events:
        raise TaskDatabaseError("无事件提交证明仍包含事件意图")
    if row["event_recovery_state"] != "none" and not events:
        raise TaskDatabaseError("事件提交证明缺少事件意图")
    return TaskCommitProof(
        plugin_id=row["plugin_id"],
        store_id=row["store_id"],
        request_id=row["request_id"],
        request_fingerprint=row["request_fingerprint"],
        fingerprint_scheme=row["fingerprint_scheme"],
        operation_name=row["operation_name"],
        core_project_ref=row["core_project_ref"],
        provider_version=row["provider_version"],
        provider_digest=row["provider_digest"],
        task_id=row["task_id"],
        operation_status=row["operation_status"],
        changed=bool(row["changed"]),
        error_code=row["error_code"],
        result_body=row["result_body"],
        committed_at=row["committed_at"],
        result_body_expires_at=row["result_body_expires_at"],
        event_intents=tuple(events),
        event_recovery_state=row["event_recovery_state"],
    )


def _assert_identity_matches(proof: TaskCommitProof, identity: CommitIdentity) -> None:
    observed = (
        proof.plugin_id, proof.store_id, proof.request_id, proof.request_fingerprint,
        proof.fingerprint_scheme, proof.operation_name, proof.core_project_ref,
        proof.provider_version, proof.provider_digest,
    )
    if observed != _identity_columns(identity):
        raise TaskRequestConflict("相同 request 身份已绑定到不同内容、意图或 provider")


def _existing_outcome(row: sqlite3.Row, identity: CommitIdentity, *, now: str) -> CommitOutcome:
    proof = _proof_from_row(row)
    _assert_identity_matches(proof, identity)
    return CommitOutcome(proof.result_data(now=now), proof.event_intents, proof)


def _filter_fingerprint(filters: TaskFilters) -> dict[str, Any]:
    return filters.cursor_payload()


def _encode_cursor(store_id: str, filters: TaskFilters, task: Task) -> str:
    payload = {
        "version": 1,
        "store_id": store_id,
        "filters": _filter_fingerprint(filters),
        "created_at": task.created_at,
        "task_id": task.id,
    }
    encoded = _canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, store_id: str, filters: TaskFilters) -> tuple[str, str]:
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
        raise TaskValidationError("cursor 编码格式无效")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        raise TaskValidationError("cursor 无法解析") from None
    if not isinstance(payload, dict) or set(payload) != {"version", "store_id", "filters", "created_at", "task_id"}:
        raise TaskValidationError("cursor 字段无效")
    if _canonical_json(payload).encode("utf-8") != raw:
        raise TaskValidationError("cursor 不是规范编码")
    if payload["version"] != 1 or type(payload["version"]) is not int:
        raise TaskValidationError("cursor 版本不受支持")
    if payload["store_id"] != store_id or payload["filters"] != _filter_fingerprint(filters):
        raise TaskValidationError("cursor 与当前 store 或过滤条件不匹配")
    created_at = payload["created_at"]
    task_id = payload["task_id"]
    if not isinstance(created_at, str) or len(created_at) != 27 or utc_timestamp(created_at) != created_at:
        raise TaskValidationError("cursor 排序时间无效")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise TaskValidationError("cursor Task ID 无效")
    return created_at, task_id


__all__ = [
    "CommitIdentity",
    "CommitOutcome",
    "RESULT_RETENTION_DAYS",
    "SCHEMA_VERSION",
    "TaskCommitProof",
    "TaskDatabaseError",
    "TaskPage",
    "TaskStore",
]


def _now() -> str:
    return utc_timestamp(datetime.now(timezone.utc))

"""Core json-stdio adapter for the YushuOS Task domain plugin."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

from yushuos_sdk import PluginContext, Request, Result, StateStore, __version__ as CORE_VERSION
from yushuos_sdk.canonical import fingerprint_for_scheme

from .contracts import (
    CAPABILITIES,
    FINGERPRINT_SCHEME,
    OPERATION_SUPPORT,
    PLUGIN_ID,
    PLUGIN_VERSION,
    TASK_EVENTS,
    validate_result_data,
)
from .domain import (
    TaskError,
    TaskFilters,
    TaskNotFound,
    TaskValidationError,
    task_from_fields,
    validate_changes,
    validate_request_target,
)
from .storage import CommitIdentity, TaskStorageError, TaskStore


_WRITE_CAPABILITIES = frozenset({
    "task.create", "task.update", "task.complete", "task.reopen", "task.delete", "task.cancel", "task.archive",
})
_CORE_VERSION = re.compile(r"^0\.3\.(?P<patch>[0-9]+)(?:\+[0-9A-Za-z.-]+)?$")
_SAFE_CONTEXT_KEYS = (
    "schema_version", "plugin_id", "plugin_version", "provider_digest", "project_ref", "request_id",
    "emitted_events", "run_id", "root_event_id", "causation_id", "depth", "intent",
    "operation_support", "fingerprint_scheme",
)


def core_version_supported(version: str) -> bool:
    """Accept stable Core 0.3.1+ versions while keeping the <0.4 ceiling."""
    if not isinstance(version, str):
        return False
    match = _CORE_VERSION.fullmatch(version)
    return bool(match and int(match.group("patch")) >= 1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _task_store(context: PluginContext) -> TaskStore:
    if not context.data_path:
        raise TaskValidationError("Core 未绑定 Task 私有数据目录")
    return TaskStore(Path(context.data_path) / "task.sqlite3", plugin_id=PLUGIN_ID)


def _operation_identity(request: Request, context: PluginContext, store_id: str) -> CommitIdentity:
    return CommitIdentity(
        plugin_id=context.plugin_id,
        store_id=store_id,
        request_id=request.request_id,
        request_fingerprint=fingerprint_for_scheme(request, context.fingerprint_scheme),
        fingerprint_scheme=context.fingerprint_scheme,
        operation_name=request.capability,
        core_project_ref=context.project_ref,
        provider_version=context.plugin_version,
        provider_digest=context.provider_digest,
    )


def _events_for(proof: Any) -> list[dict[str, Any]]:
    return [dict(event) for event in proof.event_intents]


def _result_from_proof(proof: Any, request: Request) -> Result:
    return Result(
        "succeeded",
        request.request_id,
        "Task 操作已提交",
        resource={"task_id": proof.task_id},
        data=proof.result_data(),
    )


def _proof_matches(proof: Any, identity: CommitIdentity) -> bool:
    return all((
        proof.plugin_id == identity.plugin_id,
        proof.store_id == identity.store_id,
        proof.request_id == identity.request_id,
        proof.request_fingerprint == identity.request_fingerprint,
        proof.fingerprint_scheme == identity.fingerprint_scheme,
        proof.operation_name == identity.operation_name,
        proof.core_project_ref == identity.core_project_ref,
        proof.provider_version == identity.provider_version,
        proof.provider_digest == identity.provider_digest,
        proof.operation_status == "committed",
        proof.error_code is None,
    ))


def _task_id_from(request: Request) -> str | None:
    value = request.fields.get("task_id")
    return value if isinstance(value, str) else None


def _validate_envelope(envelope: Mapping[str, Any]) -> tuple[str, Request, PluginContext]:
    if not core_version_supported(CORE_VERSION):
        raise ValueError("YushuOS Core 版本必须满足 >=0.3.1,<0.4")
    if envelope.get("protocol") != "json-stdio-v2":
        raise ValueError("协议无效")
    action = envelope.get("action", "invoke")
    if action not in {"invoke", "replay", "recover"}:
        raise ValueError("action 无效")
    request_value = envelope.get("request")
    if not isinstance(request_value, Mapping):
        raise ValueError("request 必须是对象")
    request = Request(
        request_id=request_value["request_id"],
        capability=request_value["capability"],
        intent=request_value["intent"],
        fields=request_value.get("fields", {}),
        target=request_value.get("target", {}),
        project_ref=request_value.get("project_ref", ""),
    )
    context = PluginContext.from_envelope(envelope)
    if (envelope.get("plugin_id") != PLUGIN_ID or envelope.get("plugin_version") != PLUGIN_VERSION
            or request.capability not in CAPABILITIES
            or envelope.get("capability") != request.capability
            or envelope.get("capability_effect") != CAPABILITIES[request.capability]["effect"]):
        raise ValueError("插件身份或能力不匹配")
    if request.intent not in CAPABILITIES[request.capability]["intents"]:
        raise ValueError("Task capability 不支持此 intent")
    if envelope.get("mode") not in {"preview", "execute"} or envelope.get("host_mode") not in {
        "readonly", "ask", "plan", "quick", "execute",
    }:
        raise ValueError("执行模式无效")
    if context.operation_support != OPERATION_SUPPORT:
        raise ValueError("Core operation profile 不匹配")
    return action, request, context


def _validate_scope(request: Request, context: PluginContext) -> tuple[str, str | None]:
    fields = request.fields
    if request.capability == "task.get":
        if set(fields) - {"task_id", "include_deleted"} or "task_id" not in fields:
            raise TaskValidationError("get fields 与 Task Contract 不匹配")
        if "include_deleted" in fields and type(fields["include_deleted"]) is not bool:
            raise TaskValidationError("include_deleted 必须是布尔值")
    elif request.capability == "task.update":
        if set(fields) != {"task_id", "expected_version", "changes"}:
            raise TaskValidationError("update fields 与 Task Contract 不匹配")
    elif request.capability in {"task.complete", "task.reopen", "task.delete", "task.cancel", "task.archive"}:
        if set(fields) != {"task_id", "expected_version"}:
            raise TaskValidationError("transition fields 与 Task Contract 不匹配")
    store_id, task_id = validate_request_target(request.capability, request.fields, request.target)
    resources = context.resources
    if not isinstance(resources, Mapping) or resources.get("task_store") != store_id:
        raise TaskValidationError("target.store_id 与 Core 资源绑定不一致")
    return store_id, task_id


def _preview(request: Request, store_id: str, task_id: str | None) -> Result:
    fields = dict(request.fields)
    if request.capability == "task.create":
        planned = validate_changes(fields)
        if "title" not in planned:
            raise TaskValidationError("create 请求缺少 title")
    elif request.capability == "task.update":
        planned = validate_changes(fields["changes"])
    else:
        planned = {}
    return Result(
        "preview",
        request.request_id,
        "Task 写入预览；尚未创建或修改 Task",
        resource={"task_id": task_id} if task_id else {},
        data={
            "operation_status": "preview",
            "result_state": "planned",
            "changed": False,
            "planned": planned,
        },
    )


def _read(request: Request, context: PluginContext, store_id: str, task_id: str | None) -> Result:
    store = _task_store(context)
    if request.capability == "task.get":
        task = store.get_task(
            store_id,
            task_id or "",
            include_deleted=request.fields.get("include_deleted", False),
        )
        if task is None:
            raise TaskNotFound("Task 不存在")
        data = {"task": task.to_dict()}
        validate_result_data(request.capability, data)
        return Result("succeeded", request.request_id, "已读取 Task", {"task_id": task.id}, data)

    filters = TaskFilters.from_fields({
        key: value for key, value in request.fields.items() if key not in {"limit", "cursor"}
    })
    page = store.list_tasks(
        store_id,
        filters,
        limit=request.fields.get("limit", 50),
        cursor=request.fields.get("cursor"),
    )
    data = page.to_dict()
    validate_result_data(request.capability, data)
    return Result("succeeded", request.request_id, "已读取 Task 列表", data=data)


def _commit(request: Request, context: PluginContext, store_id: str, task_id: str | None,
            state: StateStore) -> Result:
    if context.fingerprint_scheme != FINGERPRINT_SCHEME:
        raise TaskValidationError("Task 写入需要 Core 绑定的 jcs-operation-v1 指纹")
    if set(TASK_EVENTS) - set(context.emitted_events):
        raise TaskValidationError("Core 上下文没有声明 Task 事件")
    if not context.state_ledger_path or not Path(context.state_ledger_path).is_file():
        raise TaskValidationError("Core 共享操作台账不可用")
    _assert_core_binding(request, context, state)

    fields = request.fields
    # Reject deterministic domain errors before claiming a request ID.
    if request.capability == "task.create":
        normalized = validate_changes(fields)
        if "title" not in normalized:
            raise TaskValidationError("create 请求缺少 title")
    else:
        if type(fields.get("expected_version")) is not int or fields["expected_version"] < 1:
            raise TaskValidationError("expected_version 必须是正整数")
        if request.capability == "task.update":
            validate_changes(fields["changes"])

    identity = _operation_identity(request, context, store_id)
    store = _task_store(context)
    if not state.claim(request):
        return Result("unknown", request.request_id, "此 request_id 已被 claim；必须由 Core 显式恢复")

    try:
        now = _now()
        if request.capability == "task.create":
            task = task_from_fields(store_id, fields, now=now)
            outcome = store.commit_create(task, identity)
        else:
            changes = fields["changes"] if request.capability == "task.update" else None
            outcome = store.commit_mutation(
                task_id or "",
                fields["expected_version"],
                identity,
                changes=changes,
                now=now,
            )
    except TaskError as exc:
        failed = Result("failed", request.request_id, "Task 请求未能提交", error={"code": exc.code})
        try:
            state.record_with_events(failed, context, [])
        except Exception:
            return Result("unknown", request.request_id, "Task 失败收据暂未写入；需要按原请求核验")
        return failed
    except TaskStorageError:
        return Result("unknown", request.request_id, "Task 数据库结果需要通过原请求恢复核验")

    result = Result(
        "succeeded",
        request.request_id,
        "Task 操作已提交",
        resource={"task_id": outcome.proof.task_id},
        data=outcome.data,
    )
    try:
        validate_result_data(request.capability, result.data)
    except ValueError:
        return Result("unknown", request.request_id, "Task 已提交但结果快照未通过契约校验；需要恢复核验")
    # The Task proof is committed before this single Core ledger transaction.
    # If the process dies here, Core resume will find the proof and confirm it.
    try:
        state.record_with_events(result, context, _events_for(outcome.proof))
    except Exception:
        return Result("unknown", request.request_id, "Task 已提交；Core 收据与事件仍需显式恢复确认")
    try:
        store.mark_events_recorded(identity)
    except Exception:
        # Core already owns a terminal receipt and durable outbox; leave this
        # private bookkeeping marker pending without changing business success.
        pass
    return result


def _assert_core_binding(request: Request, context: PluginContext, state: StateStore) -> None:
    bound = state.operation_context(request.request_id)
    current = context.to_dict()
    expected = {key: current[key] for key in _SAFE_CONTEXT_KEYS}
    if bound is None or bound != expected:
        raise TaskValidationError("Task 操作上下文与 Core 安全绑定不一致")


def _replay_or_recover(action: str, request: Request, context: PluginContext,
                       store_id: str, task_id: str | None, state: StateStore) -> Result:
    if request.capability not in _WRITE_CAPABILITIES:
        return Result("unavailable", request.request_id, "只支持 Task 写操作的 replay/recover")
    _assert_core_binding(request, context, state)
    identity = _operation_identity(request, context, store_id)
    proof = _task_store(context).lookup_commit(PLUGIN_ID, store_id, request.request_id)
    if proof is None:
        return Result("unknown", request.request_id, "没有 Task commit proof；不会重做业务写入")
    if not _proof_matches(proof, identity) or (task_id is not None and proof.task_id != task_id):
        return Result("unavailable", request.request_id, "Task commit proof 与原请求身份不匹配")

    result = _result_from_proof(proof, request)
    try:
        validate_result_data(request.capability, result.data)
    except ValueError:
        return Result("unknown", request.request_id, "Task proof 结果快照未通过契约校验；仍需核验")
    if action == "recover":
        try:
            state.reconcile_confirmed(request, result, context, _events_for(proof))
        except Exception:
            return Result("unknown", request.request_id, "Core 恢复确认未完成；原请求仍需核验")
        try:
            _task_store(context).mark_events_recorded(identity)
        except Exception:
            pass
    return result


def execute(envelope: Mapping[str, Any]) -> Result:
    """Validate and dispatch one Core envelope without crossing ledger owners."""
    action, request, context = _validate_envelope(envelope)
    store_id, task_id = _validate_scope(request, context)
    mode = envelope.get("mode")
    host_mode = envelope.get("host_mode")

    if request.capability in _WRITE_CAPABILITIES and action == "invoke" and (
        mode != "execute" or host_mode != "execute"
    ):
        return _preview(request, store_id, task_id)
    if action in {"replay", "recover"}:
        if mode != "execute" or host_mode != "execute":
            return Result("preview", request.request_id, "Task 恢复或重放需要 Core 显式执行授权")
        if context.fingerprint_scheme != FINGERPRINT_SCHEME:
            return Result("unavailable", request.request_id, "原 Core fingerprint scheme 不匹配")
        state = StateStore(context.state_ledger_path)
        return _replay_or_recover(action, request, context, store_id, task_id, state)

    if request.capability in {"task.get", "task.list"}:
        return _read(request, context, store_id, task_id)
    if request.capability in _WRITE_CAPABILITIES:
        state = StateStore(context.state_ledger_path)
        return _commit(request, context, store_id, task_id, state)
    return Result("unavailable", request.request_id, "Task capability 不可用")


def main() -> int:
    """Read one JSON envelope from stdin and write one JSON Result to stdout."""
    request_id = "invalid-request"
    try:
        raw = sys.stdin.read()
        envelope = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON 常量无效")))
        if isinstance(envelope, Mapping) and isinstance(envelope.get("request"), Mapping):
            candidate = envelope["request"].get("request_id")
            if isinstance(candidate, str):
                request_id = candidate
        if not isinstance(envelope, Mapping):
            raise ValueError("runner envelope 必须是对象")
        result = execute(envelope)
    except TaskError as exc:
        result = Result("failed", request_id, "Task 请求未能完成", error={"code": exc.code})
    except (KeyError, TypeError, ValueError, OSError):
        result = Result("unavailable", request_id, "Task runner 请求或上下文无效")
    sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


__all__ = ["execute", "main"]

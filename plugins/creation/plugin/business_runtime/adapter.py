"""Core adapter. Private proof precedes Core receipt; recovery never repeats mutations."""
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys

from yushuos_sdk import PluginContext, Request, Result, StateStore
from yushuos_sdk.canonical import fingerprint_for_scheme
from yushuos.manifest import validate_schema

from .contracts import capabilities, events
from .domains import DomainService, timestamp
from .store import Store, DomainError, now

SAFE_KEYS = ("schema_version", "plugin_id", "plugin_version", "provider_digest", "project_ref", "request_id",
             "emitted_events", "run_id", "root_event_id", "causation_id", "depth", "intent",
             "operation_support", "fingerprint_scheme")


def identity(request, context, scope):
    return {"request_id": request.request_id, "plugin_id": context.plugin_id, "store_id": scope,
            "capability": request.capability, "intent": request.intent,
            "fingerprint": fingerprint_for_scheme(request, context.fingerprint_scheme),
            "fingerprint_scheme": context.fingerprint_scheme, "provider_version": context.plugin_version,
            "provider_digest": context.provider_digest, "project_ref": context.project_ref}


def assert_binding(request, context, state):
    value = context.to_dict()
    if state.operation_context(request.request_id) != {k: value[k] for k in SAFE_KEYS}:
        raise DomainError("operation.binding_mismatch")


def result_of(request, proof):
    if timestamp(now()) > timestamp(proof["committed_at"]) + timedelta(days=180):
        return Result("succeeded", request.request_id, "Committed result snapshot expired",
                      data={"operation_status": "committed", "result_state": "expired",
                            "original_request_id": request.request_id, "code": "result_expired"})
    return Result("succeeded", request.request_id, "Local operation committed", data=proof["data"])


def execute(slug, envelope):
    context = PluginContext.from_envelope(envelope)
    raw = envelope["request"]
    request = Request(raw["request_id"], raw["capability"], raw["intent"], raw.get("fields", {}),
                      raw.get("target", {}), raw.get("project_ref", ""))
    cap = capabilities(slug).get(request.capability)
    if (context.plugin_id != "yushuos." + slug or context.plugin_version != "0.1.0" or cap is None
            or envelope.get("capability") != request.capability
            or envelope.get("capability_effect") != cap["effect"]
            or context.operation_support != "local_commit_v1"
            or (cap["effect"] != "read_only" and context.fingerprint_scheme != "jcs-operation-v1")
            or request.intent not in cap["intents"]):
        raise DomainError("operation.invalid_context")
    action = envelope.get("action", "invoke")
    if action not in {"invoke", "recover", "replay"}:
        raise DomainError("operation.invalid_action")
    fields = request.to_dict()["fields"]
    if validate_schema(cap["inputs"], fields):
        raise DomainError("invalid_input")
    scope = raw.get("target", {}).get("store_id")
    if not isinstance(scope, str) or not scope or set(raw.get("target", {})) != {"store_id"}:
        raise DomainError("permission.invalid_scope")
    if context.resources.get(slug + "_store") != scope or not context.data_path:
        raise DomainError("permission.scope_mismatch")
    if context.mode not in {"execute", "preview"} or context.host_mode not in {"readonly", "ask", "plan", "quick", "execute"}:
        raise DomainError("operation.invalid_mode")
    private = Store(Path(context.data_path) / (slug + ".sqlite3"), context.plugin_id)
    service = DomainService(slug, private)
    if cap["effect"] == "read_only":
        if action != "invoke":
            return Result("unavailable", request.request_id, "Read operations cannot recover or replay")
        data, _ = service.operate(request.capability, fields, scope)
        if validate_schema(cap["outputs"], data):
            raise DomainError("result.invalid_contract")
        return Result("succeeded", request.request_id, "Private data read", data=data)
    if context.mode != "execute" or context.host_mode != "execute":
        return Result("preview", request.request_id, "No business mutation performed")
    if set(events(slug)) - set(context.emitted_events):
        raise DomainError("operation.undeclared_events")
    if not Path(context.state_ledger_path).is_file():
        raise DomainError("operation.missing_ledger")
    state = StateStore(context.state_ledger_path)
    assert_binding(request, context, state)
    operation = identity(request, context, scope)
    if action in {"recover", "replay"}:
        proof = private.lookup(scope, request.request_id)
        if proof is None:
            return Result("unknown", request.request_id, "No commit proof; mutation will not be retried")
        if proof["identity"] != operation:
            return Result("unavailable", request.request_id, "Commit identity no longer matches")
        result = result_of(request, proof)
        if result.data.get("result_state") != "expired" and validate_schema(cap["outputs"], result.to_dict()["data"]):
            return Result("unknown", request.request_id, "Proof result does not match contract")
        if action == "recover":
            try:
                state.reconcile_confirmed(request, result, context, proof["events"])
            except Exception:
                return Result("unknown", request.request_id, "Core confirmation still requires verification")
        return result
    if not state.claim(request):
        return Result("unknown", request.request_id, "Already claimed; explicit Core recovery required")
    try:
        def mutate(db):
            data, emitted = service.operate(request.capability, fields, scope, db)
            if validate_schema(cap["outputs"], data):
                raise DomainError("result.invalid_contract")
            if any(item["type"] not in context.emitted_events for item in emitted):
                raise DomainError("operation.undeclared_events")
            return data, emitted
        proof = private.commit(scope, operation, mutate)
    except DomainError as exc:
        failed = Result("failed", request.request_id, "Business operation rejected", error={"code": exc.code})
        try:
            state.record_with_events(failed, context, [])
        except Exception:
            return Result("unknown", request.request_id, "Failure confirmation unavailable")
        return failed
    except (OSError, sqlite3.Error):
        return Result("unknown", request.request_id, "Private commit needs verification")
    result = result_of(request, proof)
    try:
        state.record_with_events(result, context, proof["events"])
    except Exception:
        return Result("unknown", request.request_id, "Private commit exists; Core confirmation incomplete")
    return result


def main(slug):
    request_id = "invalid-request"
    try:
        envelope = json.loads(sys.stdin.read(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        request_id = envelope.get("request", {}).get("request_id", request_id)
        result = execute(slug, envelope)
    except DomainError as exc:
        result = Result("failed", request_id, "Request rejected", error={"code": exc.code})
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error):
        result = Result("unavailable", request_id, "Invalid request or storage context")
    sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False, allow_nan=False) + "\n")
    return 0

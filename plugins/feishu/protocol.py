"""External provider boundary; stdlib receipt persistence is not remote ACID."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from yushuos_sdk import PluginContext, Request, Result


class ProviderError(Exception):
    def __init__(self, code="provider_error", *, retryable=False, ambiguous=False):
        self.code, self.retryable, self.ambiguous = code, retryable, ambiguous
        super().__init__(code)


def result(status, rid, code=None, data=None, retryable=False):
    return Result(
        status,
        rid,
        "",
        resource={},
        data=data,
        error={"code": code, "retryable": retryable, "details": {}} if code else None,
    ).to_dict()


def validate(schema, value):
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
        raise ValueError("invalid_input")


def binding_file(envelope, app):
    configured = envelope.get("plugin_config", {}).get("binding_file")
    if not configured:
        return {}
    path = Path(configured).expanduser()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError("binding_file")
    # Never accept a binding inside any plugin release or this source checkout.
    release = Path(__file__).resolve().parent
    checkout = release.parents[1] if release.parent.name == "plugins" else release
    if path.is_relative_to(release) or path.is_relative_to(checkout):
        raise ValueError("binding_file")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("app") != app:
        raise ValueError("binding_file")
    return value


def sanitize(value, credentials):
    secrets = [v for v in credentials.values() if isinstance(v, str) and v]
    forbidden = (
        "authorization",
        "apikey",
        "token",
        "cookie",
        "secret",
        "password",
        "url_info",
        "headers",
    )
    if isinstance(value, dict):
        return {
            k: sanitize(v, credentials)
            for k, v in value.items()
            if not any(word in k.lower().replace("_", "") for word in forbidden)
        }
    if isinstance(value, list):
        return [sanitize(v, credentials) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
    return value


class Receipts:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute(
                "create table if not exists receipts (identity text primary key, fingerprint text not null, state text not null, result text)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("pragma synchronous=FULL")
        return db

    def claim(self, identity, fingerprint, *, recover=False):
        with self.connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select fingerprint,state,result from receipts where identity=?",
                (identity,),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ProviderError("request_conflict")
                return False, json.loads(row[2]) if row[2] else None
            if recover:
                return False, None
            db.execute(
                "insert into receipts values (?,?,?,NULL)",
                (identity, fingerprint, "claimed"),
            )
            return True, None

    def finish(self, identity, out):
        with self.connect() as db:
            db.execute(
                "update receipts set state=?,result=? where identity=?",
                (
                    "confirmed" if out["status"] == "succeeded" else out["status"],
                    json.dumps(out, ensure_ascii=False, allow_nan=False),
                    identity,
                ),
            )


def handle(
    envelope,
    app,
    capabilities,
    dispatch,
    *,
    binding=None,
    transport=None,
    receipt_path=None,
    preflight=None,
):
    rid = (
        envelope.get("request", {}).get("request_id", "")
        if isinstance(envelope, dict)
        else ""
    )
    attempted = False
    receipts = identity = None
    try:
        context = PluginContext.from_envelope(envelope)
        if context.schema_version != 1 or envelope.get("operation_support") is not None:
            raise ValueError("external_write profile")
        request = Request(**envelope["request"])
        if (
            context.plugin_id != "yushuos." + app
            or context.plugin_version != "0.1.0"
            or envelope.get("capability") != request.capability
            or context.mode != envelope.get("mode")
            or context.host_mode != envelope.get("host_mode")
            or context.mode not in ("preview", "execute")
            or context.host_mode not in ("readonly", "ask", "plan", "quick", "execute")
            or envelope.get("action", "invoke") not in ("invoke", "replay", "recover")
        ):
            raise ValueError("identity")
        spec = capabilities.get(request.capability)
        if not spec:
            return result("unavailable", rid, "unsupported_capability")
        if (
            envelope.get("capability_effect") != spec["effect"]
            or request.intent not in spec["intents"]
        ):
            raise ValueError("effect or intent")
        validate(spec["input"], request.to_dict()["fields"])
        if set(request.target) - set(spec.get("resource_scopes", {})):
            raise ValueError("unknown target scope")
        config = binding if binding is not None else binding_file(envelope, app)
        if not spec.get("implemented", True):
            return result("unavailable", rid, "not_implemented")
        if not config.get("configured"):
            return result("unavailable", rid, "not_configured")
        if request.capability not in config.get("authorized_capabilities", []):
            return result("unavailable", rid, "not_authorized")
        if request.capability not in config.get("verified_capabilities", []):
            return result("unavailable", rid, "not_verified")
        if not set(spec["permissions"]) <= set(config.get("grants", [])):
            return result("unavailable", rid, "permission_denied")
        fields = request.to_dict()["fields"]
        for field, scope in spec.get("resource_scopes", {}).items():
            allowed = context.resources.get(scope)
            allowed = (allowed,) if isinstance(allowed, str) else allowed
            if (
                not isinstance(allowed, (list, tuple))
                or not allowed
                or not all(isinstance(v, str) and v for v in allowed)
            ):
                raise ProviderError("permission_denied")
            if field in fields and fields[field] not in allowed:
                raise ProviderError("permission_denied")
            if field == "account_ref" and config.get("account_ref") not in allowed:
                raise ProviderError("permission_denied")
            if field in request.target:
                target = request.to_dict()["target"][field]
                if target != context.to_dict()["resources"][scope]:
                    raise ProviderError("permission_denied")
        credentials = dict(config.get("credentials", {}))
        if app == "ima":
            credentials = {
                "client_id": os.environ.get("IMA_OPENAPI_CLIENTID")
                or credentials.get("client_id", ""),
                "api_key": os.environ.get("IMA_OPENAPI_APIKEY")
                or credentials.get("api_key", ""),
            }
            if not all(credentials.values()):
                return result("unavailable", rid, "not_configured")
        config = {
            **config,
            "credentials": credentials,
            "bound_resources": context.to_dict()["resources"],
        }
        if preflight is not None:
            preflight(request.capability, fields)
        write = spec["effect"] == "external_write"
        if write:
            if context.mode != "execute" or context.host_mode != "execute":
                return result(
                    "preview",
                    rid,
                    data={
                        "operation_status": "not_submitted",
                        "result_state": "not_submitted",
                    },
                )
            if not context.data_path:
                return result("unavailable", rid, "receipt_path_unbound")
            identity = json.dumps(
                [app, context.project_ref, rid], separators=(",", ":")
            )
            payload = {
                "request": request.to_dict(),
                "provider_digest": context.provider_digest,
                "plugin_version": context.plugin_version,
                "scopes": context.to_dict()["resources"],
                "account_ref": config.get("account_ref", ""),
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    payload, sort_keys=True, ensure_ascii=False, allow_nan=False
                ).encode()
            ).hexdigest()
            receipts = Receipts(
                receipt_path or Path(context.data_path) / "external-receipts.sqlite3"
            )
            claimed, saved = receipts.claim(
                identity,
                fingerprint,
                recover=envelope.get("action") in ("recover", "replay"),
            )
            if not claimed:
                return saved or result("unknown", rid, "outcome_unknown")
            attempted = True
        elif envelope.get("action", "invoke") != "invoke":
            raise ValueError("read action")
        provider_key = hashlib.sha256(
            json.dumps(
                [
                    app,
                    config.get("account_ref", ""),
                    context.project_ref,
                    rid,
                    request.capability,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:32]
        data = sanitize(
            dispatch(request.capability, fields, config, transport, provider_key),
            credentials,
        )
        validate(spec["output"], data)
        out = result("succeeded", rid, data=data)
        if write:
            receipts.finish(identity, out)
        return out
    except ProviderError as exc:
        out = result(
            "unknown" if attempted and exc.ambiguous else "failed",
            rid,
            "outcome_unknown" if attempted and exc.ambiguous else exc.code,
            retryable=exc.retryable and not attempted,
        )
    except (TimeoutError, OSError, sqlite3.Error):
        out = result(
            "unknown" if attempted else "failed",
            rid,
            "outcome_unknown" if attempted else "provider_error",
            retryable=not attempted,
        )
    except (ValueError, TypeError, KeyError):
        out = result(
            "unknown" if attempted else "failed",
            rid,
            "outcome_unknown" if attempted else "invalid_input",
        )
    except Exception:  # noqa: BLE001 -- provider boundary must never expose raw exception text
        out = result(
            "unknown" if attempted else "failed",
            rid,
            "outcome_unknown" if attempted else "provider_error",
        )
    if attempted:
        try:
            receipts.finish(identity, out)
        except (OSError, sqlite3.Error):
            pass
    return out


def main(handler):
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            out = handler(payload)
        except Exception:  # noqa: BLE001 -- untrusted JSONL must yield a safe protocol frame
            out = result("failed", "", "invalid_input")
        print(json.dumps(out, ensure_ascii=False, allow_nan=False), flush=True)

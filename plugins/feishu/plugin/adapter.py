"""Feishu task/calendar/message adapter; lark-cli whitelist only."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

try:
    from . import definition as d
    from . import protocol as p
except ImportError:
    import definition as d
    import protocol as p

APP, PLUGIN_ID = "feishu", "yushuos.feishu"
ProviderError = p.ProviderError
_caps = []
for name in ("create", "get", "list", "update", "complete"):
    fields = {"tasklist_guid": d.S}
    required = ["tasklist_guid"]
    if name in ("get", "update", "complete"):
        fields["task_guid"] = d.S
        required.append("task_guid")
    if name in ("create", "update"):
        fields.update(
            title=d.S,
            description={"type": "string", "maxLength": 20000},
            due_at={"type": "string", "maxLength": 40},
        )
        required.append("title")
    if name == "list":
        fields.update(limit=d.LIMIT, cursor=d.CURSOR, completed={"type": "boolean"})
    _caps.append(
        d.cap(
            APP,
            "task." + name,
            fields,
            required,
            write=name in ("create", "update", "complete"),
            scopes={"tasklist_guid": "tasklist"},
            output=d.PAGE if name == "list" else None,
        )
    )
for name in ("create", "get", "list", "update"):
    fields, required = {"calendar_id": d.S}, ["calendar_id"]
    if name in ("get", "update"):
        fields["event_id"] = d.S
        required.append("event_id")
    if name in ("create", "update", "list"):
        fields.update(
            start_at={"type": "string", "maxLength": 40},
            end_at={"type": "string", "maxLength": 40},
        )
        required += ["start_at", "end_at"]
    if name in ("create", "update"):
        fields.update(
            summary=d.S,
            description={"type": "string", "maxLength": 20000},
            timezone=d.S,
        )
        required += ["summary", "timezone"]
    _caps.append(
        d.cap(
            APP,
            "calendar.event." + name,
            fields,
            required,
            write=name in ("create", "update"),
            scopes={"calendar_id": "calendar"},
            output=d.PAGE if name == "list" else None,
        )
    )
_caps.append(
    d.cap(
        APP,
        "message.send",
        {
            "receive_id": d.S,
            "receive_id_type": {"type": "string", "enum": ["chat_id", "open_id"]},
            "text": d.TEXT,
        },
        ["receive_id", "receive_id_type", "text"],
        write=True,
        scopes={"receive_id": "recipient"},
    )
)
_caps.append(
    d.cap(
        APP,
        "resource.bindings.get",
        {},
        [],
        output=d.obj(
            {
                "bindings": d.obj(
                    {
                        k: {"type": "array", "items": d.S}
                        for k in ("tasklist", "calendar", "recipient")
                    },
                    ("tasklist", "calendar", "recipient"),
                )
            },
            ("bindings",),
        ),
    )
)
CAPABILITIES = {c["name"]: c for c in _caps}
MANIFEST = d.manifest(APP, CAPABILITIES)


def descriptor():
    return d.descriptor(APP, CAPABILITIES)


def cli(command, params, body, write, config):
    args = [
        config.get("executable", "lark-cli"),
        *command,
        "--as",
        "user",
        "--format",
        "json",
    ]
    if params:
        args += ["--params", json.dumps(params, ensure_ascii=False)]
    if body is not None:
        args += ["--data", json.dumps(body, ensure_ascii=False)]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProviderError(ambiguous=write) from None
    try:
        value = json.loads(proc.stdout)
    except (ValueError, TypeError):
        raise ProviderError(ambiguous=write) from None
    if (
        value.get("code") in (429, 99991400)
        or value.get("error", {}).get("type") == "rate_limit"
    ):
        raise ProviderError("rate_limited", retryable=True)
    if proc.returncode == 10:
        raise ProviderError("confirmation_required")
    if (
        proc.returncode
        or not (value.get("code") == 0 or value.get("ok") is True)
        or not isinstance(value.get("data"), dict)
    ):
        raise ProviderError(ambiguous=write)
    return value["data"]


def item(value, key):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get(key), str)
        or not value[key]
    ):
        raise ProviderError(ambiguous=True)
    out = {"id": value[key]}
    if isinstance(value.get("summary"), str):
        out["title"] = value["summary"]
    if isinstance(value.get("description"), str):
        out["content"] = value["description"]
    return out


def dispatch(name, f, config, transport, rid):
    if name == "feishu.resource.bindings.get":
        resources = config["bound_resources"]
        return {
            "bindings": {
                k: [resources[k]]
                if isinstance(resources.get(k), str)
                else list(resources.get(k, []))
                for k in ("tasklist", "calendar", "recipient")
            }
        }
    invoke = transport or (
        lambda command, params, body, write: cli(command, params, body, write, config)
    )
    write = CAPABILITIES[name]["effect"] == "external_write"
    if name.startswith("feishu.task."):
        op = name.rsplit(".", 1)[-1]
        params = {"tasklist_guid": f["tasklist_guid"]}
        if "task_guid" in f:
            params["task_guid"] = f["task_guid"]
        if op in ("get", "update", "complete"):
            existing = invoke(
                ["task", "tasks", "get"], {"task_guid": f["task_guid"]}, None, False
            ).get("task")
            if not isinstance(existing, dict) or existing.get("guid") != f["task_guid"]:
                raise ProviderError()
            if not any(
                isinstance(t, dict) and t.get("tasklist_guid") == f["tasklist_guid"]
                for t in existing.get("tasklists", [])
            ):
                raise ProviderError("permission_denied")
            if op == "get":
                return {"entity": item(existing, "guid")}
        if op == "list":
            params.update(
                page_size=f.get("limit", 50), completed=f.get("completed", False)
            )
            if f.get("cursor"):
                params["page_token"] = f["cursor"]
            page = invoke(["task", "tasklists", "tasks"], params, None, False)
            if (
                not isinstance(page.get("items"), list)
                or type(page.get("has_more")) is not bool
            ):
                raise ProviderError()
            cursor = page.get("page_token") if page["has_more"] else None
            if page["has_more"] and (not cursor or cursor == f.get("cursor")):
                raise ProviderError()
            return {
                "items": [item(v, "guid") for v in page["items"]],
                "next_cursor": cursor,
            }
        body = {"summary": f.get("title"), "description": f.get("description", "")}
        if f.get("due_at"):
            body["due"] = {
                "timestamp": str(
                    int(datetime.fromisoformat(f["due_at"]).timestamp() * 1000)
                ),
                "is_all_day": False,
            }
        if op == "create":
            body.update(
                tasklists=[{"tasklist_guid": f["tasklist_guid"]}], client_token=rid
            )
        if op == "complete":
            body = {"completed_at": str(int(datetime.now(UTC).timestamp() * 1000))}
        if op in ("update", "complete"):
            body = {"task": body, "update_fields": list(body)}
        params = {} if op == "create" else {"task_guid": f["task_guid"]}
        value = invoke(
            ["task", "tasks", "create" if op == "create" else "patch"],
            params,
            body,
            True,
        )
        entity = item(value.get("task"), "guid")
        if op != "create" and entity["id"] != f["task_guid"]:
            raise ProviderError(ambiguous=True)
    elif name.startswith("feishu.calendar."):
        op = name.rsplit(".", 1)[-1]
        params = {"calendar_id": f["calendar_id"]}
        if "event_id" in f:
            params["event_id"] = f["event_id"]
        body = None
        if op in ("create", "update", "list"):
            start, end = (
                datetime.fromisoformat(f["start_at"]),
                datetime.fromisoformat(f["end_at"]),
            )
            if not start.tzinfo or not end.tzinfo or start >= end:
                raise ValueError("time range")
            if op == "list":
                if (end - start).total_seconds() >= 40 * 86400:
                    raise ValueError("time range")
                params.update(
                    start_time=str(int(start.timestamp())),
                    end_time=str(int(end.timestamp())),
                )
            else:
                body = {
                    "summary": f["summary"],
                    "description": f.get("description", ""),
                    "start_time": {
                        "timestamp": str(int(start.timestamp())),
                        "timezone": f["timezone"],
                    },
                    "end_time": {
                        "timestamp": str(int(end.timestamp())),
                        "timezone": f["timezone"],
                    },
                }
        if op == "create":
            params["idempotency_key"] = rid
        value = invoke(
            [
                "calendar",
                "events",
                {"list": "instance_view", "update": "patch"}.get(op, op),
            ],
            params,
            body,
            write,
        )
        if op == "list":
            if f.get("cursor"):
                raise ValueError("instance_view is not cursor paginated")
            if not isinstance(value.get("items"), list):
                raise ProviderError()
            return {
                "items": [item(v, "event_id") for v in value["items"]],
                "next_cursor": None,
            }
        entity = item(value.get("event"), "event_id")
        if not write:
            if entity["id"] != f["event_id"]:
                raise ProviderError()
            return {"entity": entity}
    else:
        value = invoke(
            ["im", "messages", "create"],
            {"receive_id_type": f["receive_id_type"]},
            {
                "receive_id": f["receive_id"],
                "msg_type": "text",
                "content": json.dumps({"text": f["text"]}, ensure_ascii=False),
                "uuid": rid,
            },
            True,
        )
        entity = item(value.get("message", value), "message_id")
    return {
        "provider_id": entity["id"],
        "operation_status": "confirmed",
        "result_state": "available",
    }


def handle_envelope(envelope, *, binding=None, transport=None, receipt_path=None):
    return p.handle(
        envelope,
        APP,
        CAPABILITIES,
        dispatch,
        binding=binding,
        transport=transport,
        receipt_path=receipt_path,
        preflight=preflight,
    )


def preflight(name, fields):
    if fields.get("due_at") and not datetime.fromisoformat(fields["due_at"]).tzinfo:
        raise ValueError("due_at needs timezone")
    if "start_at" in fields:
        start, end = (
            datetime.fromisoformat(fields["start_at"]),
            datetime.fromisoformat(fields["end_at"]),
        )
        if not start.tzinfo or not end.tzinfo or start >= end:
            raise ValueError("time range")
        if name.endswith(".list") and (end - start).total_seconds() >= 40 * 86400:
            raise ValueError("time range")


def main():
    p.main(handle_envelope)


if __name__ == "__main__":
    main()

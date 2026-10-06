"""Portable schema and App Descriptor builder; no local binding values."""

S = {"type": "string", "minLength": 1, "maxLength": 2000}
TEXT = {"type": "string", "minLength": 1, "maxLength": 200000}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
CURSOR = {"type": "string", "maxLength": 2000}


def obj(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


ITEM = obj(
    {"id": S, "title": {"type": "string"}, "content": {"type": "string"}}, ("id",)
)
PAGE = obj(
    {
        "items": {"type": "array", "items": ITEM},
        "next_cursor": {"type": ["string", "null"]},
    },
    ("items", "next_cursor"),
)
ENTITY = obj({"entity": ITEM}, ("entity",))
WRITE = obj(
    {
        "provider_id": S,
        "operation_status": {"type": "string", "enum": ["confirmed", "not_submitted"]},
        "result_state": {"type": "string", "enum": ["available", "not_submitted"]},
    },
    ("operation_status", "result_state"),
)


def cap(
    app,
    name,
    fields,
    required,
    *,
    write=False,
    scopes=None,
    output=None,
    implemented=True,
):
    return {
        "name": app + "." + name,
        "input": obj(fields, required),
        "output": output or (WRITE if write else ENTITY),
        "effect": "external_write" if write else "read_only",
        "intents": ["command"] if write else ["query"],
        "permissions": [app + "." + ("write" if write else "read")],
        "execution_mode": "standalone",
        "resource_scopes": scopes or {},
        "transaction": "external_receipt_v1" if write else "read_only",
        "implemented": implemented,
        "verified": False,
        "authorized": False,
        "errors": [
            "invalid_input",
            "permission_denied",
            "provider_error",
            "rate_limited",
            "request_conflict",
            "outcome_unknown",
        ],
    }


def descriptor(app, capabilities):
    return {
        "schema_version": 1,
        "app": app,
        "version": "0.1.0",
        "capabilities": [
            {
                "id": c["name"],
                "effect": c["effect"],
                "intent": c["intents"][0],
                "input_schema": c["input"],
                "output_schema": c["output"],
                "execution_mode": c["execution_mode"],
                "auth": {"required": True, "scopes": c["permissions"]},
                "resource_bindings": {
                    k: v
                    for k, v in c["resource_scopes"].items()
                    if k in c["input"]["properties"]
                },
            }
            for c in capabilities.values()
        ],
    }


def manifest(app, capabilities):
    return {
        "id": "yushuos." + app,
        "name": app,
        "version": "0.1.0",
        "contract_version": 3,
        "type": "app",
        "enabled": True,
        "dependencies": [],
        "permissions": [],
        "data_path": "data",
        "supported_runtimes": ["python>=3.11"],
        "configuration": obj({"binding_file": S}),
        "error_policy": "fail_closed",
        "audit_policy": "metadata_only",
        "emitted_events": [],
        "runner": {
            "command": ["{python}", "{plugin_root}/run.py"],
            "timeout_seconds": 45,
            "protocol": "json-stdio-v2",
        },
        "capabilities": [
            {
                "name": c["name"],
                "effect": c["effect"],
                "inputs": c["input"],
                "outputs": c["output"],
                "permissions": c["permissions"],
                "intents": c["intents"],
                "resource_scopes": c["resource_scopes"],
                "execution_mode": c["execution_mode"],
                "implemented": c["implemented"],
                "verified": False,
                "authorized": False,
                "enabled": True,
            }
            for c in capabilities.values()
        ],
    }

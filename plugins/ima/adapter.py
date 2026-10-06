"""IMA 1.1.10 note/notebook/kb mapping, no generic document endpoint."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from . import definition as d
    from . import protocol as p
except ImportError:
    import definition as d
    import protocol as p

APP, PLUGIN_ID = "ima", "yushuos.ima"
ProviderError = p.ProviderError
LIMIT = {"type": "integer", "minimum": 1, "maximum": 20}
_caps = [
    d.cap(
        APP,
        "note.create",
        {"folder_id": d.S, "content": d.TEXT},
        ["folder_id", "content"],
        write=True,
        scopes={"folder_id": "notebook"},
    ),
    d.cap(
        APP,
        "note.append",
        {"note_id": d.S, "content": d.TEXT},
        ["note_id", "content"],
        write=True,
        scopes={"note_id": "note"},
    ),
    d.cap(APP, "note.get", {"note_id": d.S}, ["note_id"], scopes={"note_id": "note"}),
    d.cap(
        APP,
        "note.list",
        {"folder_id": d.S, "limit": LIMIT, "cursor": d.CURSOR},
        ["folder_id"],
        scopes={"folder_id": "notebook"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "note.search",
        {
            "query": d.S,
            "start": {"type": "integer", "minimum": 0},
            "limit": LIMIT,
            "cursor": d.CURSOR,
            "search_type": {"type": "integer", "enum": [0, 1]},
        },
        ["query"],
        scopes={"account_ref": "account"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "notebook.list",
        {"limit": LIMIT, "cursor": d.CURSOR},
        [],
        scopes={"account_ref": "account"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "kb.get",
        {"knowledge_base_id": d.S},
        ["knowledge_base_id"],
        scopes={"knowledge_base_id": "kb"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "kb.list",
        {"knowledge_base_id": d.S, "limit": LIMIT, "cursor": d.CURSOR},
        ["knowledge_base_id"],
        scopes={"knowledge_base_id": "kb"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "kb.search",
        {"query": d.S, "limit": LIMIT, "cursor": d.CURSOR},
        ["query"],
        scopes={"account_ref": "account"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "knowledge.search",
        {"knowledge_base_id": d.S, "query": d.S, "cursor": d.CURSOR},
        ["knowledge_base_id", "query"],
        scopes={"knowledge_base_id": "kb"},
        output=d.PAGE,
    ),
    d.cap(
        APP,
        "note.delete",
        {"note_id": d.S},
        ["note_id"],
        write=True,
        scopes={"note_id": "note"},
        implemented=False,
    ),
    d.cap(
        APP,
        "note.overwrite",
        {"note_id": d.S, "content": d.TEXT},
        ["note_id", "content"],
        write=True,
        scopes={"note_id": "note"},
        implemented=False,
    ),
    d.cap(
        APP,
        "kb.associate",
        {"note_id": d.S, "knowledge_base_id": d.S, "title": d.S},
        ["note_id", "knowledge_base_id", "title"],
        write=True,
        scopes={"note_id": "note", "knowledge_base_id": "kb"},
        implemented=False,
    ),
]
CAPABILITIES = {c["name"]: c for c in _caps}
MANIFEST = d.manifest(APP, CAPABILITIES)


def descriptor():
    return d.descriptor(APP, CAPABILITIES)


def http(path, body, headers, write):
    req = Request(
        "https://ima.qq.com/" + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise ProviderError("rate_limited", retryable=True) from None
        raise ProviderError(ambiguous=write and exc.code >= 500) from None
    except (URLError, TimeoutError, ValueError):
        raise ProviderError(ambiguous=write) from None


def normalized_item(value):
    if not isinstance(value, dict):
        raise ProviderError()
    identifier = next(
        (
            value[k]
            for k in (
                "note_id",
                "notebook_id",
                "folder_id",
                "knowledge_base_id",
                "media_id",
                "id",
            )
            if isinstance(value.get(k), str) and value[k]
        ),
        None,
    )
    if not identifier:
        raise ProviderError()
    out = {"id": identifier}
    if isinstance(value.get("title"), str):
        out["title"] = value["title"]
    return out


def dispatch(name, f, config, transport, rid):
    write = CAPABILITIES[name]["effect"] == "external_write"
    body = {"cursor": f.get("cursor", ""), "limit": f.get("limit", 20)}
    key = None
    if name == "ima.note.create":
        path, body = (
            "note/v1/import_doc",
            {"folder_id": f["folder_id"], "content_format": 1, "content": f["content"]},
        )
    elif name == "ima.note.append":
        path, body = (
            "note/v1/append_doc",
            {"note_id": f["note_id"], "content_format": 1, "content": f["content"]},
        )
    elif name == "ima.note.get":
        path, body = (
            "note/v1/get_doc_content",
            {"note_id": f["note_id"], "target_content_format": 0},
        )
    elif name == "ima.note.list":
        path, key = "note/v1/list_note", "note_book_list"
        body["folder_id"] = f["folder_id"]
    elif name == "ima.note.search":
        start = f.get("start", 0)
        if f.get("cursor"):
            if not f["cursor"].isdigit():
                raise ValueError("cursor")
            start = int(f["cursor"])
        content_search = f.get("search_type", 0) == 1
        path, key = "note/v1/search_note", "search_note_infos"
        body = {
            "start": start,
            "end": start + f.get("limit", 20),
            "search_type": int(content_search),
            "query_info": {"content" if content_search else "title": f["query"]},
        }
    elif name == "ima.notebook.list":
        path, key = "note/v1/list_notebook", "note_folder_infos"
        body["cursor"] = f.get("cursor", "0")
    elif name == "ima.kb.get":
        path, key, body = (
            "wiki/v1/get_knowledge_base",
            "infos",
            {"ids": [f["knowledge_base_id"]]},
        )
    elif name == "ima.kb.list":
        path, key = "wiki/v1/get_knowledge_list", "knowledge_list"
        body["knowledge_base_id"] = f["knowledge_base_id"]
    elif name == "ima.kb.search":
        path, key = "wiki/v1/search_knowledge_base", "info_list"
        body["query"] = f["query"]
    elif name == "ima.knowledge.search":
        path, key, body = (
            "wiki/v1/search_knowledge",
            "info_list",
            {
                "query": f["query"],
                "knowledge_base_id": f["knowledge_base_id"],
                "cursor": f.get("cursor", ""),
            },
        )
    else:
        raise ProviderError("unsupported_capability")
    credentials = config["credentials"]
    headers = {
        "Content-Type": "application/json",
        "ima-openapi-clientid": credentials["client_id"],
        "ima-openapi-apikey": credentials["api_key"],
    }
    value = (transport or http)("openapi/" + path, body, headers, write)
    if (
        not isinstance(value, dict)
        or value.get("code") != 0
        or not isinstance(value.get("data"), dict)
    ):
        if isinstance(value, dict) and value.get("code") in (429, 110013):
            raise ProviderError("rate_limited", retryable=True, ambiguous=write)
        raise ProviderError(ambiguous=write)
    data = value["data"]
    if write:
        identifier = data.get("note_id")
        if (
            not isinstance(identifier, str)
            or not identifier
            or name == "ima.note.append"
            and identifier != f["note_id"]
        ):
            raise ProviderError(ambiguous=True)
        return {
            "provider_id": identifier,
            "operation_status": "confirmed",
            "result_state": "available",
        }
    if name == "ima.note.get":
        if not isinstance(data.get("content"), str):
            raise ProviderError()
        return {"entity": {"id": f["note_id"], "content": data["content"]}}
    if not isinstance(data.get(key), list):
        raise ProviderError()
    if name == "ima.kb.get":
        cursor = None
    elif name == "ima.note.search":
        if type(data.get("is_end")) is not bool:
            raise ProviderError()
        cursor = str(body["end"]) if data["is_end"] is False else None
    else:
        if type(data.get("is_end")) is not bool:
            raise ProviderError()
        cursor = data.get("next_cursor") if data["is_end"] is False else None
        if data["is_end"] is False and (not cursor or cursor == f.get("cursor")):
            # No invented cursor for APIs whose continuation is undocumented.
            raise ProviderError("pagination_unverified")
    return {
        "items": [normalized_item(item) for item in data[key]],
        "next_cursor": cursor,
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
    )


def main():
    p.main(handle_envelope)


if __name__ == "__main__":
    main()

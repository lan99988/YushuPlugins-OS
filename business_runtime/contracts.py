"""Authoritative concrete local-domain contract definitions (Core's Schema subset)."""
from copy import deepcopy


def obj(properties=None, required=(), additional=False):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": additional}


def text(minimum=1):
    return {"type": "string", "minLength": minimum, "maxLength": 20000}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": 1000}


def integer(minimum=0, maximum=1000000):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def enum(*values):
    return {"type": "string", "enum": list(values)}


REFERENCE = obj({k: text() for k in ("provider", "kind", "id", "store_id")},
                ("provider", "kind", "id", "store_id"))
REFS = array(REFERENCE)
BOOL = {"type": "boolean"}
TIME = text()
DATE = text()
CONFIDENCE = {"type": "number", "minimum": 0, "maximum": 1}
VERSION = integer(1)
STRING_LIST = array(text())
ID_VERSION = {"id": text(), "expected_version": VERSION}
LIST_FIELDS = {"limit": integer(1, 100), "cursor": {"type": ["string", "null"]}, "include_archived": BOOL}


def entity_schema():
    # All inputs are strict and concrete. Persisted fields include computed state and
    # domain-specific child records; the wrapper remains a stable cross-domain shape.
    return obj({"id": text(), "kind": text(), "version": VERSION, "created_at": TIME,
                "updated_at": TIME, "archived": BOOL, "fields": obj(additional=True)},
               ("id", "kind", "version", "created_at", "updated_at", "archived", "fields"))


ENTITY = entity_schema()
GET = obj({"entity": ENTITY}, ("entity",))
NULL_GET = deepcopy(GET)
NULL_GET["properties"]["entity"]["type"] = ["object", "null"]
PAGE = obj({"items": array(ENTITY), "next_cursor": {"type": ["string", "null"]}}, ("items", "next_cursor"))
WRITE = obj({"entity": ENTITY, "changed": BOOL, "operation_status": enum("committed"),
             "result_state": enum("available")}, ("entity", "changed", "operation_status", "result_state"))

REGISTRY = {}


def add(name, fields, required=(), *, read=False, output=None, host=False):
    slug = name.split(".")[0]
    cap = {"name": name, "effect": "read_only" if read else "internal_write",
           "inputs": obj(fields, required), "outputs": deepcopy(output or (GET if read else WRITE)),
           "dependencies": [], "permissions": [slug + (".read" if read else ".write")],
           "intents": ["query" if read else "command"], "implemented": True,
           "verified": True, "authorized": True, "enabled": True,
           "execution_mode": "host_required" if host else "standalone",
           "description": name, "resource_scopes": {"store_id": slug + "_store"}}
    REGISTRY.setdefault(slug, {})[name] = cap


def crud(kind, fields, required, transitions=(), *, update=True, archive=True):
    add(kind + ".create", fields, required)
    add(kind + ".get", {"id": text()}, ("id",), read=True)
    add(kind + ".list", LIST_FIELDS, read=True, output=PAGE)
    if update:
        add(kind + ".update", {**ID_VERSION, "changes": obj(fields)}, (*ID_VERSION, "changes"))
    if archive:
        transitions = (*transitions, "archive", "restore")
    for action in transitions:
        add(kind + "." + action, ID_VERSION, tuple(ID_VERSION))


crud("capture", {"content": text(), "source": text(), "captured_at": TIME}, ("content", "source"), update=False)
add("capture.route", {**ID_VERSION, "classification": enum("task", "idea", "bug", "knowledge", "ignore")}, (*ID_VERSION, "classification"))
add("capture.mark_processed", {**ID_VERSION, "target_ref": REFERENCE, "ignored_reason": text()}, tuple(ID_VERSION))
crud("idea", {"title": text(), "notes": text(0), "tags": STRING_LIST}, ("title",), ("discard", "delete"))
add("idea.convert", {**ID_VERSION, "target_ref": REFERENCE}, (*ID_VERSION, "target_ref"))
crud("bug", {"title": text(), "description": text(0), "severity": enum("low", "normal", "high", "critical")},
     ("title",), ("resolve", "reopen", "close", "delete"))
add("bug.link_task", {**ID_VERSION, "target_ref": REFERENCE}, (*ID_VERSION, "target_ref"))

TRANSACTION = obj({"amount": text(), "direction": enum("income", "expense"), "category": text(),
                   "at": TIME, "external_id": text()}, ("amount", "direction"))
base_finance = {"month": text(), "currency": text(), "source_id": text()}
add("finance.statement.create", {**base_finance, "transactions": array(TRANSACTION)}, (*base_finance, "transactions"))
for kind in ("finance.statement", "finance.monthly_snapshot"):
    add(kind + ".get", {"id": text()}, ("id",), read=True)
    add(kind + ".list", LIST_FIELDS, read=True, output=PAGE)
add("finance.monthly_snapshot.create", {**base_finance, **{k: text() for k in ("income", "expense", "assets", "liabilities")},
                                        "notes": text(0)}, (*base_finance, "income", "expense", "assets", "liabilities"))
add("finance.monthly_snapshot.close", ID_VERSION, tuple(ID_VERSION))
add("finance.summary", {"month": text(), "currency": text()}, ("month", "currency"), read=True,
    output=obj({"month": text(), "currency": text(), "income": text(), "expense": text(), "statement_ids": STRING_LIST},
               ("month", "currency", "income", "expense", "statement_ids")))

crud("relationship.person", {"name": text(), "relationship": text(), "notes": text(0), "important_facts": STRING_LIST}, ("name",))
add("relationship.interaction.record", {"person_id": text(), "at": TIME, "notes": text(0), "source_ref": REFERENCE}, ("person_id", "at"))
add("relationship.interaction.list", {**LIST_FIELDS, "filters": obj({"person_id": text()})}, read=True, output=PAGE)
add("relationship.recent", {"person_id": text()}, ("person_id",), read=True, output=NULL_GET)
crud("habit", {"name": text(), "timezone": text(), "frequency": enum("daily", "weekly"), "notes": text(0)},
     ("name", "timezone"), ("pause", "resume", "delete"))
for action in ("checkin", "undo_checkin"):
    add("habit." + action, {"habit_id": text(), "date": DATE}, ("habit_id", "date"))
add("habit.history", {"habit_id": text()}, ("habit_id",), read=True, output=PAGE)

add("body.state.record", {"at": TIME, "energy": integer(0, 10), "fatigue": integer(0, 10), "recovery": integer(0, 10),
                          "source": text(), "notes": text(0)}, ("at", "source"))
add("body.state.get", {"id": text()}, ("id",), read=True)
add("body.state.get_latest", {}, read=True, output=NULL_GET)
add("body.state.history", LIST_FIELDS, read=True, output=PAGE)
add("body.sleep.record", {"at": TIME, "minutes": integer(1, 1440), "source": text()}, ("at", "minutes", "source"))
add("body.training.record", {"at": TIME, "minutes": integer(1, 1440), "activity": text(), "source": text()}, ("at", "minutes", "activity", "source"))
add("body.recovery.summary", {}, read=True, output=obj({"summary": obj({k: {"type": ["integer", "null"]} for k in ("energy", "fatigue", "recovery")}),
                                                      "missing": STRING_LIST}, ("summary", "missing")))
crud("lifeadmin", {"title": text(), "due_date": DATE, "category": text(), "task_ref": REFERENCE, "notes": text(0)},
     ("title", "due_date"), ("complete", "delete"))
add("lifeadmin.renew", {**ID_VERSION, "due_date": DATE, "reason": text()}, (*ID_VERSION, "due_date", "reason"))
OPTION = obj({"id": text(), "label": text(), "notes": text(0)}, ("id", "label"))
crud("decision", {"title": text(), "options": array(OPTION, 1), "evidence_refs": REFS, "notes": text(0)}, ("title", "options"), ("delete",))
add("decision.choose", {**ID_VERSION, "option_id": text(), "reason": text()}, (*ID_VERSION, "option_id", "reason"))
add("decision.outcome", {**ID_VERSION, "record_id": text(), "at": TIME, "outcome": text(), "evidence_refs": REFS},
    (*ID_VERSION, "record_id", "at", "outcome"))
JUDGEMENT = {**ID_VERSION, "reason": text(), "judgement": text(), "evidence_refs": REFS}
add("decision.review", JUDGEMENT, (*ID_VERSION, "reason", "judgement", "evidence_refs"), host=True)

TASK_INPUT = obj({"id": text(), "version": VERSION, "priority": enum("urgent", "high", "normal", "low"),
                  "estimate_minutes": integer(1, 10080), "due_at": TIME, "earliest_start": TIME}, ("id", "version", "estimate_minutes"))
WINDOW = obj({"start": TIME, "end": TIME}, ("start", "end"))
PLAN_INPUT = {"tasks": array(TASK_INPUT), "windows": array(WINDOW), "energy_budget_minutes": integer(0, 10080)}
PLANNED = obj({"scheduled": array(obj({"id": text(), "start": TIME, "end": TIME, "task_version": VERSION},
                                    ("id", "start", "end", "task_version"))),
               "unscheduled": array(obj({"id": text(), "reason": text()}, ("id", "reason"))), "input_digest": text()},
              ("scheduled", "unscheduled", "input_digest"))
crud("planner.plan", {"title": text(), **PLAN_INPUT}, ("title", "tasks", "windows"), update=False)
add("planner.generate", PLAN_INPUT, ("tasks", "windows"))
for action in ("preview", "suggest"):
    add("planner." + action, PLAN_INPUT, ("tasks", "windows"), read=True, output=obj({"plan": PLANNED}, ("plan",)))
add("planner.apply", {**ID_VERSION, "input_digest": text()}, (*ID_VERSION, "input_digest"))
add("planner.replan", {**ID_VERSION, "inputs": obj(PLAN_INPUT, ("tasks", "windows"))}, (*ID_VERSION, "inputs"))

add("deepwork.start", {"task_ref": REFERENCE, "plan_ref": REFERENCE, "at": TIME}, ("task_ref", "at"))
add("deepwork.get", {"id": text()}, ("id",), read=True)
add("deepwork.list", LIST_FIELDS, read=True, output=PAGE)
for action in ("pause", "resume", "finish", "cancel", "interrupt"):
    add("deepwork." + action, {**ID_VERSION, "at": TIME, **({"reason": text()} if action == "interrupt" else {})},
        (*ID_VERSION, "at", *(("reason",) if action == "interrupt" else ())))
crud("competition", {"title": text(), "deadline": TIME, "task_refs": REFS, "notes": text(0)}, ("title",), ("delete",))
for action in ("milestone", "progress"):
    add("competition." + action, {**ID_VERSION, "record_id": text(), "at": TIME, "label": text(), "value": integer(0, 100)},
        (*ID_VERSION, "record_id", "at", "label", "value"))
add("competition.review", JUDGEMENT, (*ID_VERSION, "reason", "judgement", "evidence_refs"), host=True)
crud("knowledge", {"title": text(), "content": text(0), "ima_ref": REFERENCE, "tags": STRING_LIST}, ("title",), ("delete",))
for kind, props in {"concept": {"label": text()}, "link": {"target_ref": REFERENCE, "relation": text()},
                    "insight": {"statement": text(), "evidence_refs": REFS}}.items():
    add("knowledge." + kind + ".record", {"knowledge_id": text(), **props}, ("knowledge_id", *props))
add("knowledge.search", {**LIST_FIELDS, "query": text()}, ("query",), read=True, output=PAGE)
crud("creation", {"title": text(), "idea_ref": REFERENCE, "task_refs": REFS, "notes": text(0)}, ("title",), ("delete",))
add("creation.advance_stage", {**ID_VERSION, "stage": enum("draft", "review", "ready")}, (*ID_VERSION, "stage"))
add("creation.publication", {**ID_VERSION, "url": text(), "at": TIME}, (*ID_VERSION, "url", "at"))

METRICS = obj(additional=True)
add("review.generate", {"period_start": DATE, "period_end": DATE, "sources": METRICS,
                        "missing_sources": STRING_LIST, "source_refs": REFS}, ("period_start", "period_end", "sources", "missing_sources"))
add("review.get", {"id": text()}, ("id",), read=True)
add("review.list", LIST_FIELDS, read=True, output=PAGE)
add("review.finalize", ID_VERSION, tuple(ID_VERSION))
add("review.compare", {"id": text(), "other_id": text()}, ("id", "other_id"), read=True,
    output=obj({"left": METRICS, "right": METRICS, "comparable": BOOL}, ("left", "right", "comparable")))
add("review.interpret", JUDGEMENT, (*ID_VERSION, "reason", "judgement", "evidence_refs"), host=True)
crud("personal_model.hypothesis", {"statement": text(), "confidence": CONFIDENCE, "evidence_refs": REFS,
                                   "counterevidence_refs": REFS, "source": text()}, ("statement", "source"), ("withdraw",))
add("personal_model.attach", {**ID_VERSION, "reference": REFERENCE}, (*ID_VERSION, "reference"))
add("personal_model.evaluate", {**JUDGEMENT, "confidence": CONFIDENCE}, (*ID_VERSION, "reason", "judgement", "evidence_refs", "confidence"), host=True)
crud("cognition.pattern", {"statement": text(), "evidence_refs": REFS, "counterevidence_refs": REFS, "source": text()},
     ("statement", "source"), ("withdraw",))
add("cognition.analyze", JUDGEMENT, (*ID_VERSION, "reason", "judgement", "evidence_refs"), host=True)
add("cognition.pattern.review", JUDGEMENT, (*ID_VERSION, "reason", "judgement", "evidence_refs"), host=True)
add("cognition.pattern.changes", LIST_FIELDS, read=True, output=PAGE)


def capabilities(slug):
    return deepcopy(REGISTRY[slug])


def events(slug):
    names = set()
    for cap in REGISTRY[slug].values():
        if cap["effect"] == "read_only":
            continue
        kind, action = cap["name"].rsplit(".", 1)
        if action in {"create", "record"}:
            action = "created"
        names.add(kind + "." + action)
    names.update({"planner": {"planner.plan.created", "planner.plan.apply", "planner.plan.replan"},
                  "review": {"review.created"}, "deepwork": {"deepwork.started"},
                  "finance": {"finance.month.closed"}}.get(slug, set()))
    return sorted(names)


def manifest(slug):
    return {"id": "yushuos." + slug, "name": "YushuOS " + slug, "version": "0.1.0",
            "contract_version": 3, "operation_support": "local_commit_v1", "type": "domain",
            "description": "Independent local-first " + slug + " domain plugin", "enabled": True,
            "dependencies": [], "optional_dependencies": [], "permissions": [], "data_path": "data",
            "supported_runtimes": ["python>=3.11"], "configuration": obj(),
            "error_policy": "fail_closed", "audit_policy": "metadata_only",
            "runner": {"command": ["{python}", "{plugin_root}/run.py"], "timeout_seconds": 30, "protocol": "json-stdio-v2"},
            "capabilities": list(capabilities(slug).values()), "routes": [],
            "skill_names": ["yushuos." + slug], "emitted_events": events(slug), "dependency_versions": {}}

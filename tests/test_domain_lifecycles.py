import pytest
from business_runtime.domains import DomainService, schedule
from business_runtime.store import Store, DomainError
from business_runtime.contracts import REGISTRY
from tests.test_core_domains import installed, invoke

SAMPLES = {
 "capture": ("capture.create", {"content":"remember", "source":"manual"}, "capture"),
 "idea": ("idea.create", {"title":"book"}, "idea"),
 "bug": ("bug.create", {"title":"broken", "severity":"high"}, "bug"),
 "finance": ("finance.statement.create", {"month":"2026-10","currency":"CNY","source_id":"s1","transactions":[]}, "finance.statement"),
 "relationship": ("relationship.person.create", {"name":"test person"}, "relationship.person"),
 "habit": ("habit.create", {"name":"walk","timezone":"Asia/Shanghai"}, "habit"),
 "body": ("body.state.record", {"at":"2026-10-06T00:00:00Z","source":"manual","energy":5}, "body.state"),
 "lifeadmin": ("lifeadmin.create", {"title":"renew","due_date":"2026-10-06"}, "lifeadmin"),
 "decision": ("decision.create", {"title":"choose","options":[{"id":"a","label":"first"}]}, "decision"),
 "planner": ("planner.generate", {"tasks":[],"windows":[]}, "planner.plan"),
 "deepwork": ("deepwork.start", {"at":"2026-10-06T00:00:00Z","task_ref":{"provider":"yushuos.task","kind":"task","id":"t1","store_id":"personal"}}, "deepwork"),
 "competition": ("competition.create", {"title":"contest"}, "competition"),
 "knowledge": ("knowledge.create", {"title":"fact","content":"data"}, "knowledge"),
 "creation": ("creation.create", {"title":"draft"}, "creation"),
 "review": ("review.generate", {"period_start":"2026-10-01","period_end":"2026-10-07","sources":{},"missing_sources":["task"]}, "review"),
 "personal_model": ("personal-model.hypothesis.create", {"statement":"hypothesis","source":"host","evidence_refs":[]}, "personal-model.hypothesis"),
 "cognition": ("cognition.pattern.create", {"statement":"hypothesis","source":"host","evidence_refs":[]}, "cognition.pattern"),
}


@pytest.mark.parametrize("slug", SAMPLES)
def test_independent_core_installed_domain_roundtrip_and_invalid_input(tmp_path, slug):
    runtime, root = installed(tmp_path, slug)
    cap, fields, kind = SAMPLES[slug]
    first = invoke(runtime, cap, fields)
    assert first["status"] == "succeeded", first
    entity = first["data"]["entity"]
    got = invoke(runtime, kind + ".get", {"id":entity["id"]}, "get1")
    assert got["status"] == "succeeded", got
    assert got["data"]["entity"] == entity
    bad = invoke(runtime, cap, {**fields,"unknown_private_field":"unexpected"}, "bad1")
    assert bad["status"] != "succeeded"
    if kind + ".update" in REGISTRY[slug]:
        conflict = invoke(runtime, kind + ".update", {"id":entity["id"],"expected_version":999,"changes":{}}, "conflict1")
        assert conflict["status"] == "failed", conflict
        assert conflict["error"]["code"] == "entity.version_conflict"


def test_planner_priority_hard_constraints_deterministic_and_budget():
    inputs={"windows":[{"start":"2026-10-06T07:00:00+08:00","end":"2026-10-06T08:00:00+08:00"}],
      "tasks":[{"id":"b","version":1,"priority":"normal","estimate_minutes":30},
               {"id":"a","version":1,"priority":"urgent","estimate_minutes":30},
               {"id":"c","version":1,"priority":"high","estimate_minutes":45}],"energy_budget_minutes":60}
    plan=schedule(inputs)
    assert plan==schedule(inputs)
    assert [t["id"] for t in plan["scheduled"]]==["a","b"]
    assert plan["unscheduled"]==[{"id":"c","reason":"energy_budget"}]
    with pytest.raises(DomainError,match="invalid_window"):
        schedule({"tasks":[],"windows":[{"start":"2026-10-06T01:00:00Z","end":"2026-10-06T00:00:00Z"}]})


def test_creation_recorded_publication_and_bug_state_guard(tmp_path):
    s=DomainService("creation",Store(tmp_path/"creation.sqlite","yushuos.creation"))
    def call(cap,fields):
        with s.store.write() as db:return s.operate(cap,fields,"personal",db)[0]
    e=call("creation.create",{"title":"book"})["entity"]
    with pytest.raises(DomainError,match="invalid_transition"):
        call("creation.publication",{"id":e["id"],"expected_version":1,"url":"https://example.org/book","at":"2026-10-06T00:00:00Z"})
    for version,stage in enumerate(("draft","review","ready"),1):
        e=call("creation.advance_stage",{"id":e["id"],"expected_version":version,"stage":stage})["entity"]
    assert call("creation.publication",{"id":e["id"],"expected_version":4,"url":"https://example.org/book","at":"2026-10-06T00:00:00Z"})["entity"]["fields"]["status"]=="published"

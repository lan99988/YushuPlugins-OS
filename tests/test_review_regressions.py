from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import pytest
from business_runtime.domains import DomainService, schedule
from business_runtime.store import Store, DomainError
from tests.test_core_domains import installed, invoke, request


def test_concurrent_same_request_only_commits_once(tmp_path):
    store=Store(tmp_path/"private.sqlite","yushuos.idea")
    identity={"request_id":"same","provider_digest":"a"*64}
    def commit(_):
        return store.commit("personal",identity,lambda db: ({"entity":store.create(db,"idea","personal",{"title":"one"})},[]))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(commit,range(20)))
    assert all(r==results[0] for r in results)
    assert len(store.list("idea","personal"))==1


def test_expired_original_result_is_confirmed_without_body(tmp_path):
    runtime,root=installed(tmp_path)
    q=request("capture.create",{"content":"private","source":"manual"})
    first=runtime.invoke(q,mode="execute",host_mode="execute")
    assert first.status=="succeeded"
    private=next((root/"plugin-data").rglob("capture.sqlite3"))
    with sqlite3.connect(private) as db:
        row=db.execute("select proof from commits").fetchone()
        proof=json.loads(row[0]);proof["committed_at"]="2020-01-01T00:00:00Z"
        db.execute("update commits set proof=?",(json.dumps(proof),))
    replay=runtime.invoke(q,mode="execute",host_mode="execute")
    assert replay.status=="succeeded",replay
    assert replay.data["result_state"]=="expired" and "entity" not in replay.data


def test_first_rejected_mutation_does_not_break_store_read(tmp_path):
    store=Store(tmp_path/"x.sqlite","yushuos.bug")
    def fail(db):raise DomainError("invalid_transition")
    with pytest.raises(DomainError):store.commit("personal",{"request_id":"bad"},fail)
    assert store.list("bug","personal")==[]
    assert store.lookup("personal","bad") is None


def test_cognition_host_evidence_and_events_confirmed(tmp_path):
    runtime,_=installed(tmp_path,"cognition")
    entity=invoke(runtime,"cognition.pattern.create",{"statement":"test","source":"host"})["data"]["entity"]
    ref={"provider":"yushuos.review","kind":"review","id":"r1","store_id":"personal"}
    result=invoke(runtime,"cognition.analyze",{"id":entity["id"],"expected_version":1,"reason":"explicit evidence",
                  "judgement":"tentative pattern","evidence_refs":[ref]},"analyze")
    assert result["status"]=="succeeded",result
    changed=invoke(runtime,"cognition.pattern.changes",{},"changes")
    assert len(changed["data"]["items"])==1


def test_review_wrong_nested_metrics_rejected_before_claim(tmp_path):
    runtime,_=installed(tmp_path,"review")
    fields={"period_start":"2026-10-01","period_end":"2026-10-07","sources":{"task":[]},"missing_sources":[]}
    assert invoke(runtime,"review.generate",fields,"bad")["status"]!="succeeded"
    good=invoke(runtime,"review.generate",{**fields,"sources":{"task":{"planned":2,"completed":1}}},"good")
    assert good["status"]=="succeeded",good
    assert good["data"]["entity"]["fields"]["metrics"]["task_completion_rate"]==0.5


def test_planner_preserves_early_gap_and_normalizes_deadlines():
    values={"windows":[{"start":"2026-10-06T00:00:00Z","end":"2026-10-06T02:00:00Z"}],
            "tasks":[{"id":"later","version":1,"priority":"urgent","estimate_minutes":30,"earliest_start":"2026-10-06T01:00:00Z"},
                     {"id":"early","version":1,"priority":"normal","estimate_minutes":30,"due_at":"2026-10-06T00:30:00Z"}]}
    result=schedule(values)
    assert len(result["scheduled"])==2
    assert result["scheduled"][1]["id"]=="early"


def test_finance_pagination_bound_to_scope_and_filter(tmp_path):
    store=Store(tmp_path/"finance.sqlite","yushuos.finance");s=DomainService("finance",store)
    for i in range(3):
        with store.write() as db:
            s.operate("finance.statement.create",{"month":"2026-10","currency":"CNY","source_id":str(i),"transactions":[]},"personal",db)
    a=s.operate("finance.statement.list",{"limit":2},"personal")[0]
    b=s.operate("finance.statement.list",{"limit":2,"cursor":a["next_cursor"]},"personal")[0]
    assert len(a["items"])==2 and len(b["items"])==1 and b["next_cursor"] is None
    with pytest.raises(DomainError,match="invalid_cursor"):
        s.operate("finance.statement.list",{"limit":2,"cursor":a["next_cursor"]},"other")


def test_decision_option_membership_and_temporal_latest(tmp_path):
    store=Store(tmp_path/"decision.sqlite","yushuos.decision");s=DomainService("decision",store)
    with store.write() as db:
        e=s.operate("decision.create",{"title":"choice","options":[{"id":"a","label":"A"}]},"personal",db)[0]["entity"]
        e=s.operate("decision.choose",{"id":e["id"],"expected_version":1,"option_id":"a","reason":"test"},"personal",db)[0]["entity"]
    with pytest.raises(DomainError,match="invalid_option"):
        with store.write() as db:s.operate("decision.update",{"id":e["id"],"expected_version":2,"changes":{"options":[{"id":"b","label":"B"}]}},"personal",db)
    body=DomainService("body",Store(tmp_path/"body.sqlite","yushuos.body"))
    with body.store.write() as db:
        body.operate("body.state.record",{"at":"2026-10-06T10:00:00+08:00","source":"manual","energy":2},"personal",db)
        newer=body.operate("body.state.record",{"at":"2026-10-06T03:00:00Z","source":"manual","energy":5},"personal",db)[0]["entity"]
    assert body.operate("body.state.get_latest",{},"personal")[0]["entity"]["id"]==newer["id"]

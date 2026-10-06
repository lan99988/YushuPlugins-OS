from pathlib import Path
import pytest
import yaml
from yushuos.runtime import CoreRuntime
from yushuos.deployment import install_plugin
from yushuos_sdk import StateStore
from integration.flows import Flows, FlowError
from scripts.build_suite import stage


def suite(tmp_path, slugs):
    root=tmp_path/"core";root.mkdir()
    stores={slug:"personal" for slug in slugs}
    config={"schema_version":1,"project_ref":"test-project","plugins":{"versions":{ "yushuos."+s: "0.2.0" if s=="task" else "0.1.0" for s in slugs}},
            "state":{"ledger_path":"operations.sqlite3"},"permissions":{"grants":[s+"."+p for s in slugs for p in ("read","write","delete")]},
            "bindings":{"resources":{s+"_store":"personal" for s in slugs}}}
    (root/"config.yaml").write_text(yaml.safe_dump(config),encoding="utf-8")
    for slug in slugs:
        source=Path(__file__).resolve().parents[1]/"plugins/task/plugin" if slug=="task" else stage(slug,tmp_path/slug)
        assert install_plugin(source,root)["status"]=="succeeded"
    with StateStore(root/"operations.sqlite3").connect(write=True):pass
    return CoreRuntime(root),stores


def test_capture_target_confirmation_and_repeat_without_duplicate_task(tmp_path):
    core,stores=suite(tmp_path,["capture","task"])
    flow=Flows(core,stores,"new-capture")
    capture=flow.call("capture","capture.create",{"content":"test captured task","source":"manual"})["entity"]
    ref=Flows(core,stores,"convert1").capture_to(capture["id"],"task")
    assert Flows(core,stores,"convert1").capture_to(capture["id"],"task")==ref
    tasks=Flows(core,stores,"read-tasks").call("list","task.list",{},intent="query")["tasks"]
    assert len(tasks)==1 and tasks[0]["id"]==ref["id"]
    source=Flows(core,stores,"read-capture").call("get","capture.get",{"id":capture["id"]})["entity"]
    assert source["fields"]["status"]=="processed" and source["fields"]["target_ref"]==ref


@pytest.mark.parametrize("slug",["idea","bug"])
def test_idea_bug_conversion_only_records_confirmed_task_reference(tmp_path,slug):
    core,stores=suite(tmp_path,[slug,"task"])
    source=Flows(core,stores,"make").call("create",slug+".create",{"title":"test"})["entity"]
    ref=Flows(core,stores,"convert").convert_to_task(slug,source["id"])
    assert Flows(core,stores,"convert").convert_to_task(slug,source["id"])==ref
    assert len(Flows(core,stores,"list").call("get","task.list",{},intent="query")["tasks"])==1


def test_task_body_planner_drift_and_deepwork_no_implicit_completion(tmp_path):
    core,stores=suite(tmp_path,["task","body","planner","deepwork"])
    task=Flows(core,stores,"create-task").call("new","task.create",{"title":"focus","priority":"urgent","estimate_minutes":30})["task"]
    prepared=Flows(core,stores,"plan").plan_from_tasks([{"start":"2026-10-06T07:00:00+08:00","end":"2026-10-06T08:00:00+08:00"}],60)
    assert prepared["missing_body"] and len(prepared["plan"]["fields"]["scheduled"])==1
    Flows(core,stores,"update").call("update","task.update",{"task_id":task["id"],"expected_version":1,"changes":{"title":"changed"}})
    with pytest.raises(FlowError):Flows(core,stores,"apply").apply_plan(prepared["plan"]["id"])
    session=Flows(core,stores,"work").call("start","deepwork.start",{"task_ref":{"provider":"yushuos.task","kind":"task","id":task["id"],"store_id":"personal"},"at":"2026-10-06T00:00:00Z"})["entity"]
    Flows(core,stores,"finish").call("finish","deepwork.finish",{"id":session["id"],"expected_version":1,"at":"2026-10-06T00:30:00Z"})
    assert Flows(core,stores,"task-status").call("get","task.get",{"task_id":task["id"]})["task"]["status"]=="open"


def test_review_personal_model_cognition_explicit_evidence_and_missing_sources(tmp_path):
    core,stores=suite(tmp_path,["task","review","personal_model","cognition"])
    Flows(core,stores,"new-task").call("create","task.create",{"title":"one"})
    review=Flows(core,stores,"review").task_review("2000-01-01","2100-01-01")["entity"]
    assert review["fields"]["metrics"]["task_completion_rate"]==0
    assert "habit" in review["fields"]["missing_sources"]
    ref={"provider":"yushuos.review","kind":"review","id":review["id"],"store_id":"personal"}
    hypothesis=Flows(core,stores,"hypothesis").call("create","personal-model.hypothesis.create",{"statement":"tentative","source":"host","evidence_refs":[ref]})["entity"]
    evaluated=Flows(core,stores,"evaluate").call("evaluate","personal-model.evaluate",{"id":hypothesis["id"],"expected_version":1,"confidence":0.4,"reason":"one source only","judgement":"tentative","evidence_refs":[ref]})["entity"]
    assert evaluated["fields"]["confidence"]==0.4
    pattern=Flows(core,stores,"pattern").call("create","cognition.pattern.create",{"statement":"tentative","source":"host"})["entity"]
    with pytest.raises(FlowError):
        Flows(core,stores,"without-evidence").call("analyze","cognition.analyze",{"id":pattern["id"],"expected_version":1,"reason":"guess","judgement":"not supported","evidence_refs":[]})

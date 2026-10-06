"""Every declared local capability has a positive, schema-valid domain exercise."""
from copy import deepcopy
import pytest
from yushuos.manifest import validate_schema
from business_runtime.contracts import REGISTRY
from business_runtime.domains import DomainService
from business_runtime.store import Store
from tests.test_domain_lifecycles import SAMPLES

REF={"provider":"yushuos.review","kind":"review","id":"fixture-review","store_id":"personal"}


def sample(key,schema):
    if key in {"at","captured_at","due_at","deadline","earliest_start","start"}:return "2026-10-06T00:00:00Z"
    if key=="end":return "2026-10-06T02:00:00Z"
    if key in {"date","due_date","period_start","period_end"}:return "2026-10-06"
    if key=="month":return "2026-10"
    if key=="currency":return "CNY"
    if key=="timezone":return "Asia/Shanghai"
    if key.endswith("_ref") or key=="reference":return deepcopy(REF)
    if key in {"evidence_refs","counterevidence_refs","source_refs"}:return [deepcopy(REF)]
    if key in {"income","expense","assets","liabilities","amount"}:return "1.00"
    if key=="options":return [{"id":"a","label":"A"}]
    if key=="tasks":return []
    if key=="windows":return [{"start":"2026-10-06T00:00:00Z","end":"2026-10-06T02:00:00Z"}]
    if key=="sources":return {"task":{"planned":1,"completed":0}}
    if key=="confidence":return 0.5
    if "enum" in schema:return schema["enum"][0]
    kind=schema.get("type")
    if kind=="string":return "fixture"
    if kind=="boolean":return False
    if kind in {"integer","number"}:return schema.get("minimum",1)
    if kind=="array":return []
    if kind=="object":return {k:sample(k,schema["properties"][k]) for k in schema.get("required",[])}
    raise AssertionError((key,schema))


CASES=[(slug,name) for slug,caps in REGISTRY.items() for name in caps]


@pytest.mark.parametrize("slug,name",CASES)
def test_each_local_capability_has_real_domain_result(tmp_path,slug,name):
    service=DomainService(slug,Store(tmp_path/"plugin.sqlite","yushuos."+slug))
    def write(cap,fields):
        with service.store.write() as db:return service.operate(cap,fields,"personal",db)[0]
    seedcap,seedfields,primarykind=SAMPLES[slug]
    seed=write(seedcap,seedfields)["entity"]
    kind,action=name.rsplit(".",1)
    schema=REGISTRY[slug][name]["inputs"]
    fields={k:sample(k,schema["properties"][k]) for k in schema.get("required",[])}
    if "id" in fields:fields["id"]=seed["id"]
    if "expected_version" in fields:fields["expected_version"]=seed["version"]
    if "person_id" in fields:fields["person_id"]=seed["id"]
    if "habit_id" in fields:fields["habit_id"]=seed["id"]
    if "knowledge_id" in fields:fields["knowledge_id"]=seed["id"]
    if "option_id" in fields:fields["option_id"]="a"
    if "changes" in fields:
        allowed=schema["properties"]["changes"]["properties"]
        key=next(iter(allowed));fields["changes"]={key:sample(key,allowed[key])}
    if slug=="finance" and kind=="finance.monthly_snapshot":
        seed=write("finance.monthly_snapshot.create",{"month":"2026-10","currency":"CNY","source_id":"snap", "income":"1.00","expense":"0.00","assets":"2.00","liabilities":"0.00"})["entity"]
        if "id" in fields:fields["id"]=seed["id"]
    if slug=="finance" and action=="create":fields["source_id"]="new-source"
    if slug=="capture" and action=="mark_processed":fields["target_ref"]=deepcopy(REF)
    if slug=="bug" and action in {"close","reopen"}:
        seed=write("bug.resolve",{"id":seed["id"],"expected_version":seed["version"]})["entity"]
        fields["expected_version"]=seed["version"]
    if slug=="habit" and action in {"undo_checkin","history"}:
        write("habit.checkin",{"habit_id":seed["id"],"date":"2026-10-06"})
    if slug=="habit" and action=="resume":
        seed=write("habit.pause",{"id":seed["id"],"expected_version":1})["entity"]
        fields["expected_version"]=seed["version"]
    if slug=="deepwork" and action=="resume":
        seed=write("deepwork.pause",{"id":seed["id"],"expected_version":1,"at":"2026-10-06T00:00:00Z"})["entity"]
        fields["expected_version"]=seed["version"]
    if slug=="planner" and action=="apply":fields["input_digest"]=seed["fields"]["input_digest"]
    if slug=="review" and action=="compare":fields["other_id"]=write(seedcap,seedfields)["entity"]["id"]
    if slug=="creation" and action=="publication":
        for stage in ("draft","review","ready"):
            seed=write("creation.advance_stage",{"id":seed["id"],"expected_version":seed["version"],"stage":stage})["entity"]
        fields["expected_version"]=seed["version"]
    if slug=="relationship" and action=="recent":
        write("relationship.interaction.record",{"person_id":seed["id"],"at":"2026-10-06T00:00:00Z"})
    assert not validate_schema(schema,fields), (name,fields)
    if REGISTRY[slug][name]["effect"]=="read_only":
        data,_=service.operate(name,fields,"personal")
    else:
        data=write(name,fields)
        assert data["operation_status"]=="committed"
        assert data["entity"]["id"]
    assert not validate_schema(REGISTRY[slug][name]["outputs"],data),(name,data)

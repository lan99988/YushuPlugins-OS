"""Release contracts must load in the pinned Core, not just look like Schema."""
import json
from pathlib import Path
import pytest
from yushuos.manifest import load_manifest, _validate_schema_definition

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"task", "capture", "idea", "bug", "finance", "relationship", "habit", "body", "lifeadmin", "decision",
            "feishu", "ima", "planner", "deepwork", "competition", "knowledge", "creation", "review", "personal_model", "cognition"}


def test_inventory_has_twenty_independent_core_compatible_packages():
    assert {p.name for p in (ROOT/"contracts").iterdir() if p.is_dir()} == EXPECTED
    seen=set()
    for slug in EXPECTED:
        document=json.loads((ROOT/"contracts"/slug/"capabilities.json").read_text(encoding="utf-8"))
        assert document["contract_version"]==3
        assert document["provider_id"]=="yushuos."+slug
        spec=load_manifest(ROOT/"plugins"/slug/"plugin/plugin.yaml")
        assert spec.package_hash_verified
        names={cap.name for cap in spec.capabilities}
        assert names=={cap["name"] for cap in document["capabilities"]}
        assert not names & seen
        seen |= names
        for cap in spec.capabilities:
            _validate_schema_definition(cap.inputs,cap.name)
            _validate_schema_definition(cap.outputs,cap.name)
            assert cap.inputs["additionalProperties"] is False
            assert cap.intents and cap.permissions
        if slug not in {"feishu","ima"}:
            assert spec.operation_support=="local_commit_v1"
            assert all(cap.effect in {"internal_write","read_only"} for cap in spec.capabilities)
        else:
            assert spec.operation_support is None
            assert all(cap.name.startswith(slug+".") for cap in spec.capabilities)


@pytest.mark.parametrize("slug", EXPECTED-{ "task","feishu","ima" })
def test_staged_helpers_match_reviewed_canonical_sources(slug):
    source=ROOT/"business_runtime"
    staged=ROOT/"plugins"/slug/"plugin/business_runtime"
    assert {p.name for p in source.glob("*.py")} == {p.name for p in staged.glob("*.py")}
    assert all(p.read_bytes()==(staged/p.name).read_bytes() for p in source.glob("*.py"))


def test_host_required_is_declared_without_hidden_llm_service():
    for slug,name in [("review","review.interpret"),("personal_model","personal-model.evaluate"),("cognition","cognition.analyze")]:
        spec=load_manifest(ROOT/"plugins"/slug/"plugin/plugin.yaml")
        cap=next(c for c in spec.capabilities if c.name==name)
        assert cap.execution_mode=="host_required"
        assert {"judgement","evidence_refs"} <= set(cap.inputs["required"])
    review=load_manifest(ROOT/"plugins/review/plugin/plugin.yaml")
    assert next(c for c in review.capabilities if c.name=="review.generate").execution_mode=="standalone"

import importlib
import json
import shutil
from pathlib import Path
import pytest
import yaml
from yushuos.deployment import lock_plugin
from yushuos.runtime import CoreRuntime
from tests.test_system_flows import suite
from integration.flows import Flows, FlowError


def attach_mock_app(root, tmp_path, slug, fail=False):
    adapter = importlib.import_module("plugins." + slug + ".adapter")
    configure = importlib.import_module("plugins." + slug + ".configure")
    source = tmp_path / ("mock-" + slug)
    source.mkdir()
    for name in ("adapter.py", "definition.py", "protocol.py", "configure.py"):
        shutil.copyfile(Path(__file__).resolve().parents[1] / "plugins" / slug / name, source / name)
    response = {"event": {"event_id": "mock-event", "summary": "test"}} if slug == "feishu" else {"code": 0, "data": {"note_id": "mock-note"}}
    read_response = {"code": 0, "data": {"content": "mock note text"}}
    code = 'import json\nfrom pathlib import Path\nfrom adapter import handle_envelope\nfrom protocol import main\n'
    code += f'RESPONSE={response!r}\nREAD={read_response!r}\nFAIL={fail!r}\n'
    code += '''def handle(envelope):
 data=Path(envelope["context"]["data_path"]); data.mkdir(parents=True,exist_ok=True)
 def provider(*args):
  if args[-1]:
   with (data/"posts.txt").open("a") as f: f.write("post\\n")
   if FAIL: raise TimeoutError()
   return RESPONSE
  return READ
 return handle_envelope(envelope,transport=provider)
main(handle)
'''
    (source / "run.py").write_text(code, encoding="utf-8")
    (source / "plugin.yaml").write_text(yaml.safe_dump(adapter.MANIFEST), encoding="utf-8")
    assert lock_plugin(source)["verified"]
    binding = tmp_path / (slug + "-binding.json")
    binding.write_text(json.dumps({"app": slug, "configured": True, "account_ref": "fake",
                       "credentials": {"client_id": "FAKE", "api_key": "FAKE"},
                       "verified_capabilities": list(adapter.CAPABILITIES), "authorized_capabilities": list(adapter.CAPABILITIES),
                       "grants": [slug + ".read", slug + ".write"]}), encoding="utf-8")
    configure.configure(root, plugin_root=source, binding_file=binding)
    path = root / "config.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["permissions"]["grants"] += [slug + ".read", slug + ".write"]
    value["bindings"]["resources"].update({"calendar": "cal", "notebook": "folder", "note": "mock-note", "account": "fake"})
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return CoreRuntime(root)


def test_planner_calendar_confirm_and_repeat_once(tmp_path):
    core, stores = suite(tmp_path, ["task", "planner"])
    core = attach_mock_app(Path(core.config["_config_root"]), tmp_path, "feishu")
    Flows(core, stores, "task").call("new", "task.create", {"title": "focus", "estimate_minutes": 30})
    prepared = Flows(core, stores, "plan").call("generate", "planner.generate", {
        "tasks": [{"id": Flows(core, stores, "tasks").call("list", "task.list", {})["tasks"][0]["id"], "version": 1, "estimate_minutes": 30}],
        "windows": [{"start": "2026-10-06T00:00:00Z", "end": "2026-10-06T01:00:00Z"}]})["entity"]
    with pytest.raises(FlowError):
        Flows(core, stores, "not-applied").sync_plan_calendar(prepared["id"], "cal", "UTC")
    Flows(core, stores, "apply").apply_plan(prepared["id"])
    refs = Flows(core, stores, "sync").sync_plan_calendar(prepared["id"], "cal", "UTC")
    assert refs[0]["id"] == "mock-event"
    assert Flows(core, stores, "sync").sync_plan_calendar(prepared["id"], "cal", "UTC") == refs
    posts = next((Path(core.config["_config_root"]) / "plugin-data").rglob("posts.txt"))
    assert posts.read_text().splitlines() == ["post"]


@pytest.mark.parametrize("fail", [False, True])
def test_knowledge_ima_reference_only_after_confirmation(tmp_path, fail):
    core, stores = suite(tmp_path, ["knowledge"])
    core = attach_mock_app(Path(core.config["_config_root"]), tmp_path, "ima", fail)
    entity = Flows(core, stores, "make").call("new", "knowledge.create", {"title": "note", "content": "example"})["entity"]
    if fail:
        with pytest.raises(FlowError):
            Flows(core, stores, "export").knowledge_to_ima(entity["id"], "folder")
        current = Flows(core, stores, "read").call("get", "knowledge.get", {"id": entity["id"]})["entity"]
        assert not current["fields"].get("ima_ref")
    else:
        ref = Flows(core, stores, "export").knowledge_to_ima(entity["id"], "folder")
        assert ref["id"] == "mock-note"
        assert Flows(core, stores, "export").knowledge_to_ima(entity["id"], "folder") == ref
        imported = Flows(core, stores, "import").ima_to_knowledge("mock-note")
        assert imported["fields"]["content"] == "mock note text" and imported["fields"]["ima_ref"]["provider"] == "yushuos.ima"
        assert Flows(core, stores, "import").ima_to_knowledge("mock-note")["id"] == imported["id"]
    posts = next((Path(core.config["_config_root"]) / "plugin-data").rglob("posts.txt"))
    assert len(posts.read_text().splitlines()) == 1

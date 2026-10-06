import json
import os
from pathlib import Path
import sys
import time
import pytest
from unittest.mock import Mock, patch
from tests import test_devflow_runtime as fixtures
from devflow.runtime import CommandRunner, Coordinator, tree_sha, rules_sha
from devflow.engine import failure_kind


def test_timeout_kills_descendant_after_parent_exit(tmp_path):
    child = "import time; from pathlib import Path; time.sleep(1); Path('survived').write_text('bad'); time.sleep(3)"
    parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "])"
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        CommandRunner().run([sys.executable, "-c", parent], tmp_path, timeout=.2)
    assert time.monotonic() - start < 2
    time.sleep(1.1)
    assert not (tmp_path / "survived").exists()


def test_contained_runner_terminates_direct_child_without_killing_inherited_group():
    runner = CommandRunner()
    runner.contained = True
    process = Mock(pid=4321)
    with patch('devflow.runtime.os.name', 'posix'), patch('devflow.runtime.signal.SIGKILL', 9, create=True), \
            patch('devflow.runtime.os.killpg', create=True) as group:
        runner.kill(process)
    process.kill.assert_called_once()
    group.assert_not_called()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group containment')
def test_successful_parent_exit_cleans_orphaned_background_descendant(tmp_path):
    child = "import time; from pathlib import Path; time.sleep(.8); Path('escaped').write_text('bad')"
    parent = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child)
              + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
    assert CommandRunner().run([sys.executable, '-c', parent], tmp_path, timeout=5) == ''
    time.sleep(1)
    assert not (tmp_path / 'escaped').exists()


@pytest.mark.parametrize("label", ["login required", "rate limit", "connection reset"])
def test_failed_test_output_cannot_impersonate_auth_quota_network(tmp_path, label):
    with pytest.raises(RuntimeError) as refused:
        CommandRunner().run([sys.executable, "-c", "raise AssertionError(" + repr(label) + ")"], tmp_path)
    assert failure_kind(refused.value) == "validation"


def test_task_argv_expands_python_placeholder_and_validates():
    fixture = fixtures.RuntimeTests()
    fixture.setUp()
    try:
        fixture.store.set_task("a", tests=[["{python}", "-c", "from pathlib import Path; assert Path('plugins/a/file.txt').read_text()=='ok'"]])
        class Fake(CommandRunner):
            def codex(self, worktree, prompt, model, effort, sandbox, schema, output, log, timeout=1800):
                if sandbox == "workspace-write":
                    path = Path(worktree) / "plugins/a/file.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("ok")
                    binding = json.loads(prompt.split("IMPLEMENTATION_CONTRACT=")[1].split("\n")[0])
                    binding.update(tree_sha=tree_sha(worktree), rules_sha=rules_sha(worktree))
                else:
                    binding = json.loads(prompt.split("EVIDENCE_BINDING=")[1].split("\n")[0])
                Path(output).write_text(json.dumps(dict(binding, outcome="pass", summary="ok", tests=[], findings=[])))
                return {"session": "fake", "usage": {}}
        worker = Coordinator(fixture.repo, fixture.store, runner=Fake())
        worker.execute("a")
        assert fixture.store.task("a")["status"] == "verified"
        assert fixture.store.task("a")["evidence"][0]["argv"][0] == sys.executable
    finally:
        fixture.tearDown()


def test_publish_rejects_wrong_actual_base():
    fixture = fixtures.RuntimeTests()
    fixture.setUp()
    try:
        worker = Coordinator(fixture.repo, fixture.store)
        worktree = worker.worktree(fixture.store.task("a"))
        head = fixtures.git(worktree, "rev-parse", "HEAD")
        fixture.store.set_task("a", status="verified", head_sha=head, review_head=head,
                              review_tree=fixtures.git(worktree, "rev-parse", "HEAD^{tree}"), review_report="fake",
                              publish={"base": "main"})
        class WrongBase(CommandRunner):
            def __init__(self):
                super().__init__()
                self.merged = False
            def run(self, argv, cwd, **kwargs):
                if argv[1:3] == ["pr", "list"]:
                    return json.dumps([{"number": 1, "url": "https://example.invalid/pr/1", "headRefOid": head,
                                        "state": "OPEN", "baseRefName": "production"}])
                if argv[1:3] == ["pr", "view"]:
                    return json.dumps({"headRefOid": head, "baseRefName": "production", "state": "OPEN",
                                       "reviewDecision": None, "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}]})
                if argv[1:3] == ["pr", "merge"]:
                    self.merged = True
                if argv[1:3] == ["repo", "view"]:
                    return '{"nameWithOwner":"owner/repo"}'
                if argv[1:3] == ["api", "graphql"]:
                    return '{"data":{"repository":{"ref":{"branchProtectionRule":null}}}}'
                return "[]" if argv[1] == "api" else ""
        fake = WrongBase()
        worker.runner = fake
        with pytest.raises(RuntimeError, match="base"):
            worker.publish_task("a")
        assert not fake.merged
    finally:
        fixture.tearDown()

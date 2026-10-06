import json
from pathlib import Path
import sys
import pytest
from devflow.engine import validate_manifest
from devflow.runtime import CommandRunner, Coordinator, git
from tests import test_devflow_runtime as fixtures


@pytest.fixture
def verification():
    fixture = fixtures.RuntimeTests()
    fixture.setUp()
    fixture.store.set_task('a', kind='verify', tests=[['{python}', '-c', "from pathlib import Path; assert Path('README.md').read_text()=='initial'"]])
    yield fixture
    fixture.tearDown()


class ReadOnlyReviewer(CommandRunner):
    def __init__(self, mutate=False):
        super().__init__()
        self.calls = []
        self.mutate = mutate

    def codex(self, worktree, prompt, model, effort, sandbox, schema, output, log, timeout=1800):
        self.calls.append(sandbox)
        assert sandbox == 'read-only', 'verification must not invoke implementation'
        assert 'TEST_EVIDENCE=' in prompt
        assert json.loads(prompt.split('TEST_EVIDENCE=')[1])[-1]['passed']
        binding = json.loads(prompt.split('EVIDENCE_BINDING=')[1].split('\n')[0])
        Path(output).write_text(json.dumps(dict(binding, outcome='pass', summary='audited', tests=[], findings=[])))
        if self.mutate:
            (Path(worktree) / 'README.md').write_text('tampered')
        return {'session': 'review-only', 'usage': {'input_tokens': 4}}


def test_verify_runs_tests_and_one_independent_review_without_commit(verification):
    initial = git(verification.repo, 'rev-parse', 'HEAD')
    reviewer = ReadOnlyReviewer()
    worker = Coordinator(verification.repo, verification.store, runner=reviewer)
    worker.run()
    task = verification.store.task('a')
    assert task['status'] == 'verified', task
    assert reviewer.calls == ['read-only']
    assert task['head_sha'] == task['review_head'] == initial
    assert task['usage_records'][0]['phase'] == 'review'
    assert task['evidence'][0]['argv'][0] == sys.executable
    assert git(task['worktree'], 'status', '--porcelain') == ''


def test_verify_rejects_source_mutation(verification):
    worker = Coordinator(verification.repo, verification.store, runner=ReadOnlyReviewer(mutate=True))
    worker.run()
    assert verification.store.task('a')['status'] != 'verified'


def test_verification_task_cannot_request_publication():
    task = {'id': 'audit', 'kind': 'verify', 'allowed_paths': ['plugins/a/'], 'tests': [['python', '-V']],
            'publish': {'enabled': True}}
    with pytest.raises(ValueError, match='verification.*publish'):
        validate_manifest([task])


def test_ignored_rules_changed_by_test_invalidate_verification(verification):
    (verification.repo / '.gitignore').write_text('ignored/\n')
    git(verification.repo, 'add', '.')
    git(verification.repo, 'commit', '-m', 'ignore local files')
    verification.store.set_task('a', tests=[['{python}', '-c',
        "from pathlib import Path; p=Path('ignored/AGENTS.md'); p.parent.mkdir(); p.write_text('changed rules')"]])
    reviewer = ReadOnlyReviewer()
    Coordinator(verification.repo, verification.store, runner=reviewer).execute('a')
    task = verification.store.task('a')
    assert 'test rules drift' in task.get('error', '')
    assert task['status'] != 'verified'
    assert reviewer.calls == []


def test_ignored_rules_changed_after_review_prevent_publish(verification):
    reviewer = ReadOnlyReviewer()
    worker = Coordinator(verification.repo, verification.store, runner=reviewer)
    worker.execute('a')
    task = verification.store.task('a')
    assert task['status'] == 'verified'
    # Simulate an implementation task's saved publication evidence; the fixture
    # then adds a Git-ignored AGENTS file while leaving head/tree/status unchanged.
    exclude = Path(git(task['worktree'], 'rev-parse', '--git-path', 'info/exclude'))
    if not exclude.is_absolute(): exclude = Path(task['worktree']) / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text('ignored/\n')
    rules = Path(task['worktree']) / 'ignored/AGENTS.md'
    rules.parent.mkdir()
    rules.write_text('new local rule')
    assert git(task['worktree'], 'status', '--porcelain') == ''
    verification.store.set_task('a', kind='implement')
    with pytest.raises(RuntimeError, match='publish rules drift'):
        worker.publish_task('a')

import json
import tempfile
import unittest
from pathlib import Path
from devflow.engine import Store, parse_report, model_for, allowed, publish_gate, validate_manifest

class DevflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'state.db')
        self.tasks = [{'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[['python','-V']],'prompt':'a','risk':'low'}, {'id':'b','deps':['a'],'allowed_paths':['plugins/b/'],'tests':[['python','-V']],'prompt':'b','risk':'low'}]
        self.store.initialize(self.tasks)
    def tearDown(self): self.tmp.cleanup()
    def test_dag_and_fence(self):
        fence = self.store.lease('one', 100, 10)
        with self.assertRaises(RuntimeError): self.store.lease('two', 101, 10)
        self.assertEqual(self.store.ready(100)[0]['id'],'a')
        newer = self.store.lease('two', 111, 10)
        with self.assertRaises(RuntimeError): self.store.set_task('a', fence=fence,status='running')
        self.store.set_task('a', fence=newer,status='verified')
        self.assertEqual(self.store.ready(112)[0]['id'],'b')
    def test_recovery_preserves_and_does_not_complete(self):
        self.store.set_task('a',status='running',worktree='saved',attempt=1)
        self.store.recover()
        task=self.store.task('a')
        self.assertEqual(task['status'],'interrupted')
        self.assertEqual(task['worktree'],'saved')
        self.assertFalse(self.store.ready(1))
    def test_report_strict_and_bound(self):
        report={'task_id':'a','tree_sha':'abc','contract_sha':'def','rules_sha':'ghi','outcome':'pass','summary':'ok','tests':[],'findings':[]}
        self.assertEqual(parse_report(json.dumps(report),'a','abc','def','ghi')['outcome'],'pass')
        for bad in [dict(report,extra=1),dict(report,tree_sha='old'),dict(report,outcome='done')]:
            with self.assertRaises(ValueError): parse_report(json.dumps(bad),'a','abc','def','ghi')
    def test_models_paths_and_gate(self):
        self.assertEqual(model_for('low',0)[0],'gpt-6-luna')
        self.assertEqual(model_for('low',2)[0],'gpt-6.1-sol')
        self.assertEqual(model_for('critical',0)[0],'gpt-6-astra')
        self.assertTrue(allowed('plugins/a/file.py',['plugins/a/']))
        self.assertFalse(allowed('plugins/ab/file.py',['plugins/a/']))
        self.assertFalse(allowed('../plugins/a/file.py',['plugins/a/']))
        self.assertFalse(publish_gate('sha',{'headRefOid':'other','reviewDecision':'APPROVED','statusCheckRollup':[]},'sha'))
        self.assertFalse(publish_gate('sha',{'headRefOid':'sha','reviewDecision':'APPROVED','statusCheckRollup':[]},'sha'))
        self.assertTrue(publish_gate('sha',{'headRefOid':'sha','reviewDecision':'APPROVED','statusCheckRollup':[{'status':'COMPLETED','conclusion':'SUCCESS'}]},'sha'))
    def test_cycle_invalid(self):
        self.tasks[0]['deps']=['b']
        with self.assertRaises(ValueError): validate_manifest(self.tasks)
    def test_failure_escalation_and_wait(self):
        self.store.failure('a','test failure',100)
        self.store.failure('a','test failure',101)
        self.assertEqual(self.store.task('a')['level'],1)
        self.store.failure('a','quota exceeded',102)
        self.assertEqual(self.store.task('a')['status'],'waiting_quota')
        self.assertGreaterEqual(self.store.task('a')['next_run'],1902)

if __name__ == '__main__': unittest.main()
class RecoveryTests(unittest.TestCase):
    def test_release_allows_immediate_new_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.db'); fence=store.lease('one')
            store.release('one',fence)
            self.assertGreater(store.lease('two'),fence)
    def test_reset_time_quota_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.db'); store.initialize([{'id':'a','deps':[],'allowed_paths':['a/'],'tests':[['python','-V']],'risk':'low','prompt':'a'}])
            store.failure('a','quota exceeded resetAt=5000',100)
            self.assertEqual(store.task('a')['next_run'],5000)
class RiskPathTests(unittest.TestCase):
    def test_actual_sensitive_path_overrides_manifest(self):
        from devflow.engine import resolved_risk
        self.assertEqual(resolved_risk('low',['plugins/foo/auth.py']),'critical')
        self.assertEqual(resolved_risk('medium',['plugins/foo/main.py']),'medium')

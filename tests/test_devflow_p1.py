import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_devflow_runtime as fixtures
from devflow.engine import Store, model_for
from devflow.runtime import Coordinator, CommandRunner, git

class P1Tests(unittest.TestCase):
    setUp=fixtures.RuntimeTests.setUp
    tearDown=fixtures.RuntimeTests.tearDown
    git=fixtures.RuntimeTests.git
    def cleanup_worktree(self):
        tree=self.store.task('a').get('worktree')
        if tree: subprocess.run(['git','worktree','remove','--force',tree],cwd=self.repo,capture_output=True)
    def test_non_gh_process_has_no_github_credentials(self):
        with patch.dict(os.environ,{'GH_TOKEN':'original-gh-secret','GITHUB_TOKEN':'original-github-secret'}):
            result=CommandRunner().run([sys.executable,'-c','import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k in ("GH_TOKEN","GITHUB_TOKEN")}))'],self.repo)
            self.assertEqual(json.loads(result),{})
            log=self.root/'error.log'
            with self.assertRaises(RuntimeError) as error:
                CommandRunner().run([sys.executable,'-c','import sys; print("original-gh-secret original-github-secret"); sys.exit(1)'],self.repo,log=log)
            self.assertNotIn('original-gh-secret',str(error.exception))
            self.assertNotIn('original-github-secret',log.read_text())
    def test_publish_serialized_and_finished_task_not_republished(self):
        worker=Coordinator(self.repo,self.store); entered=[]; overlap=[]; active=0; guard=threading.Lock()
        def publish(id):
            nonlocal active
            with guard: active+=1; overlap.append(active); entered.append(id)
            time.sleep(.05)
            with guard: active-=1
        worker._publish_task=publish
        threads=[threading.Thread(target=worker.publish_task,args=('a',)) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(max(overlap),1)
    def test_test_that_changes_implementation_is_rejected(self):
        task=self.store.task('a'); task['tests']=[[sys.executable,'-c','from pathlib import Path; Path("plugins/a/file.txt").write_text("changed")']]
        self.store.set_task('a',tests=task['tests'])
        class Fake(CommandRunner):
            def codex(self,worktree,prompt,*args,**kwargs):
                path=Path(worktree)/'plugins/a/file.txt'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('ok')
                binding=json.loads(prompt.split('IMPLEMENTATION_CONTRACT=')[1].split('\n')[0]); binding.update(tree_sha=fixtures.tree_sha(worktree),rules_sha=fixtures.rules_sha(worktree))
                Path(args[4]).write_text(json.dumps(dict(binding,outcome='pass',summary='ok',tests=[],findings=[])))
                return {'session':'s','usage':{'input_tokens':1}}
        worker=Coordinator(self.repo,self.store,runner=Fake()); worker.execute('a')
        self.assertIn('test tree drift',self.store.task('a').get('error',''))
        self.assertNotEqual(self.store.task('a')['status'],'verified'); self.cleanup_worktree()
    def test_retry_prompt_has_failure_and_prior_evidence(self):
        self.store.set_task('a',attempt=1,last_failure='assertion exploded',evidence=[{'log':'prior.log','sha':'evidence-sha'}])
        prompts=[]
        class Fake(CommandRunner):
            def codex(self,worktree,prompt,*args,**kwargs): prompts.append(prompt); raise RuntimeError('abort fake')
        Coordinator(self.repo,self.store,runner=Fake()).execute('a')
        self.assertIn('assertion exploded',prompts[0]); self.assertIn('evidence-sha',prompts[0]); self.cleanup_worktree()
    def test_attempt_usage_is_append_only(self):
        self.store.record_usage('a',1,'implementation',{'session':'first','usage':{'input_tokens':2}},'model','high')
        self.store.record_usage('a',2,'implementation',{'session':'second','usage':{'input_tokens':3}},'model','high')
        self.assertEqual([u['usage']['input_tokens'] for u in self.store.task('a')['usage_records']],[2,3])
    def test_new_default_models_and_forbidden_models(self):
        from devflow.capabilities import DEFAULT_POLICY, validate_model_policy, select_verified_model
        self.assertEqual(model_for('low',0),('gpt-6-luna','max'))
        self.assertEqual(model_for('critical',0),('gpt-6.1-sol','max'))
        self.assertNotIn('l4_fallback',DEFAULT_POLICY)
        with self.assertRaises(RuntimeError): select_verified_model('gpt-6-astra','high',[{'model':'gpt-6.1-sol','supportedReasoningEfforts':[{'reasoningEffort':'max'}]}])
        forbidden={'levels':[{'model':'gpt-5.6-sol','effort':'high'}]*4}
        with self.assertRaises(RuntimeError): validate_model_policy(forbidden,[{'model':'gpt-5.6-sol','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}])
    def test_release_archive_is_built_from_merge_commit_and_hashes_bound(self):
        from devflow.release import build_release_assets
        old=self.git('rev-parse','HEAD'); (self.repo/'README.md').write_text('merge content'); self.git('add','.'); self.git('commit','-m','merge')
        merged=self.git('rev-parse','HEAD')
        self.git('checkout','--detach',old)
        assets=build_release_assets(self.repo,merged,'v-test',self.root/'release',{'contract_sha':'c'},CommandRunner())
        import zipfile,hashlib
        lock=json.loads((self.root/'release/release.lock.json').read_text())
        self.assertEqual(lock['merge_sha'],merged)
        archive=next(p for p in assets if p.suffix=='.zip')
        with zipfile.ZipFile(archive) as data: self.assertEqual(data.read('README.md').decode(),'merge content')
        self.assertEqual(lock['assets'][archive.name],hashlib.sha256(archive.read_bytes()).hexdigest())
class P2Tests(P1Tests):
    def test_implementation_report_is_required(self):
        class Fake(CommandRunner):
            def codex(self,worktree,prompt,*args,**kwargs):
                path=Path(worktree)/'plugins/a/file.txt'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('ok')
                return {'session':'s','usage':{}}
        Coordinator(self.repo,self.store,runner=Fake()).execute('a')
        self.assertIn('implementation report',self.store.task('a').get('error',''))
        self.cleanup_worktree()
    def test_dependency_change_blocks_existing_worktree(self):
        first=Coordinator(self.repo,self.store).worktree(self.store.task('a')); head=git(first,'rev-parse','HEAD')
        self.store.set_task('a',status='verified',head_sha=head,review_head=head,review_tree=git(first,'rev-parse','HEAD^{tree}'))
        original={'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[[sys.executable,'-c','from pathlib import Path; assert Path("plugins/a/file.txt").read_text()=="ok"']],'risk':'low','prompt':'create file'}
        self.store.initialize([original,{'id':'b','deps':['a'],'allowed_paths':['b/'],'tests':[[sys.executable,'-V']],'risk':'low','prompt':'b'}])
        worker=Coordinator(self.repo,self.store); second=worker.worktree(self.store.task('b'))
        self.store.set_task('a',review_head='stale')
        with self.assertRaisesRegex(RuntimeError,'dependency'): worker.worktree(self.store.task('b'))
        subprocess.run(['git','worktree','remove','--force',str(second)],cwd=self.repo,capture_output=True); self.cleanup_worktree()
    def test_structured_failure_does_not_block_on_auth_word_in_assertion(self):
        self.store.failure('a','assertion failed: auth widget label incorrect')
        self.assertEqual(self.store.task('a')['status'],'retry')
        self.assertEqual(self.store.task('a')['failure_kind'],'validation')
class ReleaseAssetTests(P1Tests):
    def test_remote_asset_mismatch_is_never_clobbered(self):
        from devflow.release import ensure_release_assets
        asset=self.root/'package.zip'; asset.write_bytes(b'local')
        class Fake(CommandRunner):
            def run(self,argv,cwd,**kwargs):
                self.argv=argv
                return json.dumps({'assets':[{'name':'package.zip','digest':'sha256:wrong'}]})
        fake=Fake()
        with self.assertRaisesRegex(RuntimeError,'hash mismatch'): ensure_release_assets(fake,self.repo,'gh','v1',[asset])
        self.assertNotIn('upload',fake.argv)
    def test_build_suite_outputs_are_all_in_merge_lock(self):
        from devflow.release import build_release_assets
        merged=self.git('rev-parse','HEAD')
        code='from pathlib import Path; import sys; p=Path(sys.argv[1]); (p/"plugin.zip").write_bytes(b"built"); (p/"suite-manifest.json").write_text("{}"); (p/"SHA256SUMS").write_text("builder sums")'
        task={'contract_sha':'contract','publish':{'build_commands':[[sys.executable,'-c',code,'{output_dir}']]}}
        assets=build_release_assets(self.repo,merged,'v1',self.root/'output',task,CommandRunner())
        lock=json.loads((self.root/'output/release.lock.json').read_text())
        self.assertIn('plugin.zip',lock['assets']); self.assertIn('suite-manifest.json',lock['assets']); self.assertIn('suite-SHA256SUMS',lock['assets'])
        self.assertEqual(lock['merge_sha'],merged)

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from devflow.engine import Store, publish_gate
from devflow.runtime import Coordinator, CommandRunner, changed_paths, git, tree_sha, rules_sha

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        self.git('init'); self.git('config','user.email','test@example.invalid'); self.git('config','user.name','Test')
        (self.repo/'README.md').write_text('initial'); self.git('add','.'); self.git('commit','-m','initial')
        self.store=Store(self.root/'state'/'state.db')
        self.store.set_meta('capabilities',{'command':'fake','models':[{'model':m,'supportedReasoningEfforts':[{'reasoningEffort':e} for e in ['medium','high','max']]} for m in ['gpt-6-luna','gpt-6.1-sol']]})
        self.store.initialize([{'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[[sys.executable,'-c','from pathlib import Path; assert Path("plugins/a/file.txt").read_text()=="ok"']],'risk':'low','prompt':'create file'}])
    def tearDown(self): self.tmp.cleanup()
    def git(self,*args): return subprocess.run(['git',*args],cwd=self.repo,capture_output=True,text=True,check=True).stdout.strip()
    def test_full_run_independent_review(self):
        class Fake(CommandRunner):
            def codex(self,worktree,prompt,model,effort,sandbox,schema,output,log,timeout=1800):
                if sandbox=='workspace-write':
                    path=Path(worktree)/'plugins/a/file.txt'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('ok')
                    binding=json.loads(prompt.split('IMPLEMENTATION_CONTRACT=')[1].split('\n')[0]); binding.update(tree_sha=tree_sha(worktree),rules_sha=rules_sha(worktree))
                    Path(output).write_text(json.dumps(dict(binding,outcome='pass',summary='implemented',tests=[],findings=[])))
                else:
                    binding=json.loads(prompt.split('EVIDENCE_BINDING=')[1].split('\n')[0])
                    Path(output).write_text(json.dumps(dict(binding,outcome='pass',summary='reviewed',tests=['checked'],findings=[])))
                return {'session':'fake-session','usage':{'input_tokens':5}}
        worker=Coordinator(self.repo,self.store,runner=Fake())
        worker.run(watch=False)
        t=self.store.task('a'); self.assertEqual(t['status'],'verified'); self.assertTrue(t['head_sha']); self.assertTrue(t['evidence'])
        self.assertEqual(self.git('status','--porcelain'),'')
        subprocess.run(['git','worktree','remove','--force',t['worktree']],cwd=self.repo,check=True,capture_output=True)
    def test_untracked_scope_violation(self):
        (self.repo/'secret.txt').write_text('bad')
        self.assertIn('secret.txt',changed_paths(self.repo,self.git('rev-parse','HEAD')))
    def test_process_deadline(self):
        with self.assertRaises(TimeoutError): CommandRunner().run([sys.executable,'-c','import time; time.sleep(60)'],self.repo,timeout=.1)

if __name__=='__main__': unittest.main()
import os
from unittest.mock import patch

class AdditionalRuntimeTests(unittest.TestCase):
    def test_codex_exact_flags_and_stdin(self):
        class Capture(CommandRunner):
            def run(self,argv,cwd,**kw):
                self.argv=argv; self.kw=kw
                return '{"type":"thread.started","thread_id":"s"}\n{"type":"turn.completed","usage":{"input_tokens":9}}'
        r=Capture()
        with patch('devflow.capabilities.resolve_codex',return_value='fake-codex'):
            result=r.codex('.', 'private prompt','gpt-6.1-sol','max','read-only','schema','report','log')
        self.assertEqual(r.kw['input'],'private prompt')
        self.assertNotIn('private prompt',r.argv)
        self.assertEqual(r.argv[r.argv.index('-m')+1],'gpt-6.1-sol')
        self.assertIn('model_reasoning_effort="max"',r.argv)
        self.assertEqual(result['usage']['input_tokens'],9)
    def test_cli_status_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'; manifest=Path(tmp)/'manifest.json'
            manifest.write_text(json.dumps({'version':1,'tasks':[{'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[[sys.executable,'-V']],'risk':'low','prompt':'work'}]}))
            repo=Path(tmp)/'repo'; repo.mkdir()
            subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
            command=[sys.executable,'-m','devflow','--repo',str(repo),'--state',str(state)]
            from devflow.__main__ import main
            with patch('devflow.capabilities.probe_cli',return_value={'command':'fake','models':[{'model':m,'supportedReasoningEfforts':[{'reasoningEffort':e} for e in ['medium','high','max']]} for m in ['gpt-6-luna','gpt-6.1-sol','gpt-6-astra']]}):
                self.assertEqual(main(command[3:]+['init','--manifest',str(manifest)]),0)
            subprocess.run(command+['pause'],check=True,capture_output=True)
            info=json.loads(subprocess.run(command+['status'],check=True,capture_output=True,text=True).stdout)
            self.assertEqual(info['control'],'paused')
            subprocess.run(command+['resume'],check=True,capture_output=True)
            self.assertEqual(Store(state/'state.db').meta('control'),'running')
    def test_shared_path_overlap(self):
        from devflow.runtime import overlaps
        self.assertTrue(overlaps(['plugins/a/'],['plugins/a/file.py']))
        self.assertFalse(overlaps(['plugins/a/'],['plugins/ab/']))
class GhCredentialTests(unittest.TestCase):
    def test_gh_credential_is_private_and_proxy_retry_scoped(self):
        from devflow.runtime import gh_environment
        with patch.dict(os.environ,{'HTTP_PROXY':'private-proxy'},clear=True):
            def credential(*args,**kwargs):
                self.assertEqual(kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
                return type('Result',(),{'returncode':0,'stdout':'username=x\npassword=private-token\n'})()
            env=gh_environment(credential)
            self.assertEqual(env['GH_TOKEN'],'private-token')
            self.assertNotIn('GH_TOKEN',os.environ)
            self.assertEqual(os.environ['HTTP_PROXY'],'private-proxy')
class DependencyTests(RuntimeTests):
    def test_dependency_commit_visible_in_next_worktree(self):
        from devflow.runtime import git
        first=Coordinator(self.repo,self.store).worktree(self.store.task('a'))
        (first/'marker.txt').write_text('dependency')
        git(first,'add','.'); git(first,'commit','-m','dependency')
        self.store.set_task('a',status='verified',head_sha=git(first,'rev-parse','HEAD'),review_head=git(first,'rev-parse','HEAD'),review_tree=git(first,'rev-parse','HEAD^{tree}'),rules_sha=rules_sha(first))
        self.store.initialize([{'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[[sys.executable,'-c','from pathlib import Path; assert Path("plugins/a/file.txt").read_text()=="ok"']],'risk':'low','prompt':'create file'}, {'id':'b','deps':['a'],'allowed_paths':['plugins/b/'],'tests':[[sys.executable,'-V']],'risk':'low','prompt':'b'}])
        worker=Coordinator(self.repo,self.store)
        worktree=worker.worktree(self.store.task('b'))
        self.assertEqual((worktree/'marker.txt').read_text(),'dependency')
        subprocess.run(['git','worktree','remove','--force',str(first)],cwd=self.repo,check=True,capture_output=True)
        subprocess.run(['git','worktree','remove','--force',str(worktree)],cwd=self.repo,check=True,capture_output=True)
    def test_publish_gate_with_fake_gh(self):
        worker=Coordinator(self.repo,self.store)
        worktree=worker.worktree(self.store.task('a'))
        head=git(worktree,'rev-parse','HEAD')
        self.store.set_task('a',head_sha=head,review_head=head,review_tree=git(worktree,'rev-parse','HEAD^{tree}'),review_report='review.json',status='verified',rules_sha=rules_sha(worktree))
        class FakeGh(CommandRunner):
            def __init__(self): super().__init__(); self.calls=[]
            def run(self,argv,cwd,**kwargs):
                self.calls.append(argv)
                if argv[1:3]==['repo','view']: return '{"nameWithOwner":"owner/repo"}'
                if argv[1:3]==['api','graphql']: return '{"data":{"repository":{"ref":{"branchProtectionRule":null}}}}'
                if argv[1]=='api': return '[]'
                if argv[1:3]==['pr','list']: return json.dumps([{'number':1,'url':'https://github.invalid/pull/1','headRefOid':head}])
                if argv[1:3]==['pr','view']:
                    if argv[-1]=='state,baseRefName,mergeCommit': return json.dumps({'state':'MERGED','baseRefName':'main','mergeCommit':{'oid':head}})
                    return json.dumps({'headRefOid':head,'baseRefName':'main','reviewDecision':'APPROVED','state':'OPEN','statusCheckRollup':[{'status':'COMPLETED','conclusion':'SUCCESS'}]})
                return ''
        fake=FakeGh(); worker.runner=fake; worker.publish_task('a')
        self.assertEqual(self.store.task('a')['status'],'published')
        merge=next(c for c in fake.calls if c[1:3]==['pr','merge']); self.assertIn('--match-head-commit',merge)
        subprocess.run(['git','worktree','remove','--force',str(worktree)],cwd=self.repo,check=True,capture_output=True)
class ProcessRecoveryTests(unittest.TestCase):
    def test_orphan_process_identity_recovered_without_touching_diff(self):
        from devflow.runtime import process_identity
        with tempfile.TemporaryDirectory() as tmp:
            process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=os.name!='nt')
            try:
                registry=Path(tmp)/'processes.json'; marker=Path(tmp)/'diff.txt'; marker.write_text('preserved')
                registry.write_text(json.dumps({str(process.pid):{'birth':process_identity(process.pid)}}))
                runner=CommandRunner(); runner.registry=registry; runner.recover_processes()
                process.wait(timeout=5)
                self.assertEqual(marker.read_text(),'preserved')
            finally:
                if process.poll() is None: process.kill(); process.wait()
class GhTransportTests(unittest.TestCase):
    def test_token_redacted_and_only_gh_proxy_removed(self):
        from devflow.runtime import CommandRunner
        environments=[]
        class Process:
            pid=999999999
            def __init__(self,argv,**kwargs): environments.append(kwargs['env']); self.returncode=1 if len(environments)==1 else 0
            def communicate(self,*args,**kwargs): return ('private-token','transport EOF private-token' if self.returncode else '')
            def poll(self): return self.returncode
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'log'
            with patch('devflow.runtime.gh_environment',return_value={'GH_TOKEN':'private-token','HTTP_PROXY':'proxy'}), patch('devflow.runtime.subprocess.Popen',Process), patch('devflow.runtime.process_identity',return_value='birth'), patch('devflow.process_job.ProcessJob'):
                out=CommandRunner().run(['gh','api','user'],'.',log=log)
            self.assertNotIn('private-token',out)
            self.assertNotIn('private-token',log.read_text())
            self.assertEqual(environments[0]['HTTP_PROXY'],'proxy')
            self.assertNotIn('HTTP_PROXY',environments[1])
class BranchPolicyTests(unittest.TestCase):
    def test_unprotected_branch_local_head_review_suffices(self):
        pr={'headRefOid':'h','reviewDecision':None,'statusCheckRollup':[{'status':'COMPLETED','conclusion':'SUCCESS'}]}
        self.assertTrue(publish_gate('h',pr,'h',{'known':True,'required_reviews':False}))
        self.assertFalse(publish_gate('h',pr,'h',{'known':False,'required_reviews':False}))
        self.assertFalse(publish_gate('h',pr,'h',{'known':True,'required_reviews':True}))
        self.assertFalse(publish_gate('h',pr,'stale',{'known':True,'required_reviews':False}))
    def test_protection_policy_and_ruleset_combined(self):
        from devflow.runtime import branch_review_policy
        class Fake(CommandRunner):
            def __init__(self,rule): super().__init__(); self.rule=rule
            def run(self,argv,cwd,**kwargs):
                if argv[1:3]==['repo','view']: return '{"nameWithOwner":"owner/repo"}'
                if argv[1:3]==['api','graphql']: return json.dumps({'data':{'repository':{'ref':{'branchProtectionRule':self.rule}}}})
                return '[]'
        self.assertEqual(branch_review_policy(Fake(None),'.','gh','main'),{'known':True,'required_reviews':False})
        self.assertEqual(branch_review_policy(Fake({'requiresApprovingReviews':True,'requiredApprovingReviewCount':1}),'.','gh','main'),{'known':True,'required_reviews':True})

class CapabilityTests(unittest.TestCase):
    def test_unknown_model_is_blocked_not_guessed(self):
        from devflow.capabilities import select_verified_model
        catalog=[{'model':'gpt-6.1-sol','supportedReasoningEfforts':[{'reasoningEffort':'max'}]}]
        with self.assertRaises(RuntimeError): select_verified_model('gpt-6-astra','high',catalog)
        with self.assertRaises(RuntimeError): select_verified_model('gpt-6-luna','medium',catalog)
    def test_missing_manifest_is_blocked_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            command=[sys.executable,'-m','devflow','--repo',str(Path(tmp)),'--state',str(Path(tmp).parent/'devflow-test-state'),'init','--manifest','missing.json']
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('blocked_spec',result.stderr)
class ModelPolicyTests(unittest.TestCase):
    def test_explicit_policy_effort_verified(self):
        from devflow.capabilities import validate_model_policy
        models=[{'model':'gpt-6.1-sol','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]
        policy={'levels':[{'model':'gpt-6.1-sol','effort':'high'}]*4}
        self.assertEqual(validate_model_policy(policy,models),[('gpt-6.1-sol','high',None)]*4)
        bad={'levels':[{'model':'gpt-6.1-sol','effort':'max'}]*4}
        with self.assertRaises(RuntimeError): validate_model_policy(bad,models)
    def test_blocked_init_retains_observed_capabilities(self):
        from devflow.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=root/'repo'; repo.mkdir(); subprocess.run(['git','init',str(repo)],capture_output=True,check=True)
            manifest=root/'tasks.json'; manifest.write_text(json.dumps({'version':1,'tasks':[{'id':'a','deps':[],'allowed_paths':['a/'],'tests':[[sys.executable,'-V']],'prompt':'a','risk':'low'}]}))
            with patch('devflow.capabilities.probe_cli',return_value={'command':'fake','models':[{'model':'available','supportedReasoningEfforts':[]}]}):
                self.assertEqual(main(['--repo',str(repo),'--state',str(root/'state'),'init','--manifest',str(manifest)]),1)
            store=Store(root/'state/state.db')
            self.assertEqual(store.meta('capabilities')['models'][0]['model'],'available')
            self.assertEqual(store.meta('control'),'blocked_capability')
            self.assertEqual(len(store.tasks()),1)
            self.assertEqual(store.task('a')['status'],'pending')
class AutostartTests(unittest.TestCase):
    def test_windows_launcher_plan_quotes_absolute_paths_without_registration(self):
        if os.name!='nt': self.skipTest('Windows scheduler script')
        with tempfile.TemporaryDirectory(prefix='devflow path ') as tmp:
            root=Path(tmp); repo=root/'repo'; repo.mkdir(); manifest=root/'tasks file.json'; manifest.write_text('{"version":1,"tasks":[]}')
            script=Path.cwd()/'devflow/install_autostart.ps1'
            # Plan validates paths only; fixtures must not depend on live CLI installs/auth.
            result=subprocess.run(['powershell','-NoProfile','-File',str(script),'-Mode','Plan','-Repo',str(repo),'-Python',sys.executable,'-Manifest',str(manifest),'-Codex',sys.executable,'-Gh',sys.executable,'-State',str(root/'outside state')],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            plan=json.loads(result.stdout)
            self.assertTrue(plan['TaskName'].startswith('YushuOS-Devflow-'))
            self.assertEqual(Path(plan['State']).resolve(),(root/'outside state').resolve())
            self.assertIn('"'+plan['State']+'"',plan['Arguments'])
            self.assertEqual(plan['MultipleInstances'],'IgnoreNew')

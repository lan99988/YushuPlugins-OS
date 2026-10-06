import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from devflow.engine import Store
from devflow.runtime import Coordinator, CommandRunner, changed_paths, git

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        self.git('init'); self.git('config','user.email','test@example.invalid'); self.git('config','user.name','Test')
        (self.repo/'README.md').write_text('initial'); self.git('add','.'); self.git('commit','-m','initial')
        self.store=Store(self.root/'state'/'state.db')
        self.store.initialize([{'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[[sys.executable,'-c','from pathlib import Path; assert Path("plugins/a/file.txt").read_text()=="ok"']],'risk':'low','prompt':'create file'}])
    def tearDown(self): self.tmp.cleanup()
    def git(self,*args): return subprocess.run(['git',*args],cwd=self.repo,capture_output=True,text=True,check=True).stdout.strip()
    def test_full_run_independent_review(self):
        class Fake(CommandRunner):
            def codex(self,worktree,prompt,model,effort,sandbox,schema,output,log,timeout=1800):
                if sandbox=='workspace-write':
                    path=Path(worktree)/'plugins/a/file.txt'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('ok')
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
        r=Capture(); result=r.codex('.', 'private prompt','gpt-6-astra','high','read-only','schema','report','log')
        self.assertEqual(r.kw['input'],'private prompt')
        self.assertNotIn('private prompt',r.argv)
        self.assertEqual(r.argv[r.argv.index('-m')+1],'gpt-6-astra')
        self.assertIn('model_reasoning_effort="high"',r.argv)
        self.assertEqual(result['usage']['input_tokens'],9)
    def test_cli_status_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'; manifest=Path(tmp)/'manifest.json'
            manifest.write_text(json.dumps({'version':1,'tasks':[{'id':'a','deps':[],'allowed_paths':['plugins/a/'],'tests':[[sys.executable,'-V']],'risk':'low','prompt':'work'}]}))
            repo=Path(tmp)/'repo'; repo.mkdir()
            subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
            command=[sys.executable,'-m','devflow','--repo',str(repo),'--state',str(state)]
            subprocess.run(command+['init','--manifest',str(manifest)],check=True,capture_output=True)
            subprocess.run(command+['pause'],check=True,capture_output=True)
            info=json.loads(subprocess.run(command+['status'],check=True,capture_output=True,text=True).stdout)
            self.assertEqual(info['control'],'paused')
            subprocess.run(command+['resume'],check=True,capture_output=True)
            self.assertEqual(Store(state/'state.db').meta('control'),'running')
    def test_shared_path_overlap(self):
        from devflow.runtime import overlaps
        self.assertTrue(overlaps(['plugins/a/'],['plugins/a/file.py']))
        self.assertFalse(overlaps(['plugins/a/'],['plugins/ab/']))
from unittest.mock import patch
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
        self.store.set_task('a',status='verified',head_sha=git(first,'rev-parse','HEAD'))
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
        self.store.set_task('a',head_sha=head,review_head=head,review_tree=git(worktree,'rev-parse','HEAD^{tree}'),review_report='review.json',status='verified')
        class FakeGh(CommandRunner):
            def __init__(self): super().__init__(); self.calls=[]
            def run(self,argv,cwd,**kwargs):
                self.calls.append(argv)
                if argv[1:3]==['pr','list']: return json.dumps([{'number':1,'url':'https://github.invalid/pull/1','headRefOid':head}])
                if argv[1:3]==['pr','view']:
                    if argv[-1]=='state,mergeCommit': return json.dumps({'state':'MERGED','mergeCommit':{'oid':head}})
                    return json.dumps({'headRefOid':head,'reviewDecision':'APPROVED','state':'OPEN','statusCheckRollup':[{'status':'COMPLETED','conclusion':'SUCCESS'}]})
                return ''
        fake=FakeGh(); worker.runner=fake; worker.publish_task('a')
        self.assertEqual(self.store.task('a')['status'],'published')
        merge=next(c for c in fake.calls if c[1:3]==['pr','merge']); self.assertIn('--match-head-commit',merge)
        subprocess.run(['git','worktree','remove','--force',str(worktree)],cwd=self.repo,check=True,capture_output=True)
class ProcessRecoveryTests(unittest.TestCase):
    def test_orphan_process_identity_recovered_without_touching_diff(self):
        from devflow.runtime import process_identity
        with tempfile.TemporaryDirectory() as tmp:
            process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
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
            with patch('devflow.runtime.gh_environment',return_value={'GH_TOKEN':'private-token','HTTP_PROXY':'proxy'}), patch('devflow.runtime.subprocess.Popen',Process), patch('devflow.runtime.process_identity',return_value='birth'):
                out=CommandRunner().run(['gh','api','user'],'.',log=log)
            self.assertNotIn('private-token',out)
            self.assertNotIn('private-token',log.read_text())
            self.assertEqual(environments[0]['HTTP_PROXY'],'proxy')
            self.assertNotIn('HTTP_PROXY',environments[1])

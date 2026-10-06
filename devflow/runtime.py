"""Single coordinator, fenced state transitions, isolated task processes."""
import concurrent.futures
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from .engine import REPORT_SCHEMA, allowed, digest, model_for, parse_report, publish_gate, resolved_risk

def gh_environment(credential_run=None):
    env=dict(os.environ)
    if not env.get('GH_TOKEN') and not env.get('GITHUB_TOKEN'):
        quiet=dict(env,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
        result=(credential_run or subprocess.run)(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,env=quiet,timeout=30)
        if result.returncode==0:
            for line in result.stdout.splitlines():
                if line.startswith('password='): env['GH_TOKEN']=line.split('=',1)[1]
    return env

def process_identity(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,int(pid))
        if not handle: return None
        try:
            creation,exit_time,kernel_time,user_time=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(x) for x in [creation,exit_time,kernel_time,user_time]]): return None
            return str((creation.dwHighDateTime<<32)|creation.dwLowDateTime)
        finally: kernel.CloseHandle(handle)
    try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError): return None

class CommandRunner:
    def __init__(self): self.processes={}; self.lock=threading.Lock(); self.registry=None
    def record_processes(self):
        if self.registry:
            self.registry.parent.mkdir(parents=True,exist_ok=True)
            temp=self.registry.with_suffix('.tmp')
            temp.write_text(json.dumps({str(pid):{'birth':birth} for pid,(process,birth) in self.processes.items()}),encoding='utf-8')
            temp.replace(self.registry)
    def recover_processes(self):
        if not self.registry or not self.registry.exists(): return
        records=json.loads(self.registry.read_text(encoding='utf-8'))
        for pid,record in records.items():
            if record.get('birth') and process_identity(int(pid))==record['birth']:
                if os.name=='nt': subprocess.run(['taskkill','/PID',pid,'/T','/F'],capture_output=True)
                else:
                    try: os.killpg(int(pid),signal.SIGKILL)
                    except ProcessLookupError: pass
        self.registry.write_text('{}',encoding='utf-8')
    def kill(self,p):
        if p.poll() is not None: return
        if os.name=='nt': subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True)
        else:
            try: os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError: pass
    def cancel(self):
        with self.lock:
            for p,birth in self.processes.values(): self.kill(p)
    def run(self,argv,cwd,input=None,timeout=1800,log=None,env=None,_proxy_retry=False):
        kwargs={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
        is_gh=Path(argv[0]).name.lower() in ('gh','gh.exe')
        if env is None and is_gh: env=gh_environment()
        p=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',env=env,**kwargs)
        with self.lock: self.processes[p.pid]=(p,process_identity(p.pid)); self.record_processes()
        try:
            try: out,err=p.communicate(input,timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill(p); out,err=p.communicate(); raise TimeoutError('process deadline exceeded')
            secret=(env or {}).get('GH_TOKEN') or (env or {}).get('GITHUB_TOKEN')
            if secret: out=out.replace(secret,'[REDACTED]'); err=err.replace(secret,'[REDACTED]')
            if is_gh and p.returncode and not _proxy_retry and any(x in (out+err).lower() for x in ('eof','connection reset','proxyconnect')):
                direct={k:v for k,v in env.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')}
                return self.run(argv,cwd,input,timeout,log,env=direct,_proxy_retry=True)
            if log: Path(log).write_text(out+'\nSTDERR:\n'+err,encoding='utf-8')
            if p.returncode: raise RuntimeError((err or out)[-12000:] or f'command exited {p.returncode}')
            return out
        finally:
            with self.lock: self.processes.pop(p.pid,None); self.record_processes()
    def codex(self,worktree,prompt,model,effort,sandbox,schema,output,log,timeout=1800):
        command=os.environ.get('DEVFLOW_CODEX','codex')
        argv=[command,'exec','--json','--output-schema',str(schema),'--output-last-message',str(output),'-m',model,'-c',f'model_reasoning_effort="{effort}"','--sandbox',sandbox,'-']
        out=self.run(argv,worktree,input=prompt,timeout=timeout,log=log); session=None; usage={}
        for line in out.splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            if event.get('type')=='thread.started': session=event.get('thread_id')
            if event.get('usage'): usage=event['usage']
        return {'session':session,'usage':usage}

def git(repo,*args):
    return subprocess.run(['git',*args],cwd=repo,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True).stdout.strip()
def changed_paths(repo,base):
    tracked=git(repo,'diff','--name-only','--no-renames',base).splitlines()
    untracked=git(repo,'ls-files','--others','--exclude-standard').splitlines()
    return sorted(set(tracked+untracked))
def overlaps(a,b):
    return any(x.rstrip('/')==y.rstrip('/') or (x.endswith('/') and y.startswith(x)) or (y.endswith('/') and x.startswith(y)) for x in a for y in b)
def tree_sha(repo):
    git(repo,'add','-A'); return git(repo,'write-tree')
def rules_sha(repo):
    return digest({str(p.relative_to(repo)):p.read_text(encoding='utf-8') for p in sorted(Path(repo).rglob('AGENTS.md')) if '.git' not in p.parts})

class Coordinator:
    def __init__(self,repo,store,workers=3,runner=None,publish='manual'):
        self.repo=Path(repo).resolve(); self.store=store; self.workers=max(1,min(3,workers)); self.runner=runner or CommandRunner(); self.publish=publish
        self.owner=uuid.uuid4().hex; self.fence=None; self.state=store.path.parent; self.state.mkdir(parents=True,exist_ok=True)
        self.runner.registry=self.state/'processes.json'
    def update(self,id,**values): self.store.set_task(id,fence=self.fence,**values)
    def check_scope(self,t,worktree):
        paths=changed_paths(worktree,t['base_sha'])
        bad=[p for p in paths if not allowed(p,t['allowed_paths'])]
        if bad: raise RuntimeError('out of scope: '+', '.join(bad))
        if not paths: raise RuntimeError('no implementation diff')
        return paths
    def worktree(self,t):
        if t.get('worktree'):
            if not Path(t['worktree']).is_dir(): raise RuntimeError('saved worktree missing; recovery requires inspection')
            return Path(t['worktree'])
        worktree=self.state/'worktrees'/t['id']; worktree.parent.mkdir(parents=True,exist_ok=True)
        base=git(self.repo,'rev-parse','HEAD'); branch='devflow/'+self.owner[:8]+'/'+t['id']
        git(self.repo,'worktree','add','-b',branch,str(worktree),base)
        self.update(t['id'],worktree=str(worktree),base_sha=base,branch=branch)
        for dep in t.get('deps',[]):
            dependency=self.store.task(dep)
            if dependency['status'] not in ('verified','published') or not dependency['head_sha']: raise RuntimeError('unverified dependency')
            git(worktree,'merge','--no-edit',dependency['head_sha'])
        self.update(t['id'],base_sha=git(worktree,'rev-parse','HEAD'))
        return worktree
    def execute(self,id):
        try:
            t=self.store.task(id); worktree=self.worktree(t); t=self.store.task(id)
            risk=resolved_risk(t.get('risk_resolved',t.get('risk','low')),t['allowed_paths'])
            model,effort=model_for(risk,t['level']); attempt=t['attempt']+1
            artifact=self.state/'evidence'/id/str(attempt); artifact.mkdir(parents=True,exist_ok=True)
            schema=artifact/'schema.json'; schema.write_text(json.dumps(REPORT_SCHEMA),encoding='utf-8')
            self.update(id,status='running',attempt=attempt,model=model,reasoning=effort,risk_resolved=risk)
            instructions='Only modify these allowed paths: '+json.dumps(t['allowed_paths'])+'. Do not commit, push, publish or make real App calls. Implement and test. Report outcome is advisory.\n'+t['prompt']
            try: result=self.runner.codex(worktree,instructions,model,effort,'workspace-write',schema,artifact/'implementation.json',artifact/'implementation.jsonl')
            except RuntimeError as exc:
                if model=='gpt-6-astra' and any(s in str(exc).lower() for s in ('model not found','model unavailable','unsupported model','not available')):
                    self.update(id,fallback_reason=str(exc),model='gpt-6.1-sol',reasoning='max')
                    result=self.runner.codex(worktree,instructions,'gpt-6.1-sol','max','workspace-write',schema,artifact/'implementation.json',artifact/'implementation-fallback.jsonl')
                else: raise
            self.update(id,session=result['session'],token_usage=result['usage'],status='validating')
            t=self.store.task(id); paths=self.check_scope(t,worktree)
            risk=resolved_risk(risk,paths); self.update(id,risk_resolved=risk)
            test_evidence=[]
            for index,command in enumerate(t['tests']):
                log=artifact/f'test-{index}.txt'; self.runner.run(command,worktree,log=log)
                test_evidence.append({'argv':command,'log':str(log),'sha':digest(log.read_text(encoding='utf-8'))})
            self.check_scope(t,worktree); tree=tree_sha(worktree); rules=rules_sha(worktree)
            self.update(id,status='reviewing',tree_sha=tree,rules_sha=rules,evidence=test_evidence)
            binding={'task_id':id,'tree_sha':tree,'contract_sha':t['contract_sha'],'rules_sha':rules}
            prompt='Review this task read-only. Independently inspect diff against '+t['base_sha']+', task contract, AGENTS rules and test evidence. Return pass only when correct and no findings.\nEVIDENCE_BINDING='+json.dumps(binding)+'\nTASK='+json.dumps(t)+'\nTEST_EVIDENCE='+json.dumps(test_evidence)
            review_model,review_effort=model_for(risk,max(2,t['level']))
            try: review=self.runner.codex(worktree,prompt,review_model,review_effort,'read-only',schema,artifact/'review.json',artifact/'review.jsonl')
            except RuntimeError as exc:
                if review_model=='gpt-6-astra' and any(s in str(exc).lower() for s in ('model not found','model unavailable','unsupported model','not available')):
                    self.update(id,review_fallback_reason=str(exc))
                    review=self.runner.codex(worktree,prompt,'gpt-6.1-sol','max','read-only',schema,artifact/'review.json',artifact/'review-fallback.jsonl')
                else: raise
            report=parse_report((artifact/'review.json').read_text(encoding='utf-8'),id,tree,t['contract_sha'],rules)
            if report['outcome']!='pass' or report['findings']: raise RuntimeError('review failed: '+report['summary']+' '+json.dumps(report['findings']))
            if tree_sha(worktree)!=tree or rules_sha(worktree)!=rules: raise RuntimeError('review drift')
            self.check_scope(t,worktree)
            git(worktree,'commit','-m',f'devflow: {id}')
            head=git(worktree,'rev-parse','HEAD')
            self.update(id,status='verified',head_sha=head,review_head=head,review_tree=tree,review_session=review['session'],review_usage=review['usage'],review_report=str(artifact/'review.json'))
            if self.publish=='auto' and t.get('publish',{}).get('enabled'):
                try: self.publish_task(id)
                except Exception as exc: self.update(id,status='awaiting_gates',error=str(exc))
        except Exception as exc:
            if self.store.meta('control')=='stopped': self.update(id,status='interrupted',error='stopped; worktree retained')
            else: self.store.failure(id,str(exc),fence=self.fence)
    def publish_task(self,id):
        t=self.store.task(id); worktree=Path(t['worktree']); head=git(worktree,'rev-parse','HEAD')
        if head!=t['head_sha'] or git(worktree,'status','--porcelain') or git(worktree,'rev-parse','HEAD^{tree}')!=t['review_tree']: raise RuntimeError('publish drift')
        self.update(id,status='publishing')
        gh=os.environ.get('DEVFLOW_GH','gh'); branch=t['branch']; base=t.get('publish',{}).get('base','main')
        self.runner.run(['git','push','-u','origin',branch],worktree)
        existing=json.loads(self.runner.run([gh,'pr','list','--head',branch,'--state','all','--json','number,url,headRefOid,state'],worktree))
        if existing:
            pr=existing[0]
            if pr['headRefOid']!=head: raise RuntimeError('PR head mismatch')
        else:
            body=self.state/'evidence'/id/'pr-body.md'; body.write_text(f'Task {id}\n\nValidated tree: {t["review_tree"]}\n\nIndependent review: {t["review_report"]}',encoding='utf-8')
            url=self.runner.run([gh,'pr','create','--base',base,'--head',branch,'--title',f'devflow: {id}','--body-file',str(body)],worktree).strip()
            pr={'url':url}
        self.update(id,pr=pr,status='awaiting_gates')
        data=json.loads(self.runner.run([gh,'pr','view',pr.get('url',str(pr.get('number'))),'--json','headRefOid,reviewDecision,statusCheckRollup,state'],worktree))
        if not publish_gate(head,data,t['review_head']): return
        if data['state']!='MERGED': self.runner.run([gh,'pr','merge',pr.get('url',str(pr.get('number'))),'--merge','--match-head-commit',head],worktree)
        confirmed=json.loads(self.runner.run([gh,'pr','view',pr.get('url',str(pr.get('number'))),'--json','state,mergeCommit'],worktree))
        if confirmed['state']!='MERGED': return
        release=t.get('publish',{}).get('release_tag')
        if release:
            merged=confirmed['mergeCommit']['oid']
            tags=self.runner.run(['git','ls-remote','--tags','origin','refs/tags/'+release],worktree).strip()
            if tags and tags.split()[0]!=merged: raise RuntimeError('release tag target mismatch')
            if not tags:
                self.runner.run(['git','fetch','origin',merged],worktree)
                local=git(worktree,'tag','--list',release)
                if local:
                    if git(worktree,'rev-parse',release+'^{commit}')!=merged: raise RuntimeError('local release tag mismatch')
                else: git(worktree,'tag',release,merged)
                self.runner.run(['git','push','origin','refs/tags/'+release],worktree)
            releases=json.loads(self.runner.run([gh,'release','list','--limit','100','--json','tagName'],worktree))
            if not any(r['tagName']==release for r in releases): self.runner.run([gh,'release','create',release,'--verify-tag','--generate-notes'],worktree)
        self.update(id,status='published')
    def run(self,watch=False):
        self.fence=self.store.lease(self.owner)
        finished=threading.Event(); lost=[]
        def heartbeat():
            while not finished.wait(5):
                try:
                    renewed=self.store.lease(self.owner)
                    if renewed!=self.fence: raise RuntimeError('lease fencing changed')
                    if self.store.meta('control')=='stopped': self.runner.cancel()
                except Exception as exc:
                    lost.append(exc); self.runner.cancel(); return
        thread=threading.Thread(target=heartbeat,daemon=True); thread.start()
        try: self._run(watch,lost)
        finally:
            finished.set(); thread.join(timeout=6); self.store.release(self.owner,self.fence)
    def _run(self,watch=False,lost=None):
        self.fence=self.store.lease(self.owner); self.runner.recover_processes(); self.store.recover(fence=self.fence)
        active={}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            while True:
                if lost: raise RuntimeError('coordinator lease lost') from lost[0]
                self.fence=self.store.lease(self.owner)
                control=self.store.meta('control','running')
                if control=='stopped': self.runner.cancel()
                for future,id in list(active.items()):
                    if future.done(): future.result(); del active[future]
                if control=='running':
                    if self.publish=='auto':
                        for t in self.store.tasks():
                            if t['status'] in ('awaiting_gates','verified') and t.get('publish',{}).get('enabled'):
                                try: self.publish_task(t['id'])
                                except Exception as exc: self.update(t['id'],status='awaiting_gates',error=str(exc))
                    for t in self.store.ready():
                        if len(active)>=self.workers: break
                        if any(overlaps(t['allowed_paths'],self.store.task(id)['allowed_paths']) for id in active.values()): continue
                        # Mark before submission so there is exactly one coordinator reservation.
                        self.update(t['id'],status='running'); active[pool.submit(self.execute,t['id'])]=t['id']
                if not active and (not watch or control=='stopped'): break
                time.sleep(1 if active else 5)

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
from .security import non_gh_environment, github_secrets, redact
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

def branch_review_policy(runner,repo,gh,base):
    try:
        identity=json.loads(runner.run([gh,'repo','view','--json','nameWithOwner'],repo))['nameWithOwner']
        owner,name=identity.split('/',1)
        query='query($owner:String!,$name:String!,$ref:String!){repository(owner:$owner,name:$name){ref(qualifiedName:$ref){branchProtectionRule{requiresApprovingReviews requiredApprovingReviewCount}}}}'
        answer=json.loads(runner.run([gh,'api','graphql','-f','query='+query,'-f','owner='+owner,'-f','name='+name,'-f','ref=refs/heads/'+base],repo))
        if answer.get('errors'): return {'known':False,'required_reviews':True}
        ref=answer['data']['repository']['ref']
        if ref is None: return {'known':False,'required_reviews':True}
        protection=ref['branchProtectionRule']
        required=bool(protection and (protection['requiresApprovingReviews'] or protection['requiredApprovingReviewCount']))
        # Legacy branch protection and repository/org rulesets are separate gates.
        rules=json.loads(runner.run([gh,'api',f'repos/{identity}/rules/branches/{base}'],repo))
        if not isinstance(rules,list): return {'known':False,'required_reviews':True}
        required=required or any(rule.get('type')=='pull_request' and rule.get('parameters',{}).get('required_approving_review_count',0)>0 for rule in rules)
        return {'known':True,'required_reviews':bool(required)}
    except (RuntimeError,ValueError,KeyError,TypeError): return {'known':False,'required_reviews':True}

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
                if os.name=='nt': subprocess.run(['taskkill','/PID',pid,'/T','/F'],capture_output=True,env=non_gh_environment())
                else:
                    try: os.killpg(int(pid),signal.SIGKILL)
                    except ProcessLookupError: pass
        self.registry.write_text('{}',encoding='utf-8')
    def kill(self,p):
        if p.poll() is not None: return
        if os.name=='nt': subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,env=non_gh_environment())
        else:
            try: os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError: pass
    def cancel(self):
        with self.lock:
            for p,birth in self.processes.values(): self.kill(p)
    def run(self,argv,cwd,input=None,timeout=1800,log=None,env=None,_proxy_retry=False):
        kwargs={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
        is_gh=Path(argv[0]).name.lower() in ('gh','gh.exe')
        original_env=dict(os.environ if env is None else env)
        env=gh_environment() if is_gh and env is None else (dict(env) if is_gh else non_gh_environment(original_env))
        secrets=github_secrets(os.environ,original_env,env)
        p=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',env=env,**kwargs)
        with self.lock: self.processes[p.pid]=(p,process_identity(p.pid)); self.record_processes()
        try:
            timed_out=False
            try: out,err=p.communicate(input,timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill(p); out,err=p.communicate(); timed_out=True
            out=redact(out,secrets); err=redact(err,secrets)
            if is_gh and p.returncode and not timed_out and not _proxy_retry and any(x in (out+err).lower() for x in ('eof','connection reset','proxyconnect')):
                direct={k:v for k,v in env.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')}
                return self.run(argv,cwd,input,timeout,log,env=direct,_proxy_retry=True)
            if log: Path(log).write_text(out+'\nSTDERR:\n'+err,encoding='utf-8')
            if timed_out:
                error=TimeoutError('process deadline exceeded'); error.output=out; error.stderr=err
                raise error
            if p.returncode:
                error=RuntimeError((err or out)[-12000:] or f'command exited {p.returncode}'); error.output=out; error.stderr=err
                raise error
            return out
        finally:
            with self.lock: self.processes.pop(p.pid,None); self.record_processes()
    def codex(self,worktree,prompt,model,effort,sandbox,schema,output,log,timeout=1800):
        from .capabilities import resolve_codex, ALLOWED_MODELS
        if model not in ALLOWED_MODELS or (model=='gpt-6-luna' and effort!='max'): raise RuntimeError('blocked_capability: model/effort not authorized')
        command=getattr(self,'codex_command',None) or resolve_codex()
        argv=[command,'exec','--json','--output-schema',str(schema),'--output-last-message',str(output),'-m',model,'-c',f'model_reasoning_effort="{effort}"','--sandbox',sandbox,'-']
        failure=None
        try: out=self.run(argv,worktree,input=prompt,timeout=timeout,log=log)
        except (RuntimeError,TimeoutError) as error: failure=error; out=getattr(error,'output','')
        session=None; usage={}; usage_events=[]
        for line in out.splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            if event.get('type')=='thread.started': session=event.get('thread_id')
            if event.get('usage'):
                usage_events.append(event['usage'])
                for key,value in event['usage'].items():
                    if isinstance(value,(int,float)): usage[key]=usage.get(key,0)+value
        result={'session':session,'usage':usage,'usage_events':usage_events,'outcome':'failed' if failure else 'completed'}
        if failure: failure.result=result; raise failure
        return result


def git(repo,*args):
    return subprocess.run(['git',*args],cwd=repo,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True,env=non_gh_environment()).stdout.strip()
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
        self.publisher_lock=threading.Lock()
        self.capabilities=store.meta('capabilities')
        if self.capabilities: self.runner.codex_command=self.capabilities['command']
    def desired_model(self,risk,level):
        policy=self.store.meta('models_policy')
        if not policy: return model_for(risk,level)
        index=min(3,{'low':0,'medium':1,'high':2,'critical':3}[risk]+level)
        item=policy['levels'][index]; return item['model'],item['effort']
    def verified_model(self,model,effort):
        if not self.capabilities: raise RuntimeError('blocked_capability: no verified CLI model catalog')
        from .capabilities import select_verified_model
        return select_verified_model(model,effort,self.capabilities['models'])
    def invoke_codex(self,id,attempt,phase,worktree,prompt,model,effort,sandbox,schema,output,log):
        try: result=self.runner.codex(worktree,prompt,model,effort,sandbox,schema,output,log)
        except Exception as error:
            result=getattr(error,'result',{'session':None,'usage':{},'usage_events':[],'outcome':'failed','error':str(error)})
            self.store.record_usage(id,attempt,phase,result,model,effort,fence=self.fence)
            raise
        self.store.record_usage(id,attempt,phase,result,model,effort,fence=self.fence)
        return result
    def update(self,id,**values): self.store.set_task(id,fence=self.fence,**values)
    def check_scope(self,t,worktree):
        paths=changed_paths(worktree,t['base_sha'])
        bad=[p for p in paths if not allowed(p,t['allowed_paths'])]
        if bad: raise RuntimeError('out of scope: '+', '.join(bad))
        if not paths: raise RuntimeError('no implementation diff')
        return paths
    def dependency_snapshot(self,t):
        snapshot={}
        for id in t.get('deps',[]):
            dependency=self.store.task(id); head=dependency.get('head_sha')
            if dependency['status'] not in ('verified','published') or not head or dependency.get('review_head')!=head: raise RuntimeError('blocked_dependency: dependency review invalidated')
            if dependency.get('worktree') and Path(dependency['worktree']).is_dir():
                if git(dependency['worktree'],'rev-parse','HEAD')!=head or git(dependency['worktree'],'status','--porcelain'): raise RuntimeError('blocked_dependency: dependency checkout changed')
            tree=git(self.repo,'rev-parse',head+'^{tree}')
            if dependency.get('review_tree')!=tree: raise RuntimeError('blocked_dependency: dependency tree invalidated')
            snapshot[id]={'head_sha':head,'tree_sha':tree,'contract_sha':dependency['contract_sha'],'rules_sha':dependency.get('rules_sha')}
        return snapshot
    def check_dependencies(self,t):
        current=self.dependency_snapshot(t)
        if current!=t.get('dependency_evidence',{}): raise RuntimeError('blocked_dependency: dependency evidence changed; retained worktree requires reconciliation')
    def worktree(self,t):
        if t.get('worktree'):
            self.check_dependencies(t)
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
        self.update(t['id'],base_sha=git(worktree,'rev-parse','HEAD'),dependency_evidence=self.dependency_snapshot(t))
        return worktree
    def execute(self,id):
        try:
            t=self.store.task(id); worktree=self.worktree(t); t=self.store.task(id)
            risk=resolved_risk(t.get('risk_resolved',t.get('risk','low')),t['allowed_paths'])
            model,effort=self.desired_model(risk,t['level']); model,effort,fallback=self.verified_model(model,effort); attempt=t['attempt']+1
            if fallback: self.update(id,fallback_reason=fallback)
            artifact=self.state/'evidence'/id/str(attempt); artifact.mkdir(parents=True,exist_ok=True)
            schema=artifact/'schema.json'; schema.write_text(json.dumps(REPORT_SCHEMA),encoding='utf-8')
            self.update(id,status='running',attempt=attempt,model=model,reasoning=effort,risk_resolved=risk,session_strategy='fresh_retry' if attempt>1 else 'fresh_start',prior_session=t.get('session'))
            instructions='Only modify these allowed paths: '+json.dumps(t['allowed_paths'])+'. Do not commit, push, publish or make real App calls. Implement and test. Report outcome is advisory.\n'+t['prompt']
            failure_context={'last_failure':t.get('last_failure'),'evidence':t.get('evidence',[]),'prior_attempt':t.get('attempt'),'review_report':t.get('review_report')}
            failure_context['log_tails']=[]
            for evidence in t.get('evidence',[]):
                log_path=Path(evidence.get('log','')).resolve()
                if self.state in log_path.parents and log_path.is_file(): failure_context['log_tails'].append(log_path.read_text(encoding='utf-8')[-12000:])
            instructions+='\nREPAIR_EVIDENCE='+json.dumps(failure_context)
            instructions+='\nIMPLEMENTATION_CONTRACT='+json.dumps({'task_id':id,'contract_sha':t['contract_sha'],'rules_sha':rules_sha(worktree)})+'\nReturn strict schema report with tree_sha equal to git write-tree after staging your final changes. Retry uses a fresh session with retained worktree and supplied failure evidence.'
            result=self.invoke_codex(id,attempt,'implementation',worktree,instructions,model,effort,'workspace-write',schema,artifact/'implementation.json',artifact/'implementation.jsonl')
            self.update(id,session=result['session'],status='validating')
            t=self.store.task(id); paths=self.check_scope(t,worktree)
            risk=resolved_risk(risk,paths); self.update(id,risk_resolved=risk)
            try: implementation=parse_report((artifact/'implementation.json').read_text(encoding='utf-8'),id,tree_sha(worktree),t['contract_sha'],rules_sha(worktree))
            except (ValueError,OSError) as error: raise RuntimeError('implementation report invalid: '+str(error)) from error
            if implementation['outcome']!='pass' or implementation['findings']: raise RuntimeError('implementation report failed: '+implementation['summary'])
            test_evidence=[]; validated_tree=tree_sha(worktree)
            for index,command in enumerate(t['tests']):
                if tree_sha(worktree)!=validated_tree: raise RuntimeError('test tree drift before test')
                log=artifact/f'test-{index}.txt'
                passed=False
                try:
                    self.runner.run(command,worktree,log=log); passed=True
                finally:
                    after_tree=tree_sha(worktree)
                    evidence={'argv':command,'log':str(log),'sha':digest(log.read_text(encoding='utf-8')) if log.exists() else None,'tree_sha':validated_tree,'after_tree_sha':after_tree,'passed':passed}
                    test_evidence.append(evidence); self.update(id,evidence=test_evidence)
                    if after_tree!=validated_tree: raise RuntimeError('test tree drift after test')
            self.check_scope(t,worktree); tree=tree_sha(worktree); rules=rules_sha(worktree)
            self.update(id,status='reviewing',tree_sha=tree,rules_sha=rules,evidence=test_evidence)
            binding={'task_id':id,'tree_sha':tree,'contract_sha':t['contract_sha'],'rules_sha':rules}
            prompt='Review this task read-only. Independently inspect diff against '+t['base_sha']+', task contract, AGENTS rules and test evidence. Return pass only when correct and no findings.\nEVIDENCE_BINDING='+json.dumps(binding)+'\nTASK='+json.dumps(t)+'\nTEST_EVIDENCE='+json.dumps(test_evidence)
            review_model,review_effort=self.desired_model(risk,max(2,t['level']))
            review_model,review_effort,review_fallback=self.verified_model(review_model,review_effort)
            if review_fallback: self.update(id,review_fallback_reason=review_fallback)
            review=self.invoke_codex(id,attempt,'review',worktree,prompt,review_model,review_effort,'read-only',schema,artifact/'review.json',artifact/'review.jsonl')
            report=parse_report((artifact/'review.json').read_text(encoding='utf-8'),id,tree,t['contract_sha'],rules)
            if report['outcome']!='pass' or report['findings']: raise RuntimeError('review failed: '+report['summary']+' '+json.dumps(report['findings']))
            if tree_sha(worktree)!=tree or rules_sha(worktree)!=rules: raise RuntimeError('review drift')
            self.check_scope(t,worktree)
            git(worktree,'commit','-m',f'devflow: {id}')
            head=git(worktree,'rev-parse','HEAD')
            self.update(id,status='verified',head_sha=head,review_head=head,review_tree=tree,review_session=review['session'],review_usage=review['usage'],review_report=str(artifact/'review.json'))
        except Exception as exc:
            if self.store.meta('control')=='stopped': self.update(id,status='interrupted',error='stopped; worktree retained')
            else: self.store.failure(id,exc,fence=self.fence)
    def publish_task(self,id):
        with self.publisher_lock: return self._publish_task(id)
    def _publish_task(self,id):
        if self.store.task(id)['status']=='published': return
        t=self.store.task(id); self.check_dependencies(t); worktree=Path(t['worktree']); head=git(worktree,'rev-parse','HEAD')
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
        policy=branch_review_policy(self.runner,worktree,gh,base)
        self.update(id,branch_policy=policy)
        if not publish_gate(head,data,t['review_head'],policy): return
        if data['state']!='MERGED': self.runner.run([gh,'pr','merge',pr.get('url',str(pr.get('number'))),'--merge','--match-head-commit',head],worktree)
        confirmed=json.loads(self.runner.run([gh,'pr','view',pr.get('url',str(pr.get('number'))),'--json','state,mergeCommit'],worktree))
        if confirmed['state']!='MERGED': return
        release=t.get('publish',{}).get('release_tag')
        if release:
            merged=confirmed['mergeCommit']['oid']
            self.runner.run(['git','fetch','origin',merged],worktree)
            from .release import build_release_assets, ensure_release_assets
            assets=build_release_assets(worktree,merged,release,self.state/'releases'/merged,t,self.runner)
            self.update(id,merge_sha=merged,release_assets=[str(p) for p in assets])
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
            if not any(r['tagName']==release for r in releases): self.runner.run([gh,'release','create',release,*[str(p) for p in assets],'--verify-tag','--generate-notes'],worktree)
            ensure_release_assets(self.runner,worktree,gh,release,assets)
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
                for task in self.store.tasks():
                    if task['status'] in ('verified','awaiting_gates'):
                        try: self.check_dependencies(task)
                        except RuntimeError as error: self.update(task['id'],status='blocked_dependency',error=str(error))
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

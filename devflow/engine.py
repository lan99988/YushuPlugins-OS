from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path, PurePosixPath

MODELS=[('gpt-6-luna','max'),('gpt-6.1-sol','medium'),('gpt-6.1-sol','high'),('gpt-6.1-sol','max')]
FIELDS={'task_id','tree_sha','contract_sha','rules_sha','outcome','summary','tests','findings'}
REPORT_SCHEMA={'type':'object','additionalProperties':False,'required':sorted(FIELDS),'properties':{**{k:{'type':'string'} for k in ['task_id','tree_sha','contract_sha','rules_sha','summary']},'outcome':{'type':'string','enum':['pass','fail']},'tests':{'type':'array','items':{'type':'string'}},'findings':{'type':'array','items':{'type':'string'}}}}
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def model_for(risk,level): return MODELS[min(3, {'low':0,'medium':1,'high':2,'critical':3}[risk]+level)]
def resolved_risk(risk,paths):
    sensitive=('auth','secret','migration','publish','core','permission')
    return 'critical' if any(any(s in p.lower() for s in sensitive) for p in paths) else risk
def allowed(path,patterns):
    path=path.replace('\\','/')
    if path.startswith('/') or ':' in path or '..' in PurePosixPath(path).parts: return False
    return any(path==p.rstrip('/') or (p.endswith('/') and path.startswith(p)) for p in patterns)
def parse_report(raw,task,tree,contract,rules):
    value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!=FIELDS: raise ValueError('report fields')
    if any(not isinstance(value[k],str) for k in FIELDS-{'tests','findings'}): raise ValueError('report strings')
    if any(not isinstance(value[k],list) or any(not isinstance(x,str) for x in value[k]) for k in ['tests','findings']): raise ValueError('report arrays')
    if (value['task_id'],value['tree_sha'],value['contract_sha'],value['rules_sha'])!=(task,tree,contract,rules): raise ValueError('stale evidence')
    if value['outcome'] not in ('pass','fail'): raise ValueError('report outcome')
    return value

def validate_manifest(tasks):
    ids={t['id'] for t in tasks}
    if len(ids)!=len(tasks): raise ValueError('duplicate task')
    for t in tasks:
        if t.get('kind','implement') not in ('implement','verify'): raise ValueError('task kind')
        if t.get('kind')=='verify' and t.get('publish',{}).get('enabled'): raise ValueError('verification tasks cannot publish')
        if not t['id'].replace('-','').replace('_','').isalnum(): raise ValueError('unsafe id')
        if not t.get('allowed_paths') or any(not allowed(p,t['allowed_paths']) for p in t['allowed_paths']): raise ValueError('unsafe allowlist')
        if not t.get('tests') or any(not isinstance(c,list) or not c or any(not isinstance(x,str) for x in c) for c in t['tests']): raise ValueError('tests argv required')
        if t.get('risk','low') not in ('low','medium','high','critical'): raise ValueError('risk')
        if not set(t.get('deps',[]))<=ids: raise ValueError('missing dependency')
    seen=set()
    while len(seen)<len(ids):
        ready={t['id'] for t in tasks if set(t.get('deps',[]))<=seen}-seen
        if not ready: raise ValueError('cyclic dependency')
        seen|=ready

def publish_gate(head,pr,review_head,policy=None):
    policy={'known':True,'required_reviews':True} if policy is None else policy
    reviews_ok=policy.get('known') and (not policy.get('required_reviews') or pr.get('reviewDecision')=='APPROVED')
    checks=pr.get('statusCheckRollup') or []
    return bool(head==review_head==pr.get('headRefOid') and reviews_ok and checks and all((c.get('conclusion')=='SUCCESS' and c.get('status')=='COMPLETED') or c.get('state')=='SUCCESS' for c in checks))

def failure_kind(error):
    explicit=getattr(error,'failure_kind',None)
    if explicit: return explicit
    text=str(error).lower()
    if 'blocked_dependency:' in text: return 'dependency'
    if 'blocked_capability:' in text or 'not supported with a chatgpt account' in text or 'not supported with your chatgpt account' in text or re.search(r'model.{0,200}not supported.{0,200}chatgpt account',text): return 'capability'
    if any(s in text for s in ('authentication failed','not logged in','login required','http 401','http 403','invalid api key')): return 'auth'
    if any(s in text for s in ('quota exceeded','rate limit','usage limit','insufficient_quota')): return 'quota'
    if isinstance(error,TimeoutError) or any(s in text for s in ('network error','connection refused','connection reset','transport eof','proxyconnect','timed out','process deadline exceeded')): return 'network'
    return 'validation'

class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,at REAL,task TEXT,data TEXT);')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db: yield db
        finally: db.close()
    def meta(self,key,default=None):
        with self.connect() as db: row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set_meta(self,key,value):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,json.dumps(value)))
    def control(self,value):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('control',json.dumps(value)))
    def initialize(self,tasks):
        validate_manifest(tasks)
        with self.connect() as db:
            for t in tasks:
                old=db.execute('SELECT data FROM tasks WHERE id=?',(t['id'],)).fetchone()
                if old:
                    if json.loads(old[0])['contract_sha']!=digest(t): raise ValueError('manifest drift; use a new state directory')
                    continue
                data=dict(t,status='pending',attempt=0,level=0,next_run=0,contract_sha=digest(t),base_sha=None,tree_sha=None,head_sha=None,worktree=None,session=None,token_usage={},usage_records=[],evidence=[],pr=None)
                db.execute('INSERT INTO tasks VALUES (?,?)',(t['id'],json.dumps(data)))
    def tasks(self):
        with self.connect() as db: return [json.loads(r[0]) for r in db.execute('SELECT data FROM tasks ORDER BY id')]
    def task(self,id): return next(t for t in self.tasks() if t['id']==id)
    def lease(self,owner,now=None,ttl=90):
        now=time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM meta WHERE key='lease'").fetchone(); old=json.loads(row[0]) if row else {}
            if old.get('until',0)>now and old.get('owner')!=owner: raise RuntimeError('coordinator lease held')
            fence=old.get('fence',0)+(old.get('owner')!=owner or old.get('until',0)<=now)
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('lease',json.dumps({'owner':owner,'until':now+ttl,'fence':fence})))
            return fence
    def release(self,owner,fence):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM meta WHERE key='lease'").fetchone()
            lease=json.loads(row[0]) if row else {}
            if lease.get('owner')==owner and lease.get('fence')==fence:
                lease['until']=0
                db.execute('UPDATE meta SET value=? WHERE key=?',(json.dumps(lease),'lease'))
    def set_task(self,id,fence=None,**values):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if fence is not None:
                lease=json.loads(db.execute("SELECT value FROM meta WHERE key='lease'").fetchone()[0])
                if lease['fence']!=fence: raise RuntimeError('stale coordinator')
            row=db.execute('SELECT data FROM tasks WHERE id=?',(id,)).fetchone(); data=json.loads(row[0]); data.update(values)
            db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data),id))
            db.execute('INSERT INTO events(at,task,data) VALUES (?,?,?)',(time.time(),id,json.dumps(values)))
    def record_usage(self,id,attempt,phase,result,model,effort,fence=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if fence is not None:
                lease=json.loads(db.execute("SELECT value FROM meta WHERE key='lease'").fetchone()[0])
                if lease['fence']!=fence: raise RuntimeError('stale coordinator')
            data=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(id,)).fetchone()[0])
            record={'attempt':attempt,'phase':phase,'session':result.get('session'),'usage':result.get('usage',{}),'model':model,'effort':effort,'usage_events':result.get('usage_events',[]),'outcome':result.get('outcome','completed')}
            records=data.setdefault('usage_records',[])
            records.append(record)
            totals={}
            for entry in records:
                for key,value in entry['usage'].items():
                    if isinstance(value,(int,float)): totals[key]=totals.get(key,0)+value
            data['token_usage']=totals
            db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data),id))
            db.execute('INSERT INTO events(at,task,data) VALUES (?,?,?)',(time.time(),id,json.dumps({'usage_record':record})))
    def ready(self,now=None):
        now=time.time() if now is None else now; tasks=self.tasks(); done={t['id'] for t in tasks if t['status'] in ('verified','published')}
        return [t for t in tasks if t['status'] in ('pending','retry','waiting_network','waiting_quota') and t['next_run']<=now and set(t.get('deps',[]))<=done]
    def recover(self,fence=None):
        for t in self.tasks():
            if t['status']=='publishing' and t.get('head_sha'): self.set_task(t['id'],fence=fence,status='awaiting_gates',error='publish restart; head retained')
            elif t['status'] in ('running','reviewing','validating','publishing'): self.set_task(t['id'],fence=fence,status='interrupted',error='coordinator restarted; diff retained; resume required')
    def failure(self,id,error,now=None,fence=None):
        now=time.time() if now is None else now; t=self.task(id); kind=failure_kind(error); error=str(error); attempt=t['attempt']; level=t['level']
        repeated=t.get('last_failure')==error
        status='retry'; delay=0
        if kind=='capability': status='blocked_capability'
        elif kind=='dependency': status='blocked_dependency'
        elif kind=='auth': status='blocked_auth'
        elif kind=='quota':
            status='waiting_quota'; delay=1800 if t.get('quota_waits',0)==0 else 3600
            reset=re.search(r'resets?At[\"\s:=]+(\d{9,}|\d+)',error,re.IGNORECASE)
            if reset: delay=max(0,float(reset.group(1))-now)
        elif kind=='network': status='waiting_network'; delay=min(900,30*2**min(attempt,5))
        elif t.get('kind')=='verify': status='blocked'
        elif attempt>=4: status='blocked'
        else: level=min(3,level+1) if repeated or attempt>=2 else level
        self.set_task(id,fence=fence,status=status,next_run=now+delay,level=level,error=error,failure_kind=kind,last_failure=error,quota_waits=t.get('quota_waits',0)+(status=='waiting_quota'))

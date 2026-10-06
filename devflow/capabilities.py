"""Probe the installed CLI protocol without making an agent turn or App write."""
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from .security import non_gh_environment

REQUIRED_FLAGS=['--json','--output-schema','--output-last-message','--model','--config','--sandbox']
def resolve_codex():
    configured=os.environ.get('DEVFLOW_CODEX')
    source=shutil.which(configured or 'codex') or configured
    if not source: raise RuntimeError('blocked_capability: Codex CLI not found')
    path=Path(source).resolve()
    if os.name=='nt' and path.suffix.lower() in ('.cmd','.ps1'):
        native=list((path.parent/'node_modules/@openai/codex/node_modules/@openai').glob('codex-win32-*/vendor/*/bin/codex.exe'))
        if len(native)==1: path=native[0].resolve()
        else: raise RuntimeError('blocked_capability: configure DEVFLOW_CODEX with absolute native codex.exe')
    return str(path)
ALLOWED_MODELS={'gpt-6-luna','gpt-6.1-sol'}
DEFAULT_POLICY={'levels':[{'model':m,'effort':e} for m,e in [('gpt-6-luna','max'),('gpt-6.1-sol','medium'),('gpt-6.1-sol','high'),('gpt-6.1-sol','max')]]}

def select_verified_model(model,effort,catalog,fallback=None):
    if model not in ALLOWED_MODELS or (model=='gpt-6-luna' and effort!='max'): raise RuntimeError('blocked_capability: model/effort not authorized')
    supported={item['model']:{x['reasoningEffort'] for x in item.get('supportedReasoningEfforts',[])} for item in catalog}
    if model in supported:
        if effort not in supported[model]: raise RuntimeError(f'blocked_capability: {model}/{effort} unsupported by CLI model/list')
        return model,effort,None
    raise RuntimeError(f'blocked_capability: {model} unavailable in CLI model/list')
def validate_model_policy(policy,catalog):
    if not isinstance(policy,dict) or set(policy)-{'levels'} or len(policy.get('levels',[]))!=4: raise RuntimeError('blocked_capability: model policy must define exactly four levels')
    selected=[]
    for index,item in enumerate(policy['levels']):
        if not isinstance(item,dict) or set(item)!={'model','effort'} or any(not isinstance(item[k],str) or not item[k] for k in ['model','effort']): raise RuntimeError('blocked_capability: model policy fields')
        selected.append(select_verified_model(item['model'],item['effort'],catalog))
    return selected
def probe_catalog(command,timeout=30,contained=False):
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else ({} if contained else {'start_new_session':True})
    process=subprocess.Popen([command,'app-server','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=non_gh_environment(),**options)
    responses=queue.Queue()
    def read():
        for line in process.stdout:
            try: responses.put(json.loads(line))
            except ValueError: pass
    threading.Thread(target=read,daemon=True).start()
    def request(id,method,params):
        process.stdin.write(json.dumps({'id':id,'method':method,'params':params})+'\n'); process.stdin.flush()
        import time
        deadline=time.monotonic()+timeout
        while True:
            try: message=responses.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty: raise RuntimeError('blocked_capability: CLI model/list timed out')
            if message.get('id')==id:
                if 'error' in message: raise RuntimeError('blocked_capability: CLI model/list error '+json.dumps(message['error']))
                return message['result']
            if time.monotonic()>=deadline: raise RuntimeError('blocked_capability: CLI protocol timeout')
    try:
        request(1,'initialize',{'clientInfo':{'name':'devflow','version':'1.0'}})
        process.stdin.write('{"method":"initialized"}\n'); process.stdin.flush()
        catalog=[]; cursor=None; id=2
        while True:
            params={'includeHidden':True,'limit':100}
            if cursor: params['cursor']=cursor
            result=request(id,'model/list',params); catalog.extend(result['data']); cursor=result.get('nextCursor'); id+=1
            if not cursor: return catalog
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close()
def probe_cli(runner,repo,contained=False):
    command=resolve_codex()
    help_text=runner.run([command,'--help'],repo,timeout=30)
    exec_help=runner.run([command,'exec','--help'],repo,timeout=30)
    if 'app-server' not in help_text or any(flag not in exec_help for flag in REQUIRED_FLAGS): raise RuntimeError('blocked_capability: installed CLI lacks required protocol or flags')
    catalog=probe_catalog(command,contained=contained)
    return {'command':command,'version':runner.run([command,'--version'],repo,timeout=30).strip(),'models':catalog}

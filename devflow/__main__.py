import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from .engine import Store
from .runtime import Coordinator, CommandRunner, git

def default_state(repo):
    root=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/state'))) if os.name=='nt' else Path.home()/'.local/state'
    return root/'yushuos-devflow'/hashlib.sha256(str(repo.resolve()).casefold().encode()).hexdigest()[:16]
def main(argv=None):
    parser=argparse.ArgumentParser(description='持久化 Codex 开发队列；状态、日志和工作树保存在仓库外')
    parser.add_argument('--repo',default='.')
    parser.add_argument('--state')
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('init'); init.add_argument('--manifest',default='devflow/tasks.json'); init.add_argument('--models-policy')
    run=commands.add_parser('run'); run.add_argument('--watch',action='store_true'); run.add_argument('--workers',type=int,choices=[1,2,3],default=3); run.add_argument('--publish',choices=['auto','manual'],default='manual')
    for name in ['status','pause','stop','report']: commands.add_parser(name)
    resume=commands.add_parser('resume'); resume.add_argument('--task')
    args=parser.parse_args(argv); repo=Path(args.repo).resolve(); state=Path(args.state).resolve() if args.state else default_state(repo)
    if state==repo or repo in state.parents: parser.error('state directory must be outside repository')
    store=Store(state/'state.db')
    try:
        if args.command=='init':
            path=Path(args.manifest); path=path if path.is_absolute() else repo/path
            if not path.is_file():
                store.control('blocked_spec'); store.set_meta('initialization_error','manifest missing: '+str(path))
                raise ValueError('blocked_spec: manifest missing: '+str(path))
            manifest=json.loads(path.read_text(encoding='utf-8-sig'))
            if manifest.get('version')!=1: raise ValueError('manifest version must be 1')
            if git(repo,'status','--porcelain'): raise ValueError('initial repository must be clean')
            if not manifest.get('tasks'):
                store.control('blocked_spec'); store.set_meta('initialization_error','manifest tasks must not be empty')
                raise ValueError('blocked_spec: manifest tasks must not be empty')
            store.initialize(manifest['tasks'])
            from .capabilities import probe_cli, validate_model_policy, DEFAULT_POLICY
            try:
                capabilities=probe_cli(CommandRunner(),repo)
                store.set_meta('capabilities',capabilities)
                policy=json.loads(Path(args.models_policy).resolve().read_text(encoding='utf-8-sig')) if args.models_policy else DEFAULT_POLICY
                store.set_meta('models_policy',policy)
                selected=validate_model_policy(policy,capabilities['models'])
                capabilities['selected']=selected; store.set_meta('capabilities',capabilities)
            except (RuntimeError,ValueError,OSError) as exc:
                store.control('blocked_capability'); store.set_meta('initialization_error',str(exc)); raise
            store.control('running'); store.set_meta('initialization_error',None)
            print(json.dumps({'initialized':len(store.tasks()),'state':str(state)}))
        elif args.command=='run':
            if not store.tasks(): raise ValueError('blocked_spec: initialize a nonempty task manifest first')
            if store.meta('control')=='blocked_capability' or not store.meta('capabilities'): raise ValueError('blocked_capability: initialize CLI capability evidence first')
            from .capabilities import validate_model_policy, DEFAULT_POLICY
            validate_model_policy(store.meta('models_policy',DEFAULT_POLICY),store.meta('capabilities')['models'])
            Coordinator(repo,store,args.workers,publish=args.publish).run(args.watch)
            print(json.dumps({'tasks':store.tasks()},ensure_ascii=False))
        elif args.command in ('pause','stop'): store.control('paused' if args.command=='pause' else 'stopped'); print(args.command)
        elif args.command=='resume':
            for task in store.tasks():
                if (not args.task or task['id']==args.task) and task['status'] in ('interrupted','blocked_auth'):
                    store.set_task(task['id'],status='retry',next_run=0)
            store.control('running'); print('resumed')
        else: print(json.dumps({'control':store.meta('control','running'),'state':str(state),'capabilities':store.meta('capabilities'),'models_policy':store.meta('models_policy'),'initialization_error':store.meta('initialization_error'),'tasks':store.tasks()},ensure_ascii=False,indent=2))
        return 0
    except (ValueError,RuntimeError,OSError) as exc:
        if str(exc).startswith('blocked_capability:'): store.control('blocked_capability'); store.set_meta('initialization_error',str(exc))
        elif str(exc).startswith('blocked_spec:'): store.control('blocked_spec'); store.set_meta('initialization_error',str(exc))
        print(str(exc),file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())

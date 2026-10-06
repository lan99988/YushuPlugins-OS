import argparse
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from .engine import Store
from .runtime import Coordinator, CommandRunner, git

CAPABILITY_RETRY_SECONDS = 30 * 60
CONTROL_POLL_SECONDS = 5


class CapabilityProbe:
    """Only CLI help/model-list requests; completion never updates queue state."""
    def __init__(self, repo):
        self.repo = repo
        self.runner = CommandRunner()
        self.done = threading.Event()
        self.result = None

    def __enter__(self):
        def probe():
            try:
                # Contain help and app-server under one ordinary child process,
                # so cancellation can terminate its complete process tree.
                code = '''import sys
sys.path.insert(0, sys.argv[1])
from devflow.probe_worker import main
raise SystemExit(main([sys.argv[2]]))
'''
                output = self.runner.run([sys.executable, '-c', code,
                                          str(Path(__file__).resolve().parent.parent), str(self.repo)],
                                         self.repo, timeout=180)
                self.result = (json.loads(output), None)
            except Exception as error:
                self.result = (None, error)
            finally:
                self.done.set()
        threading.Thread(target=probe, daemon=True).start()
        return self

    def poll(self):
        return self.result if self.done.is_set() else None

    def __exit__(self, *args):
        # Do not join a protocol timeout while responding to stop. Repeat
        # cancellation to cover exit racing with the child being registered.
        if not self.done.is_set():
            self.runner.cancel()
            def cancel():
                while not self.done.wait(.1):
                    self.runner.cancel()
                self.runner.cancel()
            threading.Thread(target=cancel, daemon=True).start()


def capability_error(error):
    message = str(error)
    return message if message.startswith('blocked_capability:') else 'blocked_capability: ' + message


def save_capability_result(store, capabilities, error, now):
    """Publish evidence and control together, without overwriting pause/stop."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT value FROM meta WHERE key='control'").fetchone()
        control = json.loads(row[0]) if row else 'running'
        row = db.execute("SELECT value FROM meta WHERE key='capability_watch'").fetchone()
        watching = json.loads(row[0]) if row else {}
        watching.update(waiting=error is not None, probing=False, last_probe_at=now,
                        next_probe_at=now + CAPABILITY_RETRY_SECONDS if error else None)
        values = {'capability_watch': watching, 'initialization_error': capability_error(error) if error else None}
        if capabilities is not None:
            values['capabilities'] = capabilities
        if control in ('running', 'blocked_capability'):
            values['control'] = 'blocked_capability' if error else 'running'
        for key, value in values.items():
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))


def ensure_capabilities(repo, store, watch):
    from .capabilities import validate_model_policy, DEFAULT_POLICY
    control = store.meta('control', 'running')
    if watch and control in ('stopped', 'blocked_spec'):
        return False
    capabilities = store.meta('capabilities')
    waiting = store.meta('capability_watch', {}).get('waiting', False)
    error = store.meta('initialization_error')
    blocked = control == 'blocked_capability' or waiting or bool(error) or not capabilities
    if not blocked:
        try:
            validate_model_policy(store.meta('models_policy', DEFAULT_POLICY), capabilities['models'])
            return True
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            error = exc
            save_capability_result(store, None, exc, time.time())
    if not watch:
        raise ValueError(capability_error(error or 'initialize CLI capability evidence first'))
    watching = store.meta('capability_watch', {})
    if watching.get('next_probe_at') is None:
        watching.update(waiting=True, probing=False, next_probe_at=time.time() + CAPABILITY_RETRY_SECONDS)
        store.set_meta('capability_watch', watching)
    # Waiting here is ordinary program polling. No Coordinator exists yet.
    while True:
        control = store.meta('control', 'running')
        if control in ('stopped', 'blocked_spec'):
            return False
        if not store.meta('capability_watch', {}).get('waiting', True):
            if control == 'running':
                return True
        elif control != 'paused' and time.time() >= store.meta('capability_watch')['next_probe_at']:
            started = time.time()
            watching = store.meta('capability_watch')
            watching.update(probing=True, next_probe_at=started + CAPABILITY_RETRY_SECONDS)
            store.set_meta('capability_watch', watching)
            with CapabilityProbe(repo) as probe:
                while True:
                    if store.meta('control') in ('stopped', 'blocked_spec'):
                        watching['probing'] = False
                        store.set_meta('capability_watch', watching)
                        return False
                    result = probe.poll()
                    if result is not None:
                        capabilities, error = result
                        if error is None:
                            try:
                                capabilities['selected'] = validate_model_policy(
                                    store.meta('models_policy', DEFAULT_POLICY), capabilities['models'])
                            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                                error = exc
                        save_capability_result(store, capabilities, error, started)
                        break
                    time.sleep(CONTROL_POLL_SECONDS)
            continue
        time.sleep(CONTROL_POLL_SECONDS)

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
            capabilities = None
            store.set_meta('models_policy',None)
            try:
                policy=json.loads(Path(args.models_policy).resolve().read_text(encoding='utf-8-sig')) if args.models_policy else DEFAULT_POLICY
                store.set_meta('models_policy',policy)
                capabilities=probe_cli(CommandRunner(),repo)
                selected=validate_model_policy(policy,capabilities['models'])
                capabilities['selected']=selected
            except (RuntimeError,ValueError,OSError) as exc:
                save_capability_result(store,capabilities,exc,time.time())
                store.control('blocked_capability')
                raise ValueError(capability_error(exc)) from exc
            save_capability_result(store,capabilities,None,time.time())
            store.control('running')
            print(json.dumps({'initialized':len(store.tasks()),'state':str(state)}))
        elif args.command=='run':
            if not store.tasks(): raise ValueError('blocked_spec: initialize a nonempty task manifest first')
            if ensure_capabilities(repo,store,args.watch):
                Coordinator(repo,store,args.workers,publish=args.publish).run(args.watch)
            print(json.dumps({'tasks':store.tasks()},ensure_ascii=False))
        elif args.command in ('pause','stop'): store.control('paused' if args.command=='pause' else 'stopped'); print(args.command)
        elif args.command=='resume':
            for task in store.tasks():
                if (not args.task or task['id']==args.task) and task['status'] in ('interrupted','blocked_auth'):
                    store.set_task(task['id'],status='retry',next_run=0)
            capability_pending = (store.meta('capability_watch',{}).get('waiting',False)
                                  or bool(store.meta('initialization_error')) or not store.meta('capabilities'))
            store.control('blocked_capability' if capability_pending else 'running'); print('resumed')
        else: print(json.dumps({'control':store.meta('control','running'),'state':str(state),'capabilities':store.meta('capabilities'),'models_policy':store.meta('models_policy'),'initialization_error':store.meta('initialization_error'),'capability_watch':store.meta('capability_watch'),'tasks':store.tasks()},ensure_ascii=False,indent=2))
        return 0
    except (ValueError,RuntimeError,OSError) as exc:
        if str(exc).startswith('blocked_capability:'):
            if store.meta('control') not in ('paused','stopped','blocked_spec'): store.control('blocked_capability')
            store.set_meta('initialization_error',str(exc))
        elif str(exc).startswith('blocked_spec:'): store.control('blocked_spec'); store.set_meta('initialization_error',str(exc))
        print(str(exc),file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())

"""Release assets are built from the confirmed merge commit, never a stale worker tree."""
import hashlib
import json
import sys
from pathlib import Path
from .security import non_gh_environment

def file_sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def build_release_assets(repo,merged,tag,directory,task,runner):
    from .runtime import git, tree_sha, changed_paths
    from .engine import allowed
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    if git(repo,'rev-parse',merged+'^{commit}')!=merged: raise RuntimeError('release merge SHA must be exact')
    checkout=directory/'merge-worktree'
    if checkout.is_symlink() or checkout.resolve()!=directory/'merge-worktree': raise RuntimeError('release worktree target escapes its release directory')
    if checkout.exists():
        if git(checkout,'rev-parse','HEAD')!=merged or git(checkout,'status','--porcelain'): raise RuntimeError('release checkout drift')
    else: git(repo,'worktree','add','--detach',str(checkout),merged)
    try:
        tree=git(checkout,'rev-parse','HEAD^{tree}')
        assets=[]
        for index,command in enumerate(task.get('publish',{}).get('build_commands',[])):
            if not isinstance(command,list) or not command or any(not isinstance(x,str) for x in command): raise RuntimeError('release build command must be argv')
            argv=[x.replace('{output_dir}',str(directory)).replace('{python}',sys.executable) for x in command]
            runner.run(argv,checkout,log=directory/f'build-{index}.log',env=dict(non_gh_environment(),DEVFLOW_RELEASE_DIR=str(directory)))
            writes=changed_paths(checkout,merged)
            permitted=task.get('publish',{}).get('build_write_paths',[])
            if any(not allowed(path,permitted) for path in writes) or git(checkout,'rev-parse','HEAD')!=merged: raise RuntimeError('release build source drift')
        for name in task.get('publish',{}).get('assets',[]):
            path=(directory/name).resolve()
            if directory not in path.parents or not path.is_file(): raise RuntimeError('release asset missing/outside release directory')
            assets.append(path)
        if task.get('publish',{}).get('build_commands'):
            if (directory/'SHA256SUMS').exists(): (directory/'SHA256SUMS').replace(directory/'suite-SHA256SUMS')
            assets.extend(p for p in sorted(directory.iterdir()) if p.is_file() and (p.suffix=='.zip' or p.name in ('suite-manifest.json','suite-SHA256SUMS')) and p not in assets and not p.name.startswith('source-'))
        if len({p.name for p in assets})!=len(assets): raise RuntimeError('duplicate release asset names')
        archive=directory/f'source-{merged[:12]}.zip'
        runner.run(['git','archive','--format=zip','-o',str(archive),merged],checkout)
        assets.append(archive)
        sources=checkout/'sources.lock.json'
        source_lock=json.loads(sources.read_text(encoding='utf-8-sig')) if sources.is_file() else None
        lock={'version':1,'release_tag':tag,'merge_sha':merged,'merge_tree':tree,'build_tree':tree_sha(checkout),'contract_sha':task['contract_sha'],'sources_lock':source_lock,'assets':{p.name:file_sha(p) for p in assets}}
        lock_path=directory/'release.lock.json'; canonical=json.dumps(lock,sort_keys=True,indent=2)+'\n'
        if lock_path.exists() and lock_path.read_text(encoding='utf-8')!=canonical: raise RuntimeError('release lock or asset hash drift')
        lock_path.write_text(canonical,encoding='utf-8'); assets.append(lock_path)
        sums=directory/'SHA256SUMS'; sums.write_text(''.join(f'{file_sha(p)}  {p.name}\n' for p in sorted(assets)),encoding='utf-8'); assets.append(sums)
        return assets
    finally: git(repo,'worktree','remove','--force',str(checkout))

def ensure_release_assets(runner,repo,gh,tag,assets):
    """Reuse equal remote assets; refuse replacement of a mismatched published asset."""
    release=json.loads(runner.run([gh,'release','view',tag,'--json','assets'],repo))
    existing={asset['name']:asset for asset in release.get('assets',[])}
    for path in assets:
        asset=existing.get(path.name)
        if asset:
            # GitHub supplies digest on current upload APIs; otherwise download and hash.
            digest=asset.get('digest')
            if digest:
                if digest!='sha256:'+file_sha(path): raise RuntimeError('remote release asset hash mismatch: '+path.name)
            else:
                verification=path.parent/'remote-verification'; verification.mkdir(exist_ok=True)
                target=verification/path.name
                if target.exists(): target.unlink()
                runner.run([gh,'release','download',tag,'--pattern',path.name,'--dir',str(verification)],repo)
                if not target.is_file() or file_sha(target)!=file_sha(path): raise RuntimeError('remote release asset hash mismatch: '+path.name)
        else: runner.run([gh,'release','upload',tag,str(path)],repo)

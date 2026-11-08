"""Promote an already built static export locally. No network or model execution."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import tempfile


def publish(build: Path, root: Path):
    build=build.resolve();root=root.resolve()
    manifest_path=build/'data/manifest.json'
    manifest=json.loads(manifest_path.read_text())
    allowed_roots={'index.html','assets','data','papers','methodology','THIRD-PARTY-LICENSES.txt'}
    if any(path.name not in allowed_roots for path in build.iterdir()):
        raise ValueError('Unexpected build root, possibly a private backup')
    actual={str(path.relative_to(build/'data')) for path in (build/'data').rglob('*') if path.is_file()}
    if actual != set(manifest['files']) | {'manifest.json'}:
        raise ValueError('Unmanifested public data')
    for relative in actual:
        if relative.endswith('.json'):
            data=json.loads((build/'data'/relative).read_text())
            papers=data.get('papers',[]) if relative=='catalog.json' else [data] if relative.startswith('papers/') else []
            if any('experiments' in (paper.get('extraction') or {}) for paper in papers):
                raise ValueError('Legacy experimental rows cannot be published')
    for relative, expected in manifest['files'].items():
        p=build/'data'/relative
        if not p.resolve().is_relative_to(build/'data') or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('Export integrity failure')
    for path in build.rglob('*'):
        if path.is_symlink() or path.name.startswith('.'):
            raise ValueError('Build contains a symlink or hidden file')
    if not (build/'index.html').is_file() or not (build/'methodology/index.html').is_file():
        raise ValueError('Incomplete site build')
    releases=root/'releases';releases.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(b''.join(str(p.relative_to(build)).encode()+b'\0'+hashlib.sha256(p.read_bytes()).digest()
                                    for p in sorted(build.rglob('*')) if p.is_file())).hexdigest()[:20]
    release=releases/identity
    if not release.exists():
        stage=Path(tempfile.mkdtemp(prefix='.release-',dir=releases))
        shutil.copytree(build,stage,dirs_exist_ok=True)
        stage.rename(release)
    link=root/'published'
    if link.exists() and not link.is_symlink():raise ValueError('Publication must be a symlink')
    previous=link.resolve() if link.exists() else None
    temp=root/'published.next'
    if temp.is_symlink():temp.unlink()
    temp.symlink_to(release, target_is_directory=True);os.replace(temp,link)
    return {'release':identity,'previous':previous.name if previous else None,'papers':manifest['coverage']['papers'],'extracted':manifest['coverage']['extracted']}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path,default=Path('site/dist'));p.add_argument('--root',type=Path,default=Path('runtime'))
    a=p.parse_args();print(json.dumps(publish(a.build,a.root)))

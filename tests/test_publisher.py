import hashlib
import gzip
import importlib.util
import json
from pathlib import Path
import pytest
from jepa.store import Store
from jepa.sources import record
from jepa.export import export_catalog

spec=importlib.util.spec_from_file_location('publisher',Path(__file__).resolve().parents[1]/'scripts/publish.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)


def build(tmp_path, catalog=None):
    root=tmp_path/'build';(root/'data').mkdir(parents=True);(root/'methodology').mkdir()
    (root/'index.html').write_text('catalogue');(root/'methodology/index.html').write_text('methodology')
    data=json.dumps(catalog or {'papers':[]}).encode();(root/'data/catalog.json').write_bytes(data)
    (root/'data/manifest.json').write_text(json.dumps({'files':{'catalog.json':hashlib.sha256(data).hexdigest()},'coverage':{'papers':0,'extracted':0}}))
    return root


def test_reject_backup_tree(tmp_path):
    root=build(tmp_path);(root/'data.previous').mkdir()
    with pytest.raises(ValueError,match='Unexpected build root'):publisher.publish(root,tmp_path/'private')


def test_reject_unmanifested_data(tmp_path):
    root=build(tmp_path);(root/'data/unlisted.json').write_text('{}')
    with pytest.raises(ValueError,match='Unmanifested'):publisher.publish(root,tmp_path/'private')


def test_reject_legacy_rows_even_when_manifested(tmp_path):
    root=build(tmp_path,{'papers':[{'extraction':{'experiments':[{'value':'42'}]}}]})
    with pytest.raises(ValueError,match='Legacy experimental'):publisher.publish(root,tmp_path/'private')


def test_valid_atomic_release(tmp_path):
    root=build(tmp_path);result=publisher.publish(root,tmp_path/'private')
    assert (tmp_path/'private/published').is_symlink()
    assert (tmp_path/'private/published/index.html').read_text()=='catalogue'
    assert result['previous'] is None


def test_export_backups_never_enter_static_public_directory(tmp_path):
    store=Store(tmp_path/'private');out=tmp_path/'site/public/data'
    store.ingest(record('arxiv','2401.00001',arxiv_id='2401.00001',title='Paper',url='https://arxiv.org/abs/2401.00001'))
    export_catalog(store,out);export_catalog(store,out)
    assert not (out.parent/'data.previous').exists()
    assert len(list((store.root/'export-backups').iterdir()))==1
    store.close()


def test_precompressed_release_is_deterministic(tmp_path):
    root=build(tmp_path)
    (root/'index.html').write_bytes(b'catalogue' * 200)
    (root/'robots.txt').write_bytes(b'robots' * 200)
    (root/'sitemap.xml').write_bytes(b'<url/>' * 200)
    manifest=(root/'data/manifest.json').read_bytes()
    first=publisher.publish(root,tmp_path/'private')
    release=tmp_path/'private/releases'/first['release']
    for name in ('index.html','robots.txt','sitemap.xml'):
        assert gzip.decompress((release/'.http'/f'{name}.gz').read_bytes()) == (root/name).read_bytes()
    assert (release/'data/manifest.json').read_bytes() == manifest
    assert not (root/'.http').exists()
    assert publisher.publish(root,tmp_path/'private')['release'] == first['release']


@pytest.mark.parametrize('kind', ['symlink', 'hidden', 'unmanifested', 'traversal', 'private'])
def test_reject_before_reading_unsafe_content(tmp_path, monkeypatch, kind):
    root=build(tmp_path)
    secret=tmp_path/'secret';secret.write_text('private')
    if kind == 'symlink':
        (root/'data/escape.json').symlink_to(secret)
    elif kind == 'hidden':
        (root/'data/.secret').write_text('private')
    elif kind == 'unmanifested':
        (root/'data/unlisted.json').write_text('private')
    elif kind == 'private':
        (root/'.http').mkdir();(root/'.http/private').write_text('private')
    else:
        manifest=root/'data/manifest.json'
        data=json.loads(manifest.read_text());data['files']['../../secret']='bad'
        manifest.write_text(json.dumps(data))
    original_text=Path.read_text;original_bytes=Path.read_bytes
    def checked_text(path, *args, **kwargs):
        assert path == root/'data/manifest.json', f'unexpected read: {path}'
        return original_text(path, *args, **kwargs)
    def checked_bytes(path, *args, **kwargs):
        raise AssertionError(f'unexpected read: {path}')
    monkeypatch.setattr(Path,'read_text',checked_text)
    monkeypatch.setattr(Path,'read_bytes',checked_bytes)
    with pytest.raises(ValueError):publisher.publish(root,tmp_path/'private')


def test_stage_cleaned_on_compression_error(tmp_path, monkeypatch):
    root=build(tmp_path);(root/'index.html').write_bytes(b'x'*600)
    def fail(*args, **kwargs):
        raise RuntimeError('compression failed')
    monkeypatch.setattr(publisher.gzip,'compress',fail)
    with pytest.raises(RuntimeError):publisher.publish(root,tmp_path/'private')
    assert not list((tmp_path/'private/releases').iterdir())

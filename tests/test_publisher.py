import hashlib
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

import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
from jepa.local_graph import import_graph
from jepa.store import Store


def source(path):
    c=sqlite3.connect(path);c.execute('CREATE TABLE index_snapshots(id INTEGER PRIMARY KEY,paper_key TEXT,payload TEXT,observed_at REAL)')
    items=[{'id':'W1','title':'JEPA one','authors':[{'name':'A'}],
            'locations':[{'landing_page_url':'https://arxiv.org/abs/2401.00001v2'}],
            'referenced_works':['W2']},
           {'id':'W2','title':'World model two','source_arxiv_ids':['2401.00001'],'authors':[]}]
    for i,p in enumerate(items):c.execute('INSERT INTO index_snapshots VALUES (?,?,?,?)',(i+1,'openalex:'+p['id'],json.dumps(p),i))
    c.commit();c.close()


def test_readonly_metadata_and_edges_without_hint_merge(tmp_path):
    path=tmp_path/'retained.sqlite3';source(path);before=hashlib.sha256(path.read_bytes()).digest()
    store=Store(tmp_path/'new-project');report=import_graph(store,path)
    assert report['network_requests']==0 and report['records_imported']==2
    assert hashlib.sha256(path.read_bytes()).digest()==before
    assert store.stats()['works']==2 and store.stats()['citation_edges']==1
    papers=store.papers()
    assert next(p for p in papers if p['title']=='World model two')['arxiv_id'] is None
    assert next(p for p in papers if p['title']=='JEPA one')['version']=='v2'
    assert import_graph(store,path)['new_provider_identities']==0
    assert store.stats()['works']==2
    store.close()


def test_reject_uncheckpointed_source(tmp_path):
    path=tmp_path/'retained.sqlite3';source(path);Path(str(path)+'-wal').write_bytes(b'pending')
    store=Store(tmp_path/'new-project')
    with pytest.raises(ValueError,match='uncheckpointed'):import_graph(store,path)
    assert not store.papers();store.close()

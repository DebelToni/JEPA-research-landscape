import json
import os
import time
import pytest
from jepa import cli
from jepa.store import Store
from jepa.sources import record
from jepa.export import export_catalog


def add(store, n=1, version='v1'):
    wid = store.ingest(record('arxiv', f'2401.{n:05d}', arxiv_id=f'2401.{n:05d}', version=version,
                               title='Paper', authors=['A'], url=f'https://arxiv.org/abs/2401.{n:05d}{version}'))
    with store.db: store.db.execute("UPDATE works SET relevance='direct' WHERE work_id=?",(wid,))
    return wid


def fake_parse(p, root, timeout):
    return {'version': p['version']}


def fake_extract(p, doc, root, timeout):
    from jepa import extract
    extract.call_model({}, 1, {})
    time.sleep(.05)
    return {'processing': {}, 'evidence': [{'id': 'e'}], 'completeness': 'partial', 'relevance': 'direct'}


def test_process_parallel_target_and_resume(tmp_path, monkeypatch):
    s = Store(tmp_path)
    backlog = add(s)
    s.enqueue(backlog, 'process')
    targets = [add(s, n) for n in range(2, 6)]
    monkeypatch.setattr(cli, 'acquire_and_parse', fake_parse)
    monkeypatch.setattr(cli, 'extract_paper', fake_extract)
    from jepa import extract
    monkeypatch.setattr(extract, 'call_model', lambda *a: {'usage': {'input': 7}, 'text': 'private'})
    receipt = cli.process(s, 3, ids=targets, workers=2)
    assert len(receipt['events']) == 4
    assert len(receipt['calls']) == 4
    assert all(c == {'usage': {'input': 7}} for c in receipt['calls'])
    assert s.get(backlog)['status'] == 'discovered'
    assert s.db.execute('select attempts from jobs where work_id=?', (backlog,)).fetchone()[0] == 0
    s.close()
    s = Store(tmp_path)
    receipt = cli.process(s, 3, ids=targets, workers=2)
    assert len(receipt['events']) == 4
    assert all(s.get(w)['status'] == 'partial' for w in targets)


def test_deadline_preserves_previous(tmp_path, monkeypatch):
    s = Store(tmp_path)
    w = add(s)
    s.set_extraction(w, {'summary': 'good', 'relevance': 'direct'})
    s.set_status(w, 'partial')
    monkeypatch.setattr(cli, 'acquire_and_parse', fake_parse)
    monkeypatch.setattr(cli, 'extract_paper', lambda *a: time.sleep(10))
    start = time.monotonic()
    r = cli.process(s, .7, workers=2)
    assert time.monotonic()-start < 1
    assert r['events'][0]['status'] == 'interrupted'
    assert s.get(w)['extraction']['summary'] == 'good'
    assert s.get(w)['status'] == 'partial'
    assert s.stats()['jobs'] == {'pending': 1}
    with pytest.raises(ValueError):
        cli.process(s, 28801)


def test_claim_fences_current_and_targets(tmp_path):
    s = Store(tmp_path)
    w = add(s)
    s.enqueue(w, 'process')
    add(s, version='v2')
    assert s.claim('process') is None
    s.enqueue(w, 'process')
    ids = [s.db.execute('select max(id) from jobs').fetchone()[0]]
    assert s.claim('process', job_ids=[]) is None
    assert s.claim('process', job_ids=ids)['manifestation_id'] == s._manifestation(w)['id']


def test_export_holds_conflicting_identity_for_review(tmp_path):
    s=Store(tmp_path/'private')
    s.ingest(record('arxiv','2401.00001',arxiv_id='2401.00001',doi='10.1234/a',title='A',url='https://arxiv.org/abs/2401.00001'))
    s.ingest(record('arxiv','2401.00002',arxiv_id='2401.00002',doi='10.1234/b',title='B',url='https://arxiv.org/abs/2401.00002'))
    conflict=s.ingest(record('openalex','W1',arxiv_id='2401.00001',doi='10.1234/b',title='Conflicting provider assertion',url='https://openalex.org/W1'))
    out=tmp_path/'public';manifest=export_catalog(s,out)
    assert manifest['coverage']['identity_conflicts_pending']==1
    assert conflict not in {p['work_id'] for p in json.loads((out/'catalog.json').read_text())['papers']}
    assert s.get(conflict)['title']=='Conflicting provider assertion'
    s.close()


def test_export_boundary_and_schema(tmp_path):
    from jepa.models import Extraction
    s = Store(tmp_path/'private')
    w = add(s)
    e = Extraction().model_dump()
    e['processing'] = {'secret': 'DO_NOT_EXPORT'}
    s.set_extraction(w, e)
    out = tmp_path/'public'
    export_catalog(s, out)
    text = (out/'catalog.json').read_text()
    assert 'DO_NOT_EXPORT' not in text and 'processing' not in text and 'quote' not in text
    schema = json.loads((out/'schema.json').read_text())
    assert 'processing' not in schema.get('required', [])
    assert 'quote' not in schema['$defs']['Evidence']['properties']
    s.db.execute("insert into source_cursors values ('arxiv', 'q', ?, 0)", (json.dumps({'secret':'DO_NOT_EXPORT'}),))
    s.db.commit()
    export_catalog(s, out)
    assert 'DO_NOT_EXPORT' not in (out/'catalog.json').read_text()
    text = (out/'catalog.json').read_text()
    s.db.execute('update source_records set record=json_set(record,\'$.title\',\'/home/private/key\')')
    s.db.commit()
    with pytest.raises(ValueError):
        export_catalog(s, out)
    assert (out/'catalog.json').read_text() == text


def test_fair_selection_and_worker_failure(tmp_path, monkeypatch):
    s = Store(tmp_path)
    first, second = add(s), add(s, 2)
    monkeypatch.setattr(cli, 'acquire_and_parse', fake_parse)
    monkeypatch.setattr(cli, 'extract_paper', fake_extract)
    from jepa import extract
    monkeypatch.setattr(extract, 'call_model', lambda *a: {})
    assert cli.process(s, 2, max_papers=1)['events'][0]['work_id'] == first
    assert cli.process(s, 2, max_papers=1)['events'][0]['work_id'] == second
    monkeypatch.setattr(cli, 'extract_paper', lambda *a: os._exit(3))
    r = cli.process(s, 2, ids=[first])
    assert r['events'][0]['error'] == 'WorkerExit'
    assert s.get(first)['status'] == 'partial'
    assert s.get(first)['extraction'] is not None


def test_local_discovery_filters_before_limit(tmp_path, monkeypatch):
    from argparse import Namespace
    source = tmp_path/'local.jsonl'
    source.write_text('\n'.join(json.dumps({'id': f'2401.{n:05d}', 'title': title,
        'authors': 'A', 'versions': [{'version': 'v1'}], 'update_date': '2024-01-01'})
        for n, title in [(1, 'unrelated'), (2, 'JEPA predictive')])+'\n')
    class Offline:
        def __init__(self, store): pass
        def close(self): pass
    monkeypatch.setattr(cli, 'HTTPSources', Offline)
    s = Store(tmp_path/'runtime')
    cli.discover(s, Namespace(source='local', file=source, query='JEPA', limit=1, hydrate=False))
    assert len(s.papers()) == 1
    assert s.papers()[0]['title'] == 'JEPA predictive'


def test_export_findings_and_private_legacy_rows(tmp_path):
    from jepa.models import Extraction
    s = Store(tmp_path/'private')
    w = add(s)
    e = Extraction().model_dump()
    claim = {'text': 'Interpretation of result', 'attribution': 'author', 'evidence': ['ev1']}
    result = {'id': 'selected', 'name': 'selected result', 'value': '42', 'evidence': ['ev1']}
    e['experiments'] = [{'id': 'legacy', 'name': 'PRIVATE_LEGACY_ROW', 'evidence': ['ev1']}]
    e['findings'] = [{'id': 'finding1', 'name': 'Finding one', 'interpretation': claim,
                      'key_results': [result], 'caveats': [dict(claim, text='A caveat')],
                      'evidence': ['ev1']}]
    e['evidence'] = [{'id': 'ev1', 'source_url': 'https://example.org/paper', 'locator': 'Table 1',
                      'quote': 'PRIVATE_QUOTE', 'artifact_sha256': 'a'*64, 'block_id': 'block'}]
    e['processing'] = {'secret': 'PRIVATE_PROCESSING'}
    s.set_extraction(w, e)
    out = tmp_path/'public'
    export_catalog(s, out)
    for path in [out/'catalog.json', out/'papers.jsonl', out/'papers'/f'{w}.json', out/'papers'/f'{w}.md', out/'schema.json']:
        text = path.read_text()
        assert 'PRIVATE_LEGACY_ROW' not in text
        assert 'PRIVATE_QUOTE' not in text
        assert 'PRIVATE_PROCESSING' not in text
        assert 'experiments' not in text
    for path in [out/'catalog.json', out/'papers.jsonl', out/'papers'/f'{w}.json', out/'papers'/f'{w}.md']:
        text = path.read_text()
        for expected in ('Interpretation of result', 'selected result', '42', 'A caveat', 'ev1'):
            assert expected in text
    schema = json.loads((out/'schema.json').read_text())
    assert 'findings' in schema['properties']
    assert 'experiments' not in schema['properties']
    assert 'processing' not in schema['properties']
    assert 'quote' not in schema['$defs']['Evidence']['properties']

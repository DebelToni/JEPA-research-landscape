import pytest
from jepa.store import Store, arxiv_identity, normalize_doi
from jepa.sources import record


def paper(aid='2401.00001', version='v1', **kw):
    return record('arxiv', aid + (version or ''), arxiv_id=aid, version=version,
                  title='A paper', authors=['A'], **kw)


def test_versions_and_revisions(tmp_path):
    s = Store(tmp_path)
    wid = s.ingest(paper())
    assert s.ingest(paper()) == wid
    s.set_extraction(wid, {'summary': 'first'})
    s.set_extraction(wid, {'summary': 'corrected'})
    s.set_extraction(wid, {'summary': 'corrected'})
    s.set_status(wid, 'extracted')
    assert s.ingest(paper(version='v2')) == wid
    assert s.get(wid)['extraction'] is None
    assert s.get(wid)['status'] == 'discovered'
    s.ingest(paper(version='v1', abstract='late update'))
    assert s.get(wid)['version'] == 'v2'
    assert s.stats()['extraction_revisions'] == 2
    assert s.stats()['manifestations'] == 2
    with pytest.raises(ValueError):
        s.set_extraction(wid, {'processing': {'version': 'v99'}})
    s.close()
    assert Store(tmp_path).get(wid)['version'] == 'v2'


def test_unknown_version_not_inferred(tmp_path):
    s = Store(tmp_path)
    wid = s.ingest(paper(version=None))
    assert s.get(wid)['version'] is None
    assert arxiv_identity('https://arxiv.org/abs/hep-th/9901001v2') == ('hep-th/9901001', 'v2')
    assert normalize_doi('https://doi.org/10.1000/ABC') == '10.1000/abc'


def test_conflicting_cross_ids_not_merged(tmp_path):
    s = Store(tmp_path)
    a = s.ingest(paper(doi='10.1000/a'))
    b = s.ingest(paper('2401.00002', doi='10.1000/b'))
    collision = paper('2401.00001', doi='10.1000/b')
    c = s.ingest(collision)
    assert len({a, b, c}) == 3
    assert s.ingest(collision) == c
    assert s.stats()['identity_conflicts'] == 1
    d = s.ingest(paper('2401.00003', doi='10.1000/a'))
    assert d != a
    assert s.stats()['identity_conflicts'] == 2


def test_no_title_match_and_no_metadata_erasure(tmp_path):
    s = Store(tmp_path)
    a = s.ingest(paper())
    assert s.ingest(paper('2401.00002')) != a
    s.ingest(record('curated', 'link1', arxiv_id='2401.00001', version='v1'))
    assert s.get(a)['title'] == 'A paper'


def test_jobs_leases_failure_retry_and_versions(tmp_path):
    s = Store(tmp_path)
    wid = s.ingest(paper())
    s.enqueue(wid, 'extract')
    s.enqueue(wid, 'extract')
    t = Store(tmp_path)
    j = s.claim('extract')
    assert t.claim('extract') is None
    s.finish(j['id'], 'classified failure')
    assert s.claim('extract') is None
    s.enqueue(wid, 'extract')
    j = t.claim('extract')
    t.finish(j['id'])
    with pytest.raises(ValueError):
        t.finish(j['id'])
    s.ingest(paper(version='v2'))
    s.enqueue(wid, 'extract')
    j2 = s.claim('extract')
    assert j2['manifestation_id'] != j['manifestation_id']
    with s.db:
        s.db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (j2['id'],))
    assert t.claim('extract')['attempts'] == 2


def test_invalid_record_atomic(tmp_path):
    s = Store(tmp_path)
    with pytest.raises(ValueError):
        s.ingest({'source': 'x'})
    assert s.stats()['works'] == 0


def test_stale_worker_fencing(tmp_path):
    s = Store(tmp_path)
    wid = s.ingest(paper())
    s.enqueue(wid, 'extract')
    old = s.claim('extract')
    with s.db:
        s.db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (old['id'],))
    new = s.claim('extract')
    with pytest.raises(ValueError):
        s.finish(old['id'], lease_until=old['lease_until'])
    s.finish(new['id'], lease_until=new['lease_until'])


def test_historical_extraction_is_not_promoted(tmp_path):
    s = Store(tmp_path)
    wid = s.ingest(paper())
    s.set_status(wid, 'failed')
    s.ingest(paper(version='v2'))
    s.set_extraction(wid, {'summary': 'old result', 'processing': {'version': 'v1'}})
    assert s.get(wid)['extraction'] is None
    assert s.get(wid)['status'] == 'discovered'
    assert s.db.execute('SELECT count(*) FROM extraction_revisions').fetchone()[0] == 1

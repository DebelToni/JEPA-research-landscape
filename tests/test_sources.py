import json
import httpx
import pytest
from jepa.store import Store
from jepa.sources import HTTPSources, import_arxiv_jsonl, record

ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>2</o:totalResults><entry><id>http://arxiv.org/abs/2401.00001v2</id><title>JEPA</title><summary>abstract</summary><author><name>A</name></author><published>2024-01-01T00:00:00Z</published><updated>2024-02-01T00:00:00Z</updated></entry></feed>'''


def adapter(tmp_path, handler, max_bytes=4_000_000):
    s = Store(tmp_path)
    h = HTTPSources(s, client=httpx.Client(transport=httpx.MockTransport(handler)), max_bytes=max_bytes)
    return s, h


def test_arxiv_cursor_resume(tmp_path, monkeypatch):
    monkeypatch.setattr('jepa.sources.time.sleep', lambda x: None)
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, text=ATOM)
    s, h = adapter(tmp_path, handler)
    assert h.arxiv_search('JEPA', 1).status == 'ok'
    h2 = HTTPSources(Store(tmp_path), client=h.client)
    assert h2.arxiv_search('JEPA', 1).status == 'ok'
    assert requests[1].url.params['start'] == '1'
    assert h2.arxiv_search('JEPA', 1).status == 'skipped'
    assert s.stats()['works'] == 1
    assert s.get(s.papers()[0]['work_id'])['version'] == 'v2'


def test_failure_no_cursor_and_no_exception_secrets(tmp_path):
    secret = 'DO-NOT-LEAK'
    def handler(req):
        raise httpx.ConnectError('secret ' + secret + str(req.url))
    s, h = adapter(tmp_path, handler)
    r = h.openalex_citations('W1', enabled=True, api_key=secret)
    assert r.status == 'failed'
    assert secret not in str(r)
    assert s.db.execute("SELECT count(*) FROM source_cursors WHERE source='openalex' AND query='W1'").fetchone()[0] == 0
    rows = str([tuple(x) for x in s.db.execute('SELECT * FROM quota_ledger')])
    assert secret not in rows
    assert 'api_key' not in rows


def test_openalex_quota_persistent_and_edges(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'results': [{'id': 'https://openalex.org/W2', 'title': 'Citer', 'doi': None, 'locations': []}], 'meta': {'next_cursor': 'next'}}, headers={'x-ratelimit-remaining': '2'})
    s, h = adapter(tmp_path, handler)
    seed = s.ingest(record('openalex', 'https://openalex.org/W1', url='https://openalex.org/W1'))
    assert h.openalex_citations('W1').status == 'skipped'
    assert len(calls) == 0
    assert h.openalex_citations('W1', enabled=True, api_key='secret', daily_limit=1).status == 'ok'
    assert s.stats()['citation_edges'] == 1
    assert s.db.execute('SELECT target FROM citation_edges').fetchone()[0] == seed
    other = HTTPSources(Store(tmp_path), client=h.client)
    assert other.openalex_citations('W1', enabled=True, api_key='secret', daily_limit=1).status == 'paused'
    assert len(calls) == 1
    with pytest.raises(ValueError):
        h.openalex_citations('W1', enabled=True, api_key='secret', daily_limit=21)


def test_openalex_free_balance_survives_zero_prepaid(tmp_path):
    s, h = adapter(tmp_path, lambda req: httpx.Response(200,
        json={'results': [], 'meta': {'next_cursor': None}},
        headers={'x-ratelimit-prepaid-remaining-usd':'0', 'x-ratelimit-onetime-remaining':'0',
                 'x-ratelimit-remaining':'9990', 'x-ratelimit-remaining-usd':'0.999'}))
    assert h.openalex_citations('W1', enabled=True, api_key='test').status == 'ok'
    assert h.openalex_citations('W2', enabled=True, api_key='test').status == 'ok'
    assert s.db.execute("SELECT count(*) FROM quota_ledger WHERE provider='openalex'").fetchone()[0] == 2


def test_openalex_resolution_requires_explicit_arxiv_link(tmp_path):
    item={'id':'https://openalex.org/W1','title':'JEPA','locations':[]}
    s,h=adapter(tmp_path, lambda req: httpx.Response(200,json={'results':[item]}))
    assert h.openalex_resolve('2401.00001','JEPA',api_key='test').status == 'skipped'
    item['locations']=[{'landing_page_url':'https://arxiv.org/abs/2401.00001'}]
    r=h.openalex_resolve('2401.00001','JEPA',api_key='test')
    assert r.status=='ok' and r.records[0]['arxiv_id']=='2401.00001'


def test_throttle_reset_persisted(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={'Retry-After': '3600'})
    s, h = adapter(tmp_path, handler)
    assert h.hf_search('JEPA').status == 'paused'
    assert HTTPSources(Store(tmp_path), client=h.client).hf_search('other').status == 'paused'
    assert len(calls) == 1


def test_response_bound_and_redirect_and_malformed(tmp_path):
    s, h = adapter(tmp_path, lambda req: httpx.Response(200, content=b'x' * 100), max_bytes=50)
    assert h.hf_search('JEPA').status == 'failed'
    assert s.stats()['works'] == 0
    h.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})))
    assert h.hf_search('JEPA').status == 'failed'
    h.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text='<not-xml')))
    assert h.arxiv_ids(['2401.00001']).status == 'failed'


def test_hf_daily_pagination_and_search(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.params.get('p') == '1':
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{'paper': {'id': '2401.00001', 'title': 'JEPA', 'authors': [{'name': 'A'}]}}])
    s, h = adapter(tmp_path, handler)
    assert h.hf_daily('2024-01-01', limit=1).status == 'ok'
    assert h.hf_daily('2024-01-01', limit=1).records == []
    assert calls[1].url.params['p'] == '1'
    assert h.hf_daily('2024-01-01', limit=1).status == 'skipped'
    assert h.hf_search('JEPA').status == 'ok'
    assert s.stats()['works'] == 1
    assert s.papers()[0]['version'] is None


def test_curated_links_only_and_restart(tmp_path):
    s, h = adapter(tmp_path, lambda req: httpx.Response(200, text='[A](https://arxiv.org/abs/2401.00001v2) [B](https://arxiv.org/abs/2401.00002)'))
    r = h.awesome_jepa(limit=1)
    assert r.records[0]['version'] == 'v2'
    assert h.awesome_jepa(limit=1).records[0]['arxiv_id'] == '2401.00002'
    assert h.awesome_jepa(limit=1).records == []


def test_filtered_snapshot_resume_skips_unrelated_once(tmp_path):
    path = tmp_path/'snapshot.jsonl'
    path.write_text(''.join(json.dumps({'id':f'2401.{i:05d}', 'title':title})+'\n'
        for i,title in [(1,'unrelated'),(2,'JEPA one'),(3,'other'),(4,'JEPA two')]))
    s=Store(tmp_path/'db')
    first=import_arxiv_jsonl(s,path,max_records=1,query='JEPA')
    second=import_arxiv_jsonl(s,path,max_records=1,query='JEPA')
    assert first.records[0]['arxiv_id']=='2401.00002'
    assert second.records[0]['arxiv_id']=='2401.00004'
    assert not import_arxiv_jsonl(s,path,max_records=1,query='JEPA').records


def test_local_jsonl_stream_versions_resume_and_bad_line(tmp_path):
    path = tmp_path / 'metadata.jsonl'
    item = {'id': '2401.00001', 'title': 'JEPA', 'authors_parsed': [['Smith', 'Jane', '']], 'versions': [{'version': 'v1', 'created': 'Mon, 1 Jan 2024 00:00:00 GMT'}, {'version': 'v2', 'created': 'Tue, 2 Jan 2024 00:00:00 GMT'}]}
    path.write_text(json.dumps(item) + '\n{bad\n')
    s = Store(tmp_path / 'db')
    r = import_arxiv_jsonl(s, path, max_records=1)
    assert len(r.records) == 2
    assert s.stats()['manifestations'] == 2
    assert s.papers()[0]['authors'] == ['Jane Smith']
    assert s.papers()[0]['version'] == 'v2'
    assert import_arxiv_jsonl(s, path).status == 'failed'
    assert import_arxiv_jsonl(s, path).status == 'failed'
    assert s.stats()['source_records'] == 2


def test_alphaxiv_unconfigured(tmp_path):
    s, h = adapter(tmp_path, lambda req: pytest.fail('No request allowed'))
    assert h.alphaxiv_discover('JEPA').status == 'skipped'
    assert h.alphaxiv_discover('JEPA', enabled=True).status == 'skipped'
    assert s.db.execute('SELECT count(*) FROM quota_ledger').fetchone()[0] == 0


def test_reported_zero_balance_pauses(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'results': [], 'meta': {'next_cursor': 'more'}}, headers={'x-ratelimit-remaining': '0'})
    s, h = adapter(tmp_path, handler)
    assert h.openalex_citations('W1', enabled=True, api_key='secret').status == 'ok'
    assert h.openalex_citations('W1', enabled=True, api_key='secret').status == 'paused'
    assert len(calls) == 1


def test_arxiv_connection_lease_and_hostile_xml(tmp_path):
    s, h = adapter(tmp_path, lambda req: httpx.Response(200, text='<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><feed>&e;</feed>'))
    from jepa.sources import cursor_set
    import time
    cursor_set(s, 'arxiv', '_inflight_until', time.time() + 60)
    assert h.arxiv_ids(['2401.00001']).status == 'paused'
    cursor_set(s, 'arxiv', '_inflight_until', 0)
    assert h.arxiv_ids(['2401.00001']).status == 'failed'
    assert s.stats()['works'] == 0


def test_configured_alphaxiv_mcp(tmp_path):
    methods = []
    def handler(req):
        p = json.loads(req.content)
        methods.append(p['method'])
        if p['method'] == 'initialize':
            result = {'protocolVersion': '2024-11-05'}
        elif p['method'] == 'notifications/initialized':
            return httpx.Response(202)
        elif p['method'] == 'tools/list':
            result = {'tools': [{'name': 'discover_papers', 'inputSchema': {'properties': {'query': {}}, 'required': ['query']}}]}
        else:
            result = {'content': [{'type': 'text', 'text': 'Candidate https://arxiv.org/abs/2401.00001v1'}]}
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': p['id'], 'result': result})
    s, h = adapter(tmp_path, handler)
    r = h.alphaxiv_discover('JEPA', enabled=True, bearer_token='secret')
    assert r.status == 'ok'
    assert r.records[0]['version'] == 'v1'
    assert methods == ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']


def test_partial_ingestion_does_not_advance_page(tmp_path):
    s, h = adapter(tmp_path, lambda req: httpx.Response(200, json=[{'id': '2401.00001'}, {'id': 'invalid'}]))
    assert h.hf_search('JEPA').status == 'failed'
    assert s.db.execute("SELECT count(*) FROM source_cursors WHERE query='search:JEPA'").fetchone()[0] == 0


def test_unresolved_citation_target_retained(tmp_path):
    s, h = adapter(tmp_path, lambda req: httpx.Response(200, json={'results': [{'id': 'https://openalex.org/W2'}], 'meta': {'next_cursor': None}}))
    assert h.openalex_citations('W1', enabled=True, api_key='secret').status == 'ok'
    assert s.stats()['citation_assertions'] == 1
    assert s.stats()['citation_edges'] == 0
    s.ingest(record('openalex', 'https://openalex.org/W1', url='https://openalex.org/W1'))
    assert s.stats()['citation_edges'] == 1


def test_openalex_references_cached_bounded_batches(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'id': 'https://openalex.org/W1', 'title': 'Seed', 'referenced_works': ['https://openalex.org/W2', 'https://openalex.org/W3']})
    s, h = adapter(tmp_path, handler)
    first = h.openalex_references('W1', enabled=True, api_key='secret', daily_limit=1, limit=1)
    assert first.status == 'ok'
    assert len(first.records) == 1
    assert h.openalex_references('W1', enabled=True, api_key='secret', daily_limit=1, limit=1).status == 'ok'
    assert len(calls) == 1
    assert s.stats()['citation_edges'] == 2
    assert h.openalex_references('W1', enabled=True, api_key='secret').status == 'skipped'

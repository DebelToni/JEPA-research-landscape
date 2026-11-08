import copy
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import json
import shutil
import socket
import threading

import httpx
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from jepa.api import AgentAPI
from jepa.api_docs import OPENAPI
from jepa.export import export_catalog
from jepa.models import Extraction
from jepa.serve import Handler, PublicServer
from jepa.sources import record
from jepa.store import Store

@pytest.fixture
def published(tmp_path):
    store = Store(tmp_path/'private')
    ids = {}
    specs = [
        ('a', 'Latent prediction for robots', 'direct', '2025-03-01', 90),
        ('b', 'Video predictor', 'adjacent', '2024-02-01', 0),
        ('c', 'Unrated theory of prediction', 'context', '2023-01-01', None),
        ('d', 'Robot dataset', 'direct', '2026-01-01', None),
        ('e', 'Unclassified paper', 'uncertain', '', None),
        ('f', 'PRIVATE_EXCLUDED_SENTINEL', 'excluded', '2026-01-01', None),
    ]
    for i, (key, title, relevance, date, score) in enumerate(specs, 1):
        aid = f'2401.0000{i}'
        wid = store.ingest(record('arxiv', aid, arxiv_id=aid, version='v1', title=title,
                                 authors=['Fixture Author'], published=date, url=f'https://arxiv.org/abs/{aid}v1'))
        ids[key] = wid
        if key in 'abc':
            e = Extraction.model_validate({'summary': 'A compact synthetic summary.', 'relevance': relevance,
                'topics': ['robotics'] if key == 'a' else ['video'], 'keywords': ['latent', 'prediction'],
                'sections': {'mechanism': [{'text': 'Frozen encoder predicts an action-conditioned latent state.', 'attribution': 'author', 'evidence': ['c0:e1']}]},
                'evidence': [{'id': 'c0:e1', 'block_id': 'fixture', 'artifact_sha256': 'a'*64, 'quote': 'PRIVATE_QUOTE_SENTINEL', 'source_url': f'https://arxiv.org/html/{aid}v1', 'locator': 'Results Table 1', 'page': None}]})
            e.quality.score = score
            e.quality.coverage = 1 if score is not None else 0
            e.quality.rubric = 'empirical-v1' if score is not None else 'theory-unrated'
            store.set_extraction(wid, e.model_dump())
        else:
            store.db.execute('UPDATE works SET relevance=? WHERE work_id=?', (relevance, wid))
            store.db.commit()
    for source, target, provider in [('a','b','one'), ('a','b','two'), ('b','a','one'), ('c','a','one'), ('a','a','one'), ('f','a','one')]:
        store.db.execute('INSERT INTO citation_edges VALUES(?,?,?,?)', (ids[source], ids[target], provider, 'cites'))
    store.db.commit()
    release = tmp_path/'release-one'
    export_catalog(store, release/'data')
    (release/'index.html').write_text('JEPA landscape')
    store.ingest(record('arxiv', '2401.99999', arxiv_id='2401.99999', title='PRIVATE_NEW_WORK_SENTINEL', url='https://arxiv.org/abs/2401.99999'))
    store.close()
    (tmp_path/'private/secret').write_text('PRIVATE_FILE_SENTINEL')
    (release/'data/escape').symlink_to(tmp_path/'private/secret')
    link = tmp_path/'published';link.symlink_to(release)
    return link, ids


@pytest.fixture
def service(published):
    link, ids = published
    api = AgentAPI()
    server = PublicServer(('127.0.0.1', 0), partial(Handler, publication=link, api=api))
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
        try:
            yield client, ids, link, api
        finally:
            server.shutdown();server.server_close();thread.join(2)


def test_agent_workflow_and_public_boundary(service):
    c, ids, _, _ = service
    assert c.get('/llms.txt').status_code == 200
    assert c.get('/openapi.json').json()['openapi'] == '3.1.0'
    overview = c.get('/api').json()
    assert overview['coverage']['papers'] == 5
    response = c.get('/api/search?q=latent%20prediction&extracted=true&limit=1')
    page = response.json()
    assert page['total'] == 3 and page['results'][0]['work_id'] == ids['a']
    assert response.headers['x-jepa-snapshot'] == page['snapshot']['id']
    assert response.headers['access-control-allow-origin'] == '*'
    assert response.headers['cache-control'] == 'no-cache'
    detail = c.get(page['results'][0]['links']['api']).json()
    assert detail['paper']['extraction']['quality']['score'] == 90
    assert detail['paper']['extraction']['sections']['mechanism'][0]['evidence'] == ['c0:e1']
    assert 'quote' not in detail['paper']['extraction']['evidence'][0]
    for path in ('/api/search?q=PRIVATE', '/api', '/api/facets', '/api/papers/'+ids['a']):
        assert 'SENTINEL' not in c.get(path).text
    for path in ('/runtime/catalog.sqlite3', '/private/secret', '/data/escape', '/api/../runtime/catalog.sqlite3', '/api/papers/../../private/secret'):
        assert c.get(path).status_code == 404
    assert c.get('/api/search?q=PRIVATE').json()['total'] == 0
    assert c.get('/api/papers/'+ids['f']).status_code == 404


def test_filters_and_missing_scores(service):
    c, ids, _, _ = service
    assert c.get('/api/search?min_score=0').json()['total'] == 2
    assert c.get('/api/search?min_score=1').json()['total'] == 1
    assert c.get('/api/search?extracted=false').json()['total'] == 2
    assert c.get('/api/search?extracted=true&rated=false').json()['results'][0]['work_id'] == ids['c']
    assert c.get('/api/search?relevance=uncertain').json()['results'][0]['work_id'] == ids['e']
    assert c.get('/api/search?topic=robotics&source=arxiv&year_from=2025&year_to=2025').json()['total'] == 1
    assert c.get('/api/search?year_from=1000').json()['total'] == 4
    assert c.get('/api/search?topic=ROBOTICS').json()['total'] == 0
    assert c.get('/api/search?q=2401.00001').json()['results'][0]['work_id'] == ids['a']
    assert c.get('/api/search?q=action-conditioned').json()['total'] == 3
    assert c.get('/api/search?q=Fixture%20Author').json()['total'] == 5
    scores = [p['quality']['score'] if p['quality'] else None for p in c.get('/api/search?sort=score').json()['results']]
    assert scores == [90, 0, None, None, None]
    assert c.get('/api/search').json()['results'][0]['work_id'] == ids['d']
    assert c.get('/api/search?offset=1000001').json()['results'] == []


def test_pages_citations_and_facets(service):
    c, ids, _, _ = service
    url = '/api/search?limit=2';found = []
    while url:
        page = c.get(url).json()
        found.extend(p['work_id'] for p in page['results']);url = page['next']
        if url: assert 'snapshot=' in url
    assert len(found) == len(set(found)) == 5
    all_edges = [];url = f'/api/papers/{ids["a"]}/citations?limit=2'
    while url:
        page = c.get(url).json();all_edges.extend(page['results']);url = page['next']
    assert len(all_edges) == 5
    assert len({json.dumps(r['edge'], sort_keys=True) for r in all_edges}) == 5
    assert c.get(f'/api/papers/{ids["a"]}/citations?direction=incoming').json()['total'] == 3
    assert c.get(f'/api/papers/{ids["a"]}/citations?direction=outgoing').json()['total'] == 3
    assert c.get(f'/api/papers/{ids["d"]}/citations').json()['results'] == []
    facets = c.get('/api/facets').json()
    assert (facets['total'], facets['extracted'], facets['rated']) == (5, 3, 2)
    for label, count in facets['facets']['relevance'].items():
        assert c.get('/api/search', params={'relevance': label}).json()['total'] == count


@pytest.mark.parametrize('query', ['unknown=x', 'q=a&q=b', 'bare', 'limit=0', 'limit=101', 'limit=1.2', 'offset=-1',
    'min_score=nan', 'min_score=inf', 'min_score=-1', 'min_score=101', 'year_from=2026&year_to=2020',
    'year_from=no', 'year_to=10000', 'extracted=yes', 'rated=1', 'sort=magic', 'relevance=excluded', 'q=%22*%22',
    'q='+'x'*501, 'q='+'%20'*501, 'q='+'a%20'*33])
def test_invalid_queries_are_json_400(service, query):
    c, _, _, _ = service
    r = c.get('/api/search?'+query)
    assert r.status_code == 400
    assert r.json()['error']['code'] == 'invalid_query'


def test_methods_and_unknown_routes(service):
    c, ids, _, _ = service
    for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'TRACE', 'BOGUS'):
        r = c.request(method, '/api/search')
        assert r.status_code == 405 and r.json()['error']['code'] == 'method_not_allowed'
    preflight = c.options('/api/search', headers={'Origin': 'https://example.org', 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'content-type'})
    assert preflight.status_code == 204
    assert 'Content-Type' in preflight.headers['access-control-allow-headers']
    head = c.head('/api/search');assert head.status_code == 200 and not head.content
    assert c.get('/api/nope').status_code == 404
    assert c.get('/api/papers/not-a-uuid').status_code == 404
    assert c.get('/api/papers/00000000-0000-0000-0000-000000000000').status_code == 404
    assert c.get(f'/api/papers/{ids["a"]}/citations?direction=up').status_code == 400
    assert c.get('/api/search?q=%22OR%20DROP%20TABLE%20search').status_code == 200
    assert c.get('/api/search?q=latent').json()['total'] == 3


def promote(link):
    release = link.parent/'release-two'
    shutil.copytree(link.resolve(), release, symlinks=True)
    catalog = json.loads((release/'data/catalog.json').read_text())
    catalog['papers'][0]['title'] = 'Promoted title'
    catalog['generated_at'] = '2026-11-09T00:00:00Z'
    data = json.dumps(catalog).encode();(release/'data/catalog.json').write_bytes(data)
    manifest = json.loads((release/'data/manifest.json').read_text())
    manifest['files']['catalog.json'] = hashlib.sha256(data).hexdigest()
    (release/'data/manifest.json').write_text(json.dumps(manifest))
    next_link = link.parent/'next';next_link.symlink_to(release);next_link.replace(link)
    return release


def test_snapshot_pinning_reload_and_corruption(service):
    c, _, link, _ = service
    old = c.get('/api/search?limit=1').json()
    release = promote(link)
    assert c.get(old['next']).status_code == 409
    assert c.get(old['results'][0]['links']['api']).status_code == 409
    new = c.get('/api/search?q=Promoted').json()
    assert new['total'] == 1 and new['snapshot']['id'] != old['snapshot']['id']
    broken = link.parent/'broken';shutil.copytree(release, broken, symlinks=True)
    (broken/'data/catalog.json').write_text('{}')
    switch = link.parent/'switch';switch.symlink_to(broken);switch.replace(link)
    response = c.get('/api/search')
    assert response.status_code == 503 and 'private' not in response.text


@pytest.mark.parametrize('filename', ['catalog.json', 'manifest.json'])
def test_api_rejects_snapshot_file_symlink_escape(service, filename):
    c, _, link, _ = service
    source = link.resolve()/'data'/filename
    outside = link.parent/'private'/filename
    outside.write_bytes(source.read_bytes())
    source.unlink();source.symlink_to(outside)
    assert c.get('/api').status_code == 503


def test_concurrent_search_and_single_cached_index(service):
    c, _, _, api = service
    c.get('/api');snapshot = api.snapshot
    def search(_):
        return c.get('/api/search?q=latent').json()['total']
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(search, range(24))) == [3]*24
    assert api.snapshot is snapshot


def test_openapi_matches_response_shapes(service):
    c, ids, link, _ = service
    document = copy.deepcopy(OPENAPI)
    registry = Registry().with_resource('https://jepa.toni.foo/data/schema.json', Resource.from_contents(
        json.loads((link/'data/schema.json').read_text()), default_specification=DRAFT202012))
    for path, schema in [('/api', 'Overview'), ('/api/search', 'Search'), ('/api/facets', 'Facets'),
                         ('/api/papers/'+ids['a'], 'PaperResponse'), ('/api/papers/'+ids['a']+'/citations', 'Citations'), ('/api/nope', 'Error')]:
        contract = {'$ref': '#/components/schemas/'+schema, 'components': document['components']}
        Draft202012Validator.check_schema(contract)
        Draft202012Validator(contract, registry=registry).validate(c.get(path).json())


def test_overload_is_bounded_and_recovers(published):
    link, _ = published
    started, ended = threading.Event(), threading.Event()
    class TinyServer(PublicServer):
        max_connections = 1
        def process_request_thread(self, request, address):
            started.set()
            try: super().process_request_thread(request, address)
            finally: ended.set()
    server = TinyServer(('127.0.0.1', 0), partial(Handler, publication=link, api=AgentAPI()))
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    stalled = socket.create_connection(server.server_address)
    try:
        stalled.sendall(b'GET /api HTTP/1.1\r\n')
        assert started.wait(2)
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as c:
            response = c.get('/api')
            assert response.status_code == 503 and response.json()['error']['code'] == 'busy'
            assert response.headers['retry-after'] == '5'
            stalled.close();assert ended.wait(2)
            assert c.get('/api').status_code == 200
    finally:
        stalled.close();server.shutdown();server.server_close();thread.join(2)

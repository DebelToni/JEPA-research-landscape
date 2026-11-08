import json

import pytest

from jepa import identity_review
from jepa.sources import record
from jepa.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'model': 'test', 'thinking': 'low'}))
    monkeypatch.setenv('JEPA_PROFILE_PATH', str(profile))
    monkeypatch.setenv('JEPA_PROVIDER', 'selected')
    store = Store(tmp_path / 'runtime')
    def add(aid, title='Same title', authors=None, **kwargs):
        return store.ingest(record('arxiv', aid, arxiv_id=aid, title=title,
                                   authors=authors or ['Alice Smith'],
                                   url='https://arxiv.org/abs/' + aid, **kwargs))
    return store, add


def response(payload, timeout, env):
    data = json.loads(payload['prompt'])
    works = data['works']
    assert all('abstract' not in r for w in works for r in w['records'])
    return {'stopReason': 'stop', 'model': 'test', 'provider': 'selected', 'usage': {'input': 12},
            'text': json.dumps({'work_ids': [w['work_id'] for w in works],
                                'decision': 'same_work', 'reason': 'Bibliographic match',
                                'source_urls': [works[0]['records'][0]['url']]})}


def snapshot(store):
    tables = ['works', 'identities', 'manifestations', 'source_records', 'identity_conflicts', 'extraction_revisions']
    return {t: [tuple(r) for r in store.db.execute('SELECT * FROM ' + t)] for t in tables}


def test_proposal_cache_and_no_mutation(setup, monkeypatch):
    s, add = setup
    a = add('2401.00001', abstract='secret extraction-like text')
    b = add('2401.00002')
    before = snapshot(s)
    calls = []
    def call(*args):
        calls.append(1)
        return response(*args)
    monkeypatch.setattr(identity_review.extract, 'call_model', call)
    assert identity_review.review_candidates(s)['reviewed'] == 1
    assert identity_review.review_candidates(s)['cached'] == 1
    assert len(calls) == 1 and snapshot(s) == before
    saved = s.db.execute('SELECT * FROM identity_reviews').fetchone()
    assert set(json.loads(saved['proposal_json'])['work_ids']) == {a, b}
    assert saved['input_sha256'] and saved['prompt_sha256']


def test_conflicts_even_without_title_match(setup, monkeypatch):
    s, add = setup
    a = add('2401.00001', title='One', doi='10.1000/a')
    b = add('2401.00002', title='Two', doi='10.1000/b')
    c = add('2401.00001', title='Three', doi='10.1000/b')
    monkeypatch.setattr(identity_review.extract, 'call_model', response)
    assert identity_review.review_candidates(s)['reviewed'] == 2
    assert s.stats()['works'] == 3 and s.stats()['identity_conflicts'] == 1
    assert len({a, b, c}) == 3


@pytest.mark.parametrize('field,bad', [('work_ids', ['unknown', 'other']),
                                      ('source_urls', ['https://evil.example/']),
                                      ('decision', 'merge')])
def test_invalid_output_rejected(setup, monkeypatch, field, bad):
    s, add = setup
    add('2401.00001'); add('2401.00002')
    def call(*args):
        r = response(*args)
        data = json.loads(r['text']); data[field] = bad; r['text'] = json.dumps(data)
        return r
    monkeypatch.setattr(identity_review.extract, 'call_model', call)
    result = identity_review.review_candidates(s)
    assert result['failures'] == ['model_or_validation_failed']
    assert s.db.execute('SELECT count(*) FROM identity_reviews').fetchone()[0] == 0


def test_failure_is_sanitized_and_no_retry(setup, monkeypatch):
    s, add = setup
    add('2401.00001'); add('2401.00002')
    def call(*args):
        raise RuntimeError('raw provider secret')
    monkeypatch.setattr(identity_review.extract, 'call_model', call)
    result = identity_review.review_candidates(s)
    assert result['calls'] == 1 and 'secret' not in str(result)


def test_cache_does_not_starve_later_pairs(setup, monkeypatch):
    s, add = setup
    for aid in ('2401.00001', '2401.00002', '2401.00003'):
        add(aid)
    monkeypatch.setattr(identity_review.extract, 'call_model', response)
    assert identity_review.review_candidates(s, limit=1)['reviewed'] == 1
    result = identity_review.review_candidates(s, limit=1)
    assert result['cached'] == 1 and result['reviewed'] == 1

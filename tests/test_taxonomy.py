import json
import time
import pytest
from jepa import taxonomy, classify, cli
from jepa.store import Store
from jepa.sources import record
from jepa.models import Extraction
from jepa.export import export_catalog


def add(store, version='v1'):
    return store.ingest(record('arxiv','2401.00001',arxiv_id='2401.00001',version=version,
                              title='A predictive robot model',abstract='A model for robot manipulation.',
                              url='https://arxiv.org/abs/2401.00001'+version))


def assigned(paper, ids=('robotics/manipulation',)):
    return classify.classification({'status':'classified','category_ids':list(ids),'reason':'Robot manipulation is the central task.'},
                                   classify.paper_input(paper),'synthetic-model','a'*64)


def test_controlled_leaves_and_missingness():
    assert len([n for n in taxonomy.NODES.values() if n['parent'] is None]) == 8
    for ids in (['physics-made-up'], ['science'], ['robotics/manipulation']*2):
        with pytest.raises(ValueError):
            taxonomy.validate_assignment({'taxonomy_version':taxonomy.VERSION,'status':'classified','category_ids':ids,'reason':'test'})
    with pytest.raises(ValueError):
        taxonomy.validate_assignment({'taxonomy_version':taxonomy.VERSION,'status':'classified','category_ids':[],'reason':'test'})
    taxonomy.validate_assignment(taxonomy.pending())
    assert taxonomy.path_label('science/domains/fluids').startswith('Physics and scientific modeling / Scientific domains /')


def test_classification_is_versioned_and_extraction_cannot_overwrite_it(tmp_path):
    store=Store(tmp_path);wid=add(store);p=store.get(wid)
    store.set_classification(wid,p['manifestation_id'],assigned(p))
    store.set_extraction(wid,{'relevance':'direct','topics':['physics physics swamp'], 'processing':{'version':'v1'}})
    assert store.get(wid)['category_ids']==['robotics/manipulation']
    add(store,'v2')
    assert store.get(wid)['classification']['status']=='pending'
    assert store.db.execute('select count(*) from category_revisions').fetchone()[0]==1
    with pytest.raises(ValueError):store.set_classification('wrong-work',p['manifestation_id'],assigned(p))


def test_batch_requires_exact_work_ids_and_preserves_provenance(tmp_path,monkeypatch):
    store=Store(tmp_path/'private');wid=add(store);p=store.get(wid)
    directory=tmp_path/'batch';directory.mkdir()
    def model(payload,*args):
        assert 'Never invent IDs' in payload['system']
        return {'stopReason':'stop','model':'synthetic','text':json.dumps({'papers':[{'work_id':wid,
            'status':'classified','category_ids':['robotics/manipulation'],'reason':'Robot manipulation.'}]})}
    monkeypatch.setattr(classify,'call_model',model)
    rows=classify._batch([p],time.monotonic()+5,{},directory,{'model':'synthetic','thinking':'low'})
    assert rows[0][1]==p['manifestation_id']
    assert rows[0][2]['basis']==['title','abstract_excerpt']
    assert rows[0][2]['human_reviewed'] is False


def test_curated_exports_have_joinable_facts_and_no_raw_tags(tmp_path):
    store=Store(tmp_path/'private');wid=add(store);p=store.get(wid)
    store.set_classification(wid,p['manifestation_id'],assigned(p))
    e=Extraction.model_validate({'relevance':'direct','topics':['RAW_SWAMP_TAG'],
        'evidence':[{'id':'e1','block_id':'b','artifact_sha256':'a'*64,'quote':'PRIVATE_QUOTE',
                     'source_url':'https://example.org/paper','locator':'Table 1'}],
        'findings':[{'id':'f1','name':'Zero result','interpretation':{'text':'A zero result.','attribution':'author','evidence':['e1']},
                     'key_results':[{'id':'r1','name':'Selected result','value':'0','numeric_value':0,'label_budget':'1%','evidence':['e1']}],
                     'caveats':[],'evidence':['e1']}]})
    store.set_extraction(wid,e.model_dump())
    out=tmp_path/'public';manifest=export_catalog(store,out)
    catalog=json.loads((out/'catalog.json').read_text());paper=catalog['papers'][0]
    assert paper['category_ids']==['robotics/manipulation']
    assert paper['extraction']['topics']==paper['topics']
    assert 'RAW_SWAMP_TAG' not in (out/'catalog.json').read_text()
    category=json.loads((out/'paper-categories.jsonl').read_text())
    finding=json.loads((out/'findings.jsonl').read_text())
    result=json.loads((out/'results.jsonl').read_text())
    assert category['work_id']==finding['work_id']==result['work_id']==wid
    assert result['finding_id']==finding['finding_id']
    assert result['numeric_value']==0 and result['label_budget']=='1%' and result['protocol'] is None
    assert 'PRIVATE_QUOTE' not in (out/'results.jsonl').read_text()
    assert all(name in manifest['files'] for name in ('taxonomy.json','dictionary.json','paper-categories.jsonl','findings.jsonl','results.jsonl'))
    assert store.get(wid)['extraction']['topics']==['RAW_SWAMP_TAG']


def test_future_screening_assigns_only_controlled_categories(tmp_path,monkeypatch):
    store=Store(tmp_path);wid=add(store)
    def model(payload,*args):
        return {'stopReason':'stop','model':'synthetic','text':json.dumps({'papers':[{'work_id':wid,
            'relevance':'direct','reason':'Predictive robotics.','category_ids':['robotics/manipulation'],
            'status':'classified','category_reason':'Robot manipulation.'}]})}
    monkeypatch.setattr(cli,'call_model',model)
    cli.screen(store,seconds=10)
    assert store.get(wid)['category_ids']==['robotics/manipulation']
    assert store.get(wid)['classification']['model']=='synthetic'


def test_classification_replay_and_explicit_replacement(tmp_path):
    store=Store(tmp_path);wid=add(store);p=store.get(wid);mid=p['manifestation_id']
    a=assigned(p);b={**a,'category_ids':['world-models/latent'],'input_sha256':'b'*64}
    assert store.set_classification(wid,mid,a)
    assert not store.set_classification(wid,mid,b)  # screening cannot replace curated labels
    assert store.set_classification(wid,mid,b,overwrite=True)
    assert store.set_classification(wid,mid,a,overwrite=True)
    assert store.get(wid)['category_ids']==a['category_ids']
    assert store.db.execute('select count(*) from category_revisions').fetchone()[0]==3

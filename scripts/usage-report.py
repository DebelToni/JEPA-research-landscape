"""Summarize locally recorded pipeline calls, without inventing subscription charges."""
from pathlib import Path
import argparse
import collections
import json
from datetime import datetime, timezone
from jepa.store import Store
from jepa.extract import atomic_json


def report(root):
    root=Path(root);calls={};unknown=0
    files=list((root/'extractions').glob('*/response-*.json'))+list((root/'interpretations').glob('*/response-*.json'))
    files+=list((root/'runs').glob('screen-*.json'))+list((root/'runs').glob('identity-*.json'))
    for path in files:
        value=json.loads(path.read_text())
        if not value.get('started_at'):
            unknown+=1;continue
        key=(value.get('provider'),value.get('model'),value['started_at'],value.get('ended_at'))
        calls[key]=value
    # The coordinator can persist returned usage just before an interrupted worker
    # writes its response checkpoint. Include those calls without double counting.
    for path in (root/'runs').glob('*/*-usage.jsonl'):
        for line in path.read_text().splitlines():
            try:value=json.loads(line)
            except json.JSONDecodeError:continue
            if value.get('started_at'):
                key=(value.get('provider'),value.get('model'),value['started_at'],value.get('ended_at'))
                calls.setdefault(key,value)
    tokens=collections.Counter();models=collections.Counter();stops=collections.Counter()
    for value in calls.values():
        models[value.get('model','unknown')]+=1;stops[value.get('stopReason','unknown')]+=1
        for key in ('input','output','cacheRead','cacheWrite','reasoning','totalTokens'):
            n=(value.get('usage') or {}).get(key)
            if isinstance(n,(int,float)):tokens[key]+=n
    store=Store(root);papers=store.papers()
    public=[p for p in papers if p['title'] and p['relevance']!='excluded' and not store.db.execute('SELECT 1 FROM identity_conflicts WHERE work_id=?',(p['work_id'],)).fetchone()]
    stats=store.stats()
    stats.update({'metadata_with_titles':sum(bool(p['title']) for p in papers),
                  'public_paper_records':len(public),
                  'source_extracted':sum(bool(p['extraction']) for p in public),
                  'papers_with_experimental_findings':sum(bool((p['extraction'] or {}).get('findings')) for p in public),
                  'experimental_findings':sum(len((p['extraction'] or {}).get('findings',[])) for p in public),
                  'selected_results':sum(len(f['key_results']) for p in public for f in (p['extraction'] or {}).get('findings',[])),
                  'provisionally_rated':sum((p['extraction'] or {}).get('quality',{}).get('score') is not None for p in public)})
    requests={r[0]:r[1] for r in store.db.execute('SELECT provider,count(*) FROM quota_ledger GROUP BY provider')}
    store.close()
    return {'created_at':datetime.now(timezone.utc).isoformat(),'stats':stats,
            'recorded_pipeline_calls':len(calls),'model_calls':dict(models),'stop_reasons':dict(stops),
            'provider_returned_tokens':dict(tokens),'provider_requests':requests,
            'subscription_charges':'unknown; catalog-estimated token prices are not charges',
            'scope':'Recorded pipeline calls only; excludes coding/research agent sessions, setup probes and interrupted calls without returned usage. Cache tokens and totalTokens retain provider meanings.',
            'unidentified_receipts':unknown}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('runtime'))
    p.add_argument('--output',type=Path,default=Path('runtime/reports/usage.json'))
    a=p.parse_args();value=report(a.root);atomic_json(a.output,value);print(json.dumps(value,indent=2))

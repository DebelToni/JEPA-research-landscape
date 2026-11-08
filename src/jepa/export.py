"""Allowlisted public snapshots. No raw text, credentials or operational state."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
from .models import Extraction
from . import taxonomy


def public_url(value):
    if not value:
        return None
    try:
        u = urlsplit(value)
        host = (u.hostname or '').lower()
        if u.scheme not in ('https', 'http') or not host or u.username or u.password:
            return None
        if host == 'localhost' or host.endswith(('.local', '.internal', '.ts.net')) or '.' not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if re.search(r'(?:key|token|secret|password|signature)=', u.query, re.I):
            return None
        return value
    except ValueError:
        return None


def public_extraction(value):
    if value is None:
        return None
    # Reject unknown fields rather than accidentally publishing future private additions.
    record = Extraction.model_validate(value).model_dump()
    processing = record.pop('processing')
    known = {entry['id'] for entry in record['evidence']}
    for key in ('summary_evidence', 'relevance_evidence'):
        record[key] = [ref for ref in processing.get(key, []) if isinstance(ref, str) and ref in known]
    used = set(record['summary_evidence'] + record['relevance_evidence'])
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'evidence' and isinstance(item, list) and all(isinstance(ref, str) for ref in item):
                    used.update(item)
                elif key not in ('evidence', 'experiments'):
                    collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    collect(record)
    record['evidence'] = [entry for entry in record['evidence'] if entry['id'] in used]
    record.pop('experiments', None)
    for e in record['evidence']:
        e.pop('quote')
        e['source_url'] = public_url(e['source_url'])
        if not e['source_url']:
            raise ValueError('Nonpublic evidence URL')
    record['artifacts'] = [a for a in record['artifacts'] if public_url(a['url'])]
    encoded = json.dumps(record)
    if re.search(r'/home/|/Users/|\.ts\.net\b|sk-[A-Za-z0-9]{20}', encoded):
        raise ValueError('Possible private material in export')
    return record


def public_paper(p):
    keys = ('work_id', 'title', 'authors', 'published', 'updated', 'arxiv_id',
            'version', 'doi', 'relevance', 'status', 'sources')
    out = {k: p.get(k) for k in keys}
    out['url'] = public_url(p.get('url'))
    if not out['url']:
        raise ValueError('Nonpublic paper URL')
    classification = p.get('classification') or taxonomy.pending()
    taxonomy.validate_assignment(classification)
    allowed = ('taxonomy_version','status','category_ids','reason','basis','model','human_reviewed','input_sha256','source_sha256','prompt_sha256','created_at')
    out['classification'] = {key: classification[key] for key in allowed if key in classification}
    out['category_ids'] = list(classification['category_ids'])
    out['topics'] = [taxonomy.path_label(value) for value in out['category_ids']]
    for key in ('published', 'updated'):
        out[key] = out.get(key) or None
    out['extraction'] = public_extraction(p.get('extraction'))
    if out['extraction']:
        out['extraction']['topics'] = out['topics']
    return out


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def export_catalog(store, destination: Path):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.export-', dir=destination.parent))
    try:
        # One read snapshot across live extraction/classification writes. Release
        # it before serialization; every export table derives from these objects.
        own_transaction = not store.db.in_transaction
        if own_transaction: store.db.execute('BEGIN')
        try:
            all_papers = store.papers()
            quarantined = {row[0] for row in store.db.execute('SELECT work_id FROM identity_conflicts')}
            all_edges = [dict(r) for r in store.db.execute('SELECT source,target,provider,relation FROM citation_edges')]
        finally:
            if own_transaction: store.db.rollback()
        papers = [public_paper(p) for p in all_papers if p.get('title')
                  and p['relevance'] != 'excluded' and p['work_id'] not in quarantined]
        ids = {p['work_id'] for p in papers}
        edges = [r for r in all_edges if r['source'] in ids and r['target'] in ids]
        coverage = {'sources': sorted({source for p in papers for source in p['sources']}),
                    # Raw provider cursors and query payloads are private operational state.
                    'papers': len(papers), 'extracted': sum(p['extraction'] is not None for p in papers),
                    'complete': sum(bool(p['extraction']) and p['extraction']['completeness']=='complete' for p in papers),
                    'scope': 'Continuous discovery; source coverage and extraction remain incomplete.',
                    'excluded': sum(p['relevance']=='excluded' for p in all_papers),
                    'taxonomy_version': taxonomy.VERSION,
                    'classification': {status:sum(p['classification']['status']==status for p in papers) for status in taxonomy.STATUSES},
                    'identity_conflicts_pending': len(quarantined),
                    'source_configuration': 'Not audited by public export; presence in sources records observation only.',
                    'not_yet_implemented': ['Semantic Scholar', 'Crossref', 'venue-wide OpenReview discovery']}
        catalog = {'schema_version': '1.0', 'generated_at': datetime.now(timezone.utc).isoformat(),
                   'papers': papers, 'edges': edges, 'coverage': coverage, 'taxonomy': taxonomy.DATA}
        if re.search(r'/home/|/Users/|\.ts\.net\b|sk-[A-Za-z0-9]{20}|(?:api_key|token|secret|password)=', json.dumps(catalog), re.I):
            raise ValueError('Possible private material in export')
        write_json(staging/'catalog.json', catalog)
        for p in papers:
            write_json(staging/'papers'/f"{p['work_id']}.json", p)
            lines = [f"# {p['title']}", '', f"Source: {p['url']}", f"Version: {p['version'] or 'unresolved'}", '',
                     f"Categories: {'; '.join(p['topics']) or 'Unclassified'}", f"Classification: {p['classification']['status']}", '']
            e = p['extraction']
            if e:
                lines += [e['summary'], '']
                for name, claims in e['sections'].items():
                    if claims:
                        lines += ['## '+name.replace('_', ' ').title(), '']
                        lines += [f"- [{c['attribution']}] {c['text']} ({', '.join(c['evidence'])})" for c in claims]
                        lines.append('')
                if e.get('findings'):
                    lines += ['## Findings', '']
                    for finding in e['findings']:
                        interpretation = finding['interpretation']
                        lines += [f"### {finding['name']}", '',
                                  f"[{interpretation['attribution']}] {interpretation['text']} ({', '.join(interpretation['evidence'])})", '']
                        for result in finding['key_results']:
                            details = ', '.join(f"{key}: {value}" for key, value in result.items()
                                                if key not in ('id', 'evidence') and value is not None)
                            lines.append(f"- {details} ({', '.join(result['evidence'])})")
                        for caveat in finding['caveats']:
                            lines.append(f"- Caveat [{caveat['attribution']}]: {caveat['text']} ({', '.join(caveat['evidence'])})")
                        lines += [f"Evidence: {', '.join(finding['evidence'])}", '']
                lines += ['## Evidence', ''] + [f"- {x['id']}: {x['locator']} · {x['source_url']}" for x in e['evidence']]
            else:
                lines += ['Full-paper extraction pending.']
            (staging/'papers'/f"{p['work_id']}.md").write_text('\n'.join(lines)+'\n')
        (staging/'papers.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in papers))
        write_json(staging/'edges.json', edges)
        write_json(staging/'taxonomy.json', taxonomy.DATA)
        associations, findings, results = [], [], []
        finding_keys, result_keys = set(), set()
        for p in papers:
            identity = {'work_id':p['work_id'],'version':p['version']}
            for category_id in p['category_ids']:
                associations.append({**identity,'category_id':category_id,'taxonomy_version':taxonomy.VERSION,
                                     'classification_status':p['classification']['status']})
            for finding in (p['extraction'] or {}).get('findings', []):
                key = (p['work_id'],p['version'],finding['id'])
                if key in finding_keys: raise ValueError('Duplicate analytical finding key')
                finding_keys.add(key)
                findings.append({**identity,'finding_id':finding['id'],'name':finding['name'],
                    'interpretation':finding['interpretation']['text'],'attribution':finding['interpretation']['attribution'],
                    'interpretation_evidence':finding['interpretation']['evidence'],
                    'caveats':finding['caveats'],'evidence':finding['evidence']})
                for result in finding['key_results']:
                    key = (p['work_id'],p['version'],result['id'])
                    if key in result_keys: raise ValueError('Duplicate analytical result key')
                    result_keys.add(key)
                    results.append({**identity,'finding_id':finding['id'],'result_id':result['id'],
                                    **{k:v for k,v in result.items() if k!='id'}})
        for name, rows in (('paper-categories',associations),('findings',findings),('results',results)):
            (staging/f'{name}.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows))
        write_json(staging/'dictionary.json', {
            'schema_version':'1.0','taxonomy_version':taxonomy.VERSION,
            'tables':{
                'papers.jsonl':{'key':['work_id'],'description':'One current public paper record, including version, classification, scores and evidence.'},
                'paper-categories.jsonl':{'key':['work_id','version','category_id'],'description':'One assigned leaf per row; join category_id to taxonomy.nodes.id. Multi-label membership, not exclusive groups.'},
                'findings.jsonl':{'key':['work_id','version','finding_id'],'description':'One experimental finding per row; interpretations distinguish author statements from analyst inference.'},
                'results.jsonl':{'key':['work_id','version','result_id'],'description':'One selected numerical result per row; join finding_id within work_id/version to findings.'}},
            'null_policy':'Missing and unrated values remain null; absent rows are not zero or negative results.',
            'numeric_policy':'numeric_value is a parsed scalar in the reported scale, not harmonized across units, metrics, datasets or protocols. Preserve value, units, split, label_budget and protocol when comparing.',
            'evidence_policy':'Evidence IDs resolve inside the same paper record and source version. Raw quotes and full text remain private; public locators link to originals.',
            'category_policy':'Only approved leaves are assigned. Parent counts are unique-paper unions; do not sum overlapping branches into a corpus total. Classification is separate from evidence quality.',
            'snapshot_policy':'All tables are derived from the same database read snapshot and covered by manifest hashes.'})
        schema = copy.deepcopy(Extraction.model_json_schema())
        for key in ('summary_evidence', 'relevance_evidence'):
            schema['properties'][key] = {'type': 'array', 'items': {'type': 'string'}}
        for private_field in ('processing', 'experiments'):
            schema['properties'].pop(private_field, None)
            if private_field in schema.get('required', []): schema['required'].remove(private_field)
        ev = schema['$defs']['Evidence']; ev['properties'].pop('quote'); ev['required'].remove('quote')
        write_json(staging/'schema.json', schema)
        manifest = {'schema_version': '1.0', 'generated_at': catalog['generated_at'], 'coverage': coverage,
                    'license': 'Original annotations: CC BY 4.0; third-party bibliographic facts and identifiers retain source terms. No full text or evidence quotations included.',
                    'files': {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in staging.rglob('*') if p.is_file()}}
        write_json(staging/'manifest.json',manifest)
        # Only generated data directory is replaced; never a caller's arbitrary tree.
        if destination.exists():
            if not (destination/'catalog.json').exists():
                raise ValueError('Refusing to replace a non-export directory')
            # Vite copies every public/ child. Backups must stay in private runtime.
            import time
            backups = store.root/'export-backups'
            backups.mkdir(parents=True, exist_ok=True)
            backup = backups/str(time.time_ns())
            shutil.move(str(destination), str(backup))
        staging.rename(destination)
        return manifest
    except BaseException:
        if staging.exists(): shutil.rmtree(staging)
        raise

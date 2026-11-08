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
            'version', 'doi', 'relevance', 'topics', 'status', 'sources')
    out = {k: p.get(k) for k in keys}
    out['url'] = public_url(p.get('url'))
    if not out['url']:
        raise ValueError('Nonpublic paper URL')
    out['extraction'] = public_extraction(p.get('extraction'))
    return out


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def export_catalog(store, destination: Path):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.export-', dir=destination.parent))
    try:
        papers = [public_paper(p) for p in store.papers() if p.get('title') and p['relevance'] != 'excluded']
        ids = {p['work_id'] for p in papers}
        edges = [dict(r) for r in store.db.execute('SELECT source,target,provider,relation FROM citation_edges')
                 if r['source'] in ids and r['target'] in ids]
        coverage = {'sources': sorted({source for p in papers for source in p['sources']}),
                    # Raw provider cursors and query payloads are private operational state.
                    'papers': len(papers), 'extracted': sum(p['extraction'] is not None for p in papers),
                    'complete': sum(bool(p['extraction']) and p['extraction']['completeness']=='complete' for p in papers),
                    'scope': 'Continuous discovery; source coverage and extraction remain incomplete.',
                    'excluded': sum(p['relevance']=='excluded' for p in store.papers()),
                    'source_configuration': 'Not audited by public export; presence in sources records observation only.',
                    'not_yet_implemented': ['Semantic Scholar', 'Crossref', 'venue-wide OpenReview discovery']}
        catalog = {'schema_version': '1.0', 'generated_at': datetime.now(timezone.utc).isoformat(),
                   'papers': papers, 'edges': edges, 'coverage': coverage}
        if re.search(r'/home/|/Users/|\.ts\.net\b|sk-[A-Za-z0-9]{20}|(?:api_key|token|secret|password)=', json.dumps(catalog), re.I):
            raise ValueError('Possible private material in export')
        write_json(staging/'catalog.json', catalog)
        for p in papers:
            write_json(staging/'papers'/f"{p['work_id']}.json", p)
            lines = [f"# {p['title']}", '', f"Source: {p['url']}", f"Version: {p['version'] or 'unresolved'}", '']
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

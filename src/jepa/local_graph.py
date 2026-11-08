"""Read-only reuse of an existing scholarly SQLite graph, without its application.

Supported input: index_snapshots(id,paper_key,payload,observed_at). No migrations,
provider calls, queue updates or imports of the source application's code.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from .sources import record
from .store import arxiv_identity

DEFAULT_TERMS=('jepa','world model','latent dynamics','neural operator','physics-informed','action-conditioned')


def import_graph(store, path: Path, terms=DEFAULT_TERMS, limit=1000):
    path=Path(path).resolve()
    if not 1<=limit<=10000 or not terms or len(terms)>30:
        raise ValueError('Invalid bounded graph import')
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Source has uncheckpointed WAL; require a stable read-only snapshot')
    before=(path.stat().st_size,path.stat().st_mtime_ns)
    source=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    source.execute('PRAGMA query_only=ON')
    source.set_progress_handler(lambda: time.monotonic()>deadline,10000)
    deadline=time.monotonic()+60
    terms=tuple(str(term).casefold() for term in terms)
    query=' OR '.join("lower(json_extract(payload,'$.title')) LIKE ?" for _ in terms)
    try:
        snapshots=source.execute('SELECT id,paper_key,payload,observed_at FROM index_snapshots WHERE '+query+' ORDER BY observed_at DESC,id DESC LIMIT ?',(*('%'+term+'%' for term in terms),limit)).fetchall()
    finally:source.close()
    accepted=0;skipped=0;seen=set();new=0
    for sid,key,raw,observed in snapshots:
        value=json.loads(raw)
        oid=str(value.get('id') or key).removeprefix('https://openalex.org/').removeprefix('openalex:')
        if not re.fullmatch(r'W\d+',oid) or oid in seen:
            skipped+=1;continue
        seen.add(oid)
        explicit=set()
        for location in value.get('locations',[]):
            url=location.get('landing_page_url') or ''
            if re.fullmatch(r'https?://(?:export\.)?arxiv\.org/abs/[\w./-]+',url):
                try:explicit.add(arxiv_identity(url))
                except ValueError:pass
        aid=version=None
        if len(explicit)==1:aid,version=next(iter(explicit))
        fullid='https://openalex.org/'+oid
        existed=store.db.execute("SELECT 1 FROM identities WHERE kind='provider:openalex' AND value=?",(fullid,)).fetchone()
        r=record('openalex',fullid,arxiv_id=aid,version=version,doi=value.get('doi'),
                 title=value.get('title') or '',authors=[a['name'] for a in value.get('authors',[]) if a.get('name')],
                 published=str(value.get('year') or ''),updated='',url=fullid,
                 license='https://creativecommons.org/publicdomain/zero/1.0/',
                 provenance={'acquisition':'retained-index-cache','snapshot_id':sid,'upstream_observed_at':observed,
                             'payload_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                             'ambiguous_arxiv_locations':len(explicit)>1})
        store.ingest(r);accepted+=1;new+=not bool(existed)
        with store.db:
            for ref in value.get('referenced_works',[]):
                rid=str(ref).removeprefix('https://openalex.org/').removeprefix('openalex:')
                if re.fullmatch(r'W\d+',rid):
                    store.db.execute('INSERT OR IGNORE INTO citation_assertions(provider,source_id,target_id,observed) VALUES (?,?,?,?)',
                                     ('openalex-cache',fullid,'https://openalex.org/'+rid,observed))
    with store.db:
        store.db.execute('''INSERT OR IGNORE INTO citation_edges(source,target,provider,relation)
            SELECT a.work_id,b.work_id,c.provider,c.relation FROM citation_assertions c
            JOIN identities a ON a.kind='provider:openalex' AND a.value=c.source_id
            JOIN identities b ON b.kind='provider:openalex' AND b.value=c.target_id
            WHERE c.provider='openalex-cache' ''')
    if before!=(path.stat().st_size,path.stat().st_mtime_ns):
        raise ValueError('Source changed during read-only import; review imported provenance')
    return {'snapshots_read':len(snapshots),'records_imported':accepted,'new_provider_identities':new,
            'skipped_duplicate_or_non_openalex':skipped,'network_requests':0,'source_modified':False}

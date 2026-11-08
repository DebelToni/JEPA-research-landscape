"""SQLite identity and processing ledger.

Store(root) owns root/catalog.sqlite3; ``db`` is the SQLite connection (Row rows).
Contract methods: ingest(record)->UUID, get(UUID)->canonical dict, papers()->list,
set_extraction(UUID, dict), set_status(UUID, status), enqueue(UUID, stage,
fingerprint='v1'), claim(stage, lease_seconds=900)->job|None, finish(job_id,
error=None, *, lease_until=None), stats()->dict. Concurrent workers must pass
claim's lease_until to finish for stale-worker fencing; legacy id-only finish
checks expiry but cannot distinguish a reclaimed lease. finish requires a live lease; failed jobs are retained,
not automatically retried. Re-enqueue a failed fingerprint explicitly to retry.

Source observations, manifestations and extraction revisions are append-only.
get() selects the highest known arXiv version, never a late stale observation;
unknown versions stay distinct. Job rows pin manifestation IDs. set_extraction
uses processing.version when present (including historical versions), otherwise
current; acquisition callers should always supply that explicit provenance. Cross-identifier
collisions are quarantined as separate works in identity_conflicts, not merged.
No fuzzy matching or implicit identity merge. All persisted times are UTC.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

RELEVANCE = {'direct', 'adjacent', 'context', 'uncertain', 'excluded'}
STATUSES = {'discovered', 'acquired', 'extracted', 'partial', 'failed'}
FIELDS = ('source', 'source_id', 'arxiv_id', 'version', 'doi', 'title', 'authors',
          'published', 'updated', 'url', 'abstract', 'license')


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def arxiv_identity(value):
    """Return (unversioned ID, explicit version|None), including legacy IDs."""
    if not value:
        return None, None
    value = re.sub(r'^arxiv:', '', value.strip(), flags=re.I)
    value = re.sub(r'^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/', '', value)
    value = value.removesuffix('.pdf')
    m = re.fullmatch(r'(\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(v[1-9]\d*)?', value)
    if not m:
        raise ValueError('Invalid arXiv identity')
    return m.group(1), m.group(2)


def normalize_doi(value):
    if not value:
        return None
    value = unquote(re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value.strip(), flags=re.I)).lower()
    if not re.fullmatch(r'10\.\d{4,9}/\S+', value):
        raise ValueError('Invalid DOI')
    return value


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'catalog.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS works (
          work_id TEXT PRIMARY KEY, created REAL NOT NULL, relevance TEXT NOT NULL DEFAULT 'uncertain',
          topics TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'discovered');
        CREATE TABLE IF NOT EXISTS identities (
          kind TEXT NOT NULL, value TEXT NOT NULL, work_id TEXT NOT NULL REFERENCES works,
          PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS manifestations (
          id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works,
          identity TEXT NOT NULL, version TEXT NOT NULL, rank INTEGER NOT NULL,
          created REAL NOT NULL, UNIQUE(work_id,identity,version));
        CREATE TABLE IF NOT EXISTS manifestation_status (
          manifestation_id INTEGER PRIMARY KEY REFERENCES manifestations,
          status TEXT NOT NULL DEFAULT 'discovered');
        CREATE TABLE IF NOT EXISTS source_records (
          id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works,
          manifestation_id INTEGER NOT NULL REFERENCES manifestations,
          digest TEXT NOT NULL UNIQUE, record TEXT NOT NULL, retrieved REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS identity_conflicts (
          id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works,
          candidates TEXT NOT NULL, record_digest TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS extraction_revisions (
          id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works,
          manifestation_id INTEGER NOT NULL REFERENCES manifestations,
          digest TEXT NOT NULL, extraction TEXT NOT NULL, created REAL NOT NULL,
          UNIQUE(manifestation_id,digest));
        CREATE TABLE IF NOT EXISTS category_revisions (
          id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works,
          manifestation_id INTEGER NOT NULL REFERENCES manifestations,
          taxonomy_version TEXT NOT NULL, revision_key TEXT NOT NULL,
          record TEXT NOT NULL, created REAL NOT NULL,
          UNIQUE(manifestation_id,taxonomy_version,revision_key));
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works,
          manifestation_id INTEGER NOT NULL REFERENCES manifestations, stage TEXT NOT NULL,
          fingerprint TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
          lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
          UNIQUE(work_id,manifestation_id,stage,fingerprint));
        CREATE TABLE IF NOT EXISTS source_cursors (
          source TEXT NOT NULL, query TEXT NOT NULL, cursor TEXT, updated REAL NOT NULL,
          PRIMARY KEY(source,query));
        CREATE TABLE IF NOT EXISTS quota_ledger (
          id INTEGER PRIMARY KEY, provider TEXT NOT NULL, day TEXT NOT NULL,
          at REAL NOT NULL, outcome TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS citation_edges (
          source TEXT NOT NULL REFERENCES works, target TEXT NOT NULL REFERENCES works,
          provider TEXT NOT NULL, relation TEXT NOT NULL DEFAULT 'cites',
          PRIMARY KEY(source,target,provider,relation));
        CREATE TABLE IF NOT EXISTS citation_assertions (
          provider TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
          relation TEXT NOT NULL DEFAULT 'cites', observed REAL NOT NULL,
          PRIMARY KEY(provider,source_id,target_id,relation));
        CREATE VIRTUAL TABLE IF NOT EXISTS paper_search USING fts5(work_id UNINDEXED,title,abstract);
        ''')
        # Preserve all prototype revisions while separating decision identity
        # from input provenance (which remains inside each immutable record).
        if any(row[1] == 'input_sha256' for row in self.db.execute('PRAGMA table_info(category_revisions)')):
            self.db.execute('ALTER TABLE category_revisions RENAME COLUMN input_sha256 TO revision_key')
            self.db.commit()

    def close(self):
        self.db.close()

    def ingest(self, record):
        missing = set(FIELDS) - record.keys()
        if missing:
            raise ValueError('Missing SourceRecord fields: ' + ', '.join(sorted(missing)))
        r = dict(record)
        if not r['source'] or not r['source_id'] or not isinstance(r['authors'], list):
            raise ValueError('Invalid source identity or authors')
        aid, embedded_version = arxiv_identity(r['arxiv_id'])
        version = r['version'] or embedded_version
        if version and (not aid or not re.fullmatch(r'v[1-9]\d*', version)):
            raise ValueError('Invalid explicit version')
        if embedded_version and r['version'] and embedded_version != r['version']:
            raise ValueError('Conflicting arXiv versions')
        r.update(arxiv_id=aid, version=version, doi=normalize_doi(r['doi']))
        raw = dumps(r)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        keys = [('provider:' + r['source'], str(r['source_id']))]
        if aid:
            keys.append(('arxiv', aid))
        if r['doi']:
            keys.append(('doi', r['doi']))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT work_id FROM source_records WHERE digest=?', (digest,)).fetchone()
            if old:
                self.db.commit()
                return old[0]
            candidates = set()
            for kind, value in keys:
                row = self.db.execute('SELECT work_id FROM identities WHERE kind=? AND value=?', (kind, value)).fetchone()
                if row:
                    candidates.add(row[0])
            # A provider or DOI claiming a second arXiv ID is also a conflict.
            conflict = len(candidates) > 1
            if len(candidates) == 1 and aid:
                known = {row[0] for row in self.db.execute("SELECT value FROM identities WHERE kind='arxiv' AND work_id=?", (next(iter(candidates)),))}
                conflict |= bool(known and aid not in known)
            wid = next(iter(candidates)) if len(candidates) == 1 and not conflict else str(uuid.uuid4())
            self.db.execute('INSERT OR IGNORE INTO works(work_id,created) VALUES (?,?)', (wid, time.time()))
            if conflict:
                self.db.execute('INSERT INTO identity_conflicts(work_id,candidates,record_digest,reason,created) VALUES (?,?,?,?,?)',
                                (wid, dumps(sorted(candidates)), digest, 'incompatible explicit identifiers', time.time()))
            else:
                for kind, value in keys:
                    self.db.execute('INSERT OR IGNORE INTO identities VALUES (?,?,?)', (kind, value, wid))
            identity = 'arxiv:' + aid if aid else ('doi:' + r['doi'] if r['doi'] else r['source'] + ':' + str(r['source_id']))
            self.db.execute('INSERT OR IGNORE INTO manifestations(work_id,identity,version,rank,created) VALUES (?,?,?,?,?)',
                            (wid, identity, version or '', int(version[1:]) if version else 0, time.time()))
            mid = self.db.execute('SELECT id FROM manifestations WHERE work_id=? AND identity=? AND version=?', (wid, identity, version or '')).fetchone()[0]
            self.db.execute('INSERT INTO source_records(work_id,manifestation_id,digest,record,retrieved) VALUES (?,?,?,?,?)', (wid, mid, digest, raw, time.time()))
            self.db.execute('DELETE FROM paper_search WHERE work_id=?', (wid,))
            current = self.get(wid)
            self.db.execute('INSERT INTO paper_search VALUES (?,?,?)', (wid, current['title'], current['abstract']))
            self.db.execute('''INSERT OR IGNORE INTO citation_edges(source,target,provider,relation)
                SELECT a.work_id,b.work_id,c.provider,c.relation FROM citation_assertions c
                JOIN identities a ON a.kind='provider:'||c.provider AND a.value=c.source_id
                JOIN identities b ON b.kind='provider:'||c.provider AND b.value=c.target_id''')
            self.db.commit()
            return wid
        except BaseException:
            self.db.rollback()
            raise

    def _manifestation(self, wid):
        row = self.db.execute("SELECT * FROM manifestations WHERE work_id=? ORDER BY rank DESC,CASE WHEN identity LIKE 'arxiv:%' THEN 0 WHEN identity LIKE 'doi:%' THEN 1 ELSE 2 END,id DESC LIMIT 1", (wid,)).fetchone()
        if not row:
            raise KeyError(wid)
        return row

    def get(self, work_id):
        m = self._manifestation(work_id)
        r = json.loads(self.db.execute("SELECT record FROM source_records WHERE manifestation_id=? ORDER BY coalesce(json_extract(record,'$.provenance.metadata_pending'),0),CASE json_extract(record,'$.source') WHEN 'arxiv' THEN 0 WHEN 'arxiv-local' THEN 1 WHEN 'openalex' THEN 2 WHEN 'huggingface' THEN 3 ELSE 4 END,id DESC LIMIT 1", (m['id'],)).fetchone()[0])
        w = dict(self.db.execute('SELECT * FROM works WHERE work_id=?', (work_id,)).fetchone())
        e = self.db.execute('SELECT extraction FROM extraction_revisions WHERE manifestation_id=? ORDER BY id DESC LIMIT 1', (m['id'],)).fetchone()
        r.update(work_id=work_id, relevance=w['relevance'], topics=json.loads(w['topics']), status=w['status'], extraction=json.loads(e[0]) if e else None)
        state = self.db.execute('SELECT status FROM manifestation_status WHERE manifestation_id=?', (m['id'],)).fetchone()
        r['status'] = state[0] if state else 'discovered'
        r['sources'] = [row[0] for row in self.db.execute("SELECT DISTINCT json_extract(record,'$.source') FROM source_records WHERE work_id=? ORDER BY 1", (work_id,))]
        from .taxonomy import VERSION, pending
        category = self.db.execute('SELECT record FROM category_revisions WHERE manifestation_id=? AND taxonomy_version=? ORDER BY id DESC LIMIT 1', (m['id'], VERSION)).fetchone()
        r['manifestation_id'] = m['id']
        r['classification'] = json.loads(category[0]) if category else pending()
        r['category_ids'] = r['classification']['category_ids']
        return r

    def papers(self):
        return [self.get(r[0]) for r in self.db.execute('SELECT work_id FROM works ORDER BY created,work_id')]

    def set_classification(self, work_id, manifestation_id, record, *, overwrite=False):
        from .taxonomy import validate_assignment
        validate_assignment(record)
        owner = self.db.execute('SELECT work_id FROM manifestations WHERE id=?', (manifestation_id,)).fetchone()
        if not owner or owner[0] != work_id:
            raise ValueError('Classification manifestation does not belong to work')
        with self.db:
            if not self.db.in_transaction: self.db.execute('BEGIN IMMEDIATE')
            old = self.db.execute('SELECT record FROM category_revisions WHERE manifestation_id=? AND taxonomy_version=? ORDER BY id DESC LIMIT 1', (manifestation_id, record['taxonomy_version'])).fetchone()
            if old:
                previous = json.loads(old[0])
                if old[0] == dumps(record) or (not overwrite and (previous['status'] == 'classified' or previous.get('human_reviewed'))):
                    return False
            self.db.execute('INSERT INTO category_revisions(work_id,manifestation_id,taxonomy_version,revision_key,record,created) VALUES(?,?,?,?,?,?)',
                            (work_id, manifestation_id, record['taxonomy_version'], str(uuid.uuid4()), dumps(record), time.time()))
        return True

    def set_extraction(self, work_id, extraction):
        # Pin historical processing explicitly; omitted version means current.
        current = self._manifestation(work_id)
        m = current
        processing = extraction.get('processing', {})
        if 'version' in processing:
            m = self.db.execute('SELECT * FROM manifestations WHERE work_id=? AND version=? ORDER BY id DESC LIMIT 1', (work_id, processing['version'] or '')).fetchone()
            if m is None:
                raise ValueError('Extraction version has not been ingested')
        raw = dumps(extraction)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO extraction_revisions(work_id,manifestation_id,digest,extraction,created) VALUES (?,?,?,?,?)',
                            (work_id, m['id'], hashlib.sha256(raw.encode()).hexdigest(), raw, time.time()))
            relevance = extraction.get('relevance', 'uncertain')
            if relevance not in RELEVANCE:
                raise ValueError('Invalid relevance')
            if m['id'] == current['id']:
                self.db.execute('UPDATE works SET relevance=?,topics=? WHERE work_id=?', (relevance, dumps(extraction.get('topics', [])), work_id))

    def set_status(self, work_id, status):
        if status not in STATUSES:
            raise ValueError('Invalid status')
        m = self._manifestation(work_id)
        with self.db:
            self.db.execute('INSERT INTO manifestation_status VALUES (?,?) ON CONFLICT(manifestation_id) DO UPDATE SET status=excluded.status', (m['id'], status))
            self.db.execute('UPDATE works SET status=? WHERE work_id=?', (status, work_id))

    def enqueue(self, work_id, stage, fingerprint='v1'):
        m = self._manifestation(work_id)
        with self.db:
            self.db.execute('INSERT INTO jobs(work_id,manifestation_id,stage,fingerprint) VALUES (?,?,?,?) ON CONFLICT(work_id,manifestation_id,stage,fingerprint) DO UPDATE SET state=CASE WHEN state=\'failed\' THEN \'pending\' ELSE state END', (work_id, m['id'], stage, fingerprint))

    def claim(self, stage, lease_seconds=900, *, job_ids=None):
        if lease_seconds <= 0:
            raise ValueError('Lease must be positive')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            now = time.time()
            scope = ""
            params = [stage, now]
            if job_ids is not None:
                if not job_ids:
                    self.db.commit()
                    return None
                scope = " AND id IN (" + ",".join("?" for _ in job_ids) + ")"
                params.extend(job_ids)
            row = self.db.execute("SELECT * FROM jobs WHERE stage=? AND (state='pending' OR (state='running' AND lease_until<?))" + scope + " AND manifestation_id=(SELECT id FROM manifestations m WHERE m.work_id=jobs.work_id ORDER BY rank DESC,CASE WHEN identity LIKE 'arxiv:%' THEN 0 WHEN identity LIKE 'doi:%' THEN 1 ELSE 2 END,id DESC LIMIT 1) ORDER BY id LIMIT 1", params).fetchone()
            if row:
                self.db.execute("UPDATE jobs SET state='running',lease_until=?,attempts=attempts+1,error=NULL WHERE id=?", (now + lease_seconds, row['id']))
                row = self.db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
            self.db.commit()
            return dict(row) if row else None
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, job_id, error=None, *, lease_until=None):
        with self.db:
            if lease_until is not None:
                current = self.db.execute('SELECT lease_until FROM jobs WHERE id=?', (job_id,)).fetchone()
                if current is None or current[0] != lease_until:
                    raise ValueError('Stale job lease')
            changed = self.db.execute("UPDATE jobs SET state=?,error=?,lease_until=NULL WHERE id=? AND state='running' AND lease_until>=? AND (? IS NULL OR lease_until=?)", ('failed' if error else 'done', error, job_id, time.time(), lease_until, lease_until)).rowcount
            if not changed:
                raise ValueError('Job has no active lease')

    def stats(self):
        result = {name: self.db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ('works', 'manifestations', 'source_records', 'extraction_revisions', 'identity_conflicts', 'citation_edges', 'citation_assertions')}
        result['jobs'] = {r[0]: r[1] for r in self.db.execute('SELECT state,count(*) FROM jobs GROUP BY state')}
        return result

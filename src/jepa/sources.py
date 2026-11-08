"""Bounded discovery; no credential discovery, ambient auth or giant downloads.

All functions return DiscoveryResult(status='ok'|'skipped'|'paused'|'failed',
records=[SourceRecord dict], work_ids=[UUID], reason=...). Successful records are
idempotently ingested BEFORE a persisted cursor advances; reruns are crash safe.
Public record fields exactly follow IMPLEMENTATION-CONTRACT (extra provenance
is private). Store.db tables source_cursors/quota_ledger retain UTC progress.

HTTPSources(store, client=None, timeout=30, max_bytes=4_000_000) provides:
 arxiv_search(query, page_size=50), arxiv_ids(ids<=50),
 hf_search(query, limit=50), hf_daily(date=None, limit=100),
 awesome_jepa(url=<curated README>, limit=100),
 openalex_citations(openalex_id, enabled=False, api_key=None, daily_limit=20),
 openalex_references(openalex_id, enabled=False, api_key=None, daily_limit=20, limit=100),
 alphaxiv_discover(query, bearer_token=None, enabled=False).
One HTTP page per call, except explicit MCP initialize + tool invocation. arXiv
calls serialize via persistent 3-second reservation; other clients must still
coordinate independently. OpenAlex incoming edges are provider observations;
no recursive expansion or silent quota fallback. Outgoing reference IDs are
metadata-pending placeholders, not fetched paper bodies or verified citations.
OpenAlex uses a request allocation <=20/day, not an assertion of account balance;
observed usage headers and 429 reset delays persist. Other account users remain
unknown. No retries hidden within a call. Auth failure is explicit failed.

import_arxiv_jsonl(store, path, max_records=1000) streams ONLY an existing local
arXiv metadata JSONL file. Byte offset + inode/size/mtime identity are persisted;
changed files get a new cursor, not reused offsets. Malformed lines stop visibly.
No network or archive extraction. Source functions never execute paper code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree as ET

from .store import arxiv_identity, dumps

AWESOME_JEPA = 'https://raw.githubusercontent.com/AbdelStark/awesome-jepa/main/README.md'


@dataclass
class DiscoveryResult:
    status: str
    records: list[dict] = field(default_factory=list)
    work_ids: list[str] = field(default_factory=list)
    reason: str | None = None


class SourceFailure(Exception):
    """Safe, fixed-message failure; never contains URLs, response bodies or keys."""


def record(source, source_id, *, arxiv_id=None, version=None, doi=None,
           title='', authors=None, published='', updated='', url='', abstract='', license=None, **private):
    return dict(source=source, source_id=str(source_id), arxiv_id=arxiv_id,
                version=version, doi=doi, title=title, authors=authors or [],
                published=published, updated=updated, url=url, abstract=abstract,
                license=license, **private)


def cursor_get(store, source, query, default=None):
    row = store.db.execute('SELECT cursor FROM source_cursors WHERE source=? AND query=?', (source, query)).fetchone()
    return json.loads(row[0]) if row else default


def cursor_set(store, source, query, value):
    with store.db:
        store.db.execute('INSERT INTO source_cursors VALUES (?,?,?,?) ON CONFLICT(source,query) DO UPDATE SET cursor=excluded.cursor,updated=excluded.updated', (source, query, dumps(value), time.time()))


def _save(store, records, raw_response=None, query=None):
    if raw_response is not None:
        digest = hashlib.sha256(raw_response).hexdigest()
        for r in records:
            r['provenance'] = {**r.get('provenance', {}), 'raw_response_sha256': digest}
            if query is not None:
                r['provenance']['query'] = query
    ids = [store.ingest(r) for r in records]
    return DiscoveryResult('ok', records, ids)


def _bound(n, maximum):
    if not isinstance(n, int) or not 1 <= n <= maximum:
        raise ValueError('Invalid discovery page bound')


def _atom(data):
    root = ET.fromstring(data)
    ns = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom', 'o': 'http://a9.com/-/spec/opensearch/1.1/'}
    records = []
    for e in root.findall('a:entry', ns):
        text = lambda name: e.findtext(name, default='', namespaces=ns).strip()
        aid, version = arxiv_identity(text('a:id'))
        if not aid:
            raise SourceFailure('arXiv returned an invalid entry')
        license_node = e.find('x:license', ns)
        records.append(record('arxiv', aid + (version or ''), arxiv_id=aid, version=version,
                              doi=text('x:doi') or None, title=' '.join(text('a:title').split()),
                              authors=[a.findtext('a:name', default='', namespaces=ns) for a in e.findall('a:author', ns)],
                              published=text('a:published'), updated=text('a:updated'),
                              url='https://arxiv.org/abs/' + aid + (version or ''), abstract=text('a:summary'),
                              license=license_node.attrib.get('href') if license_node is not None else None))
    total = root.findtext('o:totalResults', namespaces=ns)
    return records, int(total) if total is not None else None


def _hf(item):
    p = item.get('paper', item)
    aid, version = arxiv_identity(p['id'])
    return record('huggingface', p['id'], arxiv_id=aid, version=version,
                  title=p.get('title', ''), authors=[a['name'] if isinstance(a, dict) else a for a in p.get('authors', [])],
                  published=p.get('publishedAt', ''), updated=p.get('updatedAt', p.get('publishedAt', '')),
                  abstract=p.get('summary', ''), url='https://huggingface.co/papers/' + p['id'])


class HTTPSources:
    def __init__(self, store, client=None, timeout=30, max_bytes=4_000_000):
        if timeout <= 0 or timeout > 120 or max_bytes <= 0 or max_bytes > 16_000_000:
            raise ValueError('Invalid HTTP bounds')
        self.store, self.timeout, self.max_bytes = store, timeout, max_bytes
        self.client = client or httpx.Client(trust_env=False, follow_redirects=False)
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    def _request(self, provider, url, params=None, headers=None, method='GET', payload=None, daily_limit=None):
        allowed = {'export.arxiv.org', 'huggingface.co', 'raw.githubusercontent.com', 'api.openalex.org', 'api.alphaxiv.org'}
        u = urlsplit(url)
        if u.scheme != 'https' or u.hostname not in allowed or u.username or u.password or u.query:
            raise SourceFailure('Unapproved source endpoint')
        day = datetime.now(timezone.utc).date().isoformat()
        db = self.store.db
        db.execute('BEGIN IMMEDIATE')
        try:
            pause = cursor_get(self.store, provider, '_pause_until', 0)
            if pause > time.time():
                raise SourceFailure('Provider paused until persisted reset')
            if daily_limit is not None:
                used = db.execute('SELECT count(*) FROM quota_ledger WHERE provider=? AND day=?', (provider, day)).fetchone()[0]
                if used >= daily_limit:
                    raise SourceFailure('Local daily request allocation exhausted; account balance unknown')
            reserved = time.time()
            if provider == 'arxiv':
                if cursor_get(self.store, provider, '_inflight_until', 0) > reserved:
                    raise SourceFailure('Provider paused: another arXiv request holds the connection lease')
                reserved = max(reserved, cursor_get(self.store, provider, '_next_request', 0))
                db.execute('INSERT INTO source_cursors VALUES (?,?,?,?) ON CONFLICT(source,query) DO UPDATE SET cursor=excluded.cursor,updated=excluded.updated', (provider, '_inflight_until', dumps(reserved + 2 * self.timeout + 10), time.time()))
                db.execute('INSERT INTO source_cursors VALUES (?,?,?,?) ON CONFLICT(source,query) DO UPDATE SET cursor=excluded.cursor,updated=excluded.updated', (provider, '_next_request', dumps(reserved + 3.05), time.time()))
            row = db.execute('INSERT INTO quota_ledger(provider,day,at,outcome) VALUES (?,?,?,?)', (provider, day, reserved, 'reserved'))
            ledger_id = row.lastrowid
            db.commit()
        except BaseException:
            db.rollback()
            raise
        if reserved > time.time():
            time.sleep(reserved - time.time())
        outcome, usage = 'network_failure', {}
        started = time.monotonic()
        try:
            # No response/request objects or exception details escape this boundary.
            with self.client.stream(method, url, params=params, headers=headers, json=payload,
                                    timeout=self.timeout, follow_redirects=False) as response:
                outcome = str(response.status_code)
                usage = {k: v for k, v in response.headers.items() if k.lower().startswith(('x-ratelimit', 'ratelimit', 'x-api-usage'))}
                # A reported zero balance pauses subsequent requests even on HTTP 200.
                for name, value in usage.items():
                    # Zero prepaid/one-time balance does not exhaust the free daily allocation.
                    if name.lower() in ('x-ratelimit-remaining', 'x-ratelimit-remaining-usd', 'ratelimit-remaining'):
                        try:
                            exhausted = float(value) <= 0
                        except ValueError:
                            exhausted = False
                        if exhausted:
                            tomorrow = (int(time.time()) // 86400 + 1) * 86400
                            cursor_set(self.store, provider, '_pause_until', tomorrow)
                if response.status_code == 429:
                    value = response.headers.get('retry-after', '')
                    try:
                        delay = float(value)
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(value).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            delay = 3600
                    cursor_set(self.store, provider, '_pause_until', time.time() + max(1, min(delay, 86400)))
                    raise SourceFailure('Provider throttled; reset persisted')
                if response.status_code >= 300:
                    raise SourceFailure('Source HTTP failure (' + str(response.status_code) + ')')
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > self.timeout:
                        raise SourceFailure('Source total response time exceeded bound')
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise SourceFailure('Source response exceeded byte bound')
                return bytes(body), response.headers
        except SourceFailure:
            raise
        except Exception:
            raise SourceFailure('Source transport failed; request details suppressed') from None
        finally:
            if provider == 'arxiv':
                cursor_set(self.store, provider, '_inflight_until', 0)
            with db:
                db.execute('UPDATE quota_ledger SET outcome=?,usage=? WHERE id=?', (outcome, dumps(usage), ledger_id))

    def _run(self, fn):
        try:
            return fn()
        except SourceFailure as e:
            message = str(e)
            status = 'paused' if any(word in message for word in ('allocation', 'paused', 'throttled')) else 'failed'
            return DiscoveryResult(status, reason=message)
        except Exception:
            return DiscoveryResult('failed', reason='Source response invalid; details suppressed')

    def arxiv_search(self, query, page_size=50):
        _bound(page_size, 100)
        key = dumps({'query': query, 'size': page_size, 'sort': 'lastUpdatedDate:ascending'})
        def run():
            state = cursor_get(self.store, 'arxiv', key, {'offset': 0, 'done': False})
            if state['done']:
                return DiscoveryResult('skipped', reason='Cached search exhausted; use a new date-window query to refresh')
            body, _ = self._request('arxiv', 'https://export.arxiv.org/api/query', params={'search_query': query, 'start': state['offset'], 'max_results': page_size, 'sortBy': 'lastUpdatedDate', 'sortOrder': 'ascending'})
            records, total = _atom(body)
            result = _save(self.store, records, body, query)
            offset = state['offset'] + len(records)
            cursor_set(self.store, 'arxiv', key, {'offset': offset, 'done': offset >= total if total is not None else len(records) < page_size})
            return result
        return self._run(run)

    def arxiv_ids(self, ids):
        _bound(len(ids), 50)
        normalized = []
        for value in ids:
            aid, version = arxiv_identity(value)
            if not aid:
                raise ValueError('Empty arXiv ID')
            normalized.append(aid + (version or ''))
        def run():
            data, _ = self._request('arxiv', 'https://export.arxiv.org/api/query', params={'id_list': ','.join(normalized), 'max_results': len(ids)})
            records, _ = _atom(data)
            return _save(self.store, records, data, ','.join(normalized))
        return self._run(run)

    def hf_search(self, query, limit=50):
        _bound(limit, 100)
        def run():
            body, _ = self._request('huggingface', 'https://huggingface.co/api/papers/search', params={'q': query, 'limit': limit})
            items = json.loads(body)
            if not isinstance(items, list) or len(items) > limit:
                raise SourceFailure('HF search exceeded requested bound or schema changed')
            result = _save(self.store, [_hf(x) for x in items], body)
            cursor_set(self.store, 'huggingface', 'search:' + query, {'retrieved': time.time(), 'count': len(items), 'pagination': 'not_exposed'})
            return result
        return self._run(run)

    def hf_daily(self, date=None, limit=100):
        _bound(limit, 100)
        if date is not None:
            datetime.strptime(date, '%Y-%m-%d')
        date = date or datetime.now(timezone.utc).date().isoformat()
        key = dumps({'date': date, 'limit': limit})
        def run():
            state = cursor_get(self.store, 'huggingface-daily', key, {'page': 0, 'done': False})
            if state['done']:
                return DiscoveryResult('skipped', reason='Daily page cursor exhausted; refresh explicitly to revisit changing feeds')
            body, _ = self._request('huggingface', 'https://huggingface.co/api/daily_papers', params={'date': date, 'p': state['page'], 'limit': limit})
            items = json.loads(body)
            if not isinstance(items, list) or len(items) > limit:
                raise SourceFailure('HF daily schema or page bound changed')
            result = _save(self.store, [_hf(x) for x in items], body)
            cursor_set(self.store, 'huggingface-daily', key, {'page': state['page'] + 1, 'done': len(items) < limit})
            return result
        return self._run(run)

    def awesome_jepa(self, url=AWESOME_JEPA, limit=100):
        _bound(limit, 1000)
        if not re.fullmatch(r'https://raw\.githubusercontent\.com/[\w.-]+/[\w.-]+/[\w./-]+', url):
            raise ValueError('Curated list must be a public raw GitHub path')
        def run():
            body, _ = self._request('curated', url)
            digest = hashlib.sha256(body).hexdigest()
            ids = list(dict.fromkeys(re.findall(r'https?://arxiv\.org/(?:abs|pdf)/([\w./-]+)', body.decode('utf-8'))))
            offset = cursor_get(self.store, 'curated', digest, 0)
            records = []
            for raw in ids[offset:offset + limit]:
                aid, version = arxiv_identity(raw)
                records.append(record('awesome-jepa', aid + (version or ''), arxiv_id=aid, version=version,
                                      url='https://arxiv.org/abs/' + aid + (version or ''),
                                      provenance={'list_url': url, 'sha256': digest, 'metadata_pending': True}))
            result = _save(self.store, records)
            cursor_set(self.store, 'curated', digest, offset + len(records))
            return result
        return self._run(run)

    def openalex_resolve(self, arxiv_id, title, *, api_key=None, daily_limit=20):
        """One title-search request; accept only an explicit matching arXiv location."""
        if not api_key:
            return DiscoveryResult('skipped', reason='Explicit OpenAlex key required')
        _bound(daily_limit, 20)
        aid, _ = arxiv_identity(arxiv_id)
        def run():
            body, _ = self._request('openalex', 'https://api.openalex.org/works',
                params={'search': title, 'per-page': 25, 'api_key': api_key}, daily_limit=daily_limit)
            matches=[]
            for p in json.loads(body)['results']:
                for location in p.get('locations', []):
                    link=location.get('landing_page_url') or ''
                    if re.match(r'https?://arxiv\.org/abs/', link):
                        linked, version=arxiv_identity(link)
                        if linked==aid:
                            matches.append(record('openalex',p['id'],arxiv_id=aid,version=version,
                                doi=p.get('doi'),title=p.get('title') or '',
                                authors=[a['author']['display_name'] for a in p.get('authorships',[])],
                                published=p.get('publication_date') or '',updated=p.get('updated_date') or '',
                                url=p['id'],license='https://creativecommons.org/publicdomain/zero/1.0/'))
                            break
            if len(matches)!=1:
                return DiscoveryResult('skipped',reason='No unique explicit arXiv mapping; title similarity does not authorize merge')
            return _save(self.store,matches,body,'resolve-arxiv:'+aid)
        return self._run(run)

    def openalex_citations(self, openalex_id, *, enabled=False, api_key=None, daily_limit=20):
        if not enabled or not api_key:
            return DiscoveryResult('skipped', reason='OpenAlex opt-in and explicit key required; no request made')
        _bound(daily_limit, 20)
        oid = openalex_id.removeprefix('https://openalex.org/')
        if not re.fullmatch(r'W\d+', oid):
            raise ValueError('Invalid OpenAlex work ID')
        def run():
            cursor = cursor_get(self.store, 'openalex', oid, '*')
            if cursor is None:
                return DiscoveryResult('skipped', reason='Citation cursor exhausted')
            body, _ = self._request('openalex', 'https://api.openalex.org/works', params={'filter': 'cites:' + oid, 'per-page': 100, 'cursor': cursor, 'api_key': api_key}, daily_limit=daily_limit)
            data = json.loads(body)
            records = []
            for p in data['results']:
                aid = version = None
                for location in p.get('locations', []):
                    link = location.get('landing_page_url') or ''
                    if re.match(r'https?://arxiv\.org/abs/', link):
                        aid, version = arxiv_identity(link)
                        break
                records.append(record('openalex', p['id'], arxiv_id=aid, version=version, doi=p.get('doi'), title=p.get('title') or '',
                                      authors=[a['author']['display_name'] for a in p.get('authorships', [])],
                                      published=p.get('publication_date') or '', updated=p.get('updated_date') or '',
                                      url=p['id'], license='https://creativecommons.org/publicdomain/zero/1.0/',
                                      provenance={'relation': 'cites', 'target_openalex': oid, 'account_remaining': 'unknown'}))
            result = _save(self.store, records, body, 'cites:' + oid)
            with self.store.db:
                for r in records:
                    self.store.db.execute('INSERT OR IGNORE INTO citation_assertions(provider,source_id,target_id,observed) VALUES (?,?,?,?)', ('openalex', r['source_id'], 'https://openalex.org/' + oid, time.time()))
            target = self.store.db.execute("SELECT work_id FROM identities WHERE kind='provider:openalex' AND value=?", ('https://openalex.org/' + oid,)).fetchone()
            if target:
                with self.store.db:
                    for wid in result.work_ids:
                        self.store.db.execute('INSERT OR IGNORE INTO citation_edges(source,target,provider) VALUES (?,?,?)', (wid, target[0], 'openalex'))
            cursor_set(self.store, 'openalex', oid, data.get('meta', {}).get('next_cursor'))
            return result
        return self._run(run)

    def openalex_references(self, openalex_id, *, enabled=False, api_key=None, daily_limit=20, limit=100):
        """One metadata request, then resumable local batches of asserted reference IDs.

        No extra per-reference requests; callers may enrich metadata separately.
        The cached reference list is an observation, not live graph completeness.
        """
        if not enabled or not api_key:
            return DiscoveryResult('skipped', reason='OpenAlex opt-in and explicit key required; no request made')
        _bound(daily_limit, 20)
        _bound(limit, 100)
        oid = openalex_id.removeprefix('https://openalex.org/')
        if not re.fullmatch(r'W\d+', oid):
            raise ValueError('Invalid OpenAlex work ID')
        def run():
            state = cursor_get(self.store, 'openalex-references', oid)
            if state is None:
                body, _ = self._request('openalex', 'https://api.openalex.org/works/' + oid, params={'api_key': api_key}, daily_limit=daily_limit)
                p = json.loads(body)
                if p['id'] != 'https://openalex.org/' + oid:
                    raise SourceFailure('OpenAlex returned unexpected identity')
                refs = p.get('referenced_works', [])
                if not isinstance(refs, list) or any(not re.fullmatch(r'https://openalex.org/W\d+', x) for x in refs):
                    raise SourceFailure('OpenAlex reference schema invalid')
                root = record('openalex', p['id'], doi=p.get('doi'), title=p.get('title') or '',
                              authors=[a['author']['display_name'] for a in p.get('authorships', [])],
                              published=p.get('publication_date') or '', updated=p.get('updated_date') or '',
                              url=p['id'], license='https://creativecommons.org/publicdomain/zero/1.0/')
                _save(self.store, [root], body, 'references:' + oid)
                state = {'references': refs, 'offset': 0, 'response_sha256': hashlib.sha256(body).hexdigest()}
                cursor_set(self.store, 'openalex-references', oid, state)
            refs = state['references'][state['offset']:state['offset'] + limit]
            if not refs:
                return DiscoveryResult('skipped', reason='Cached reference cursor exhausted')
            records = [record('openalex', rid, url=rid, license='https://creativecommons.org/publicdomain/zero/1.0/',
                              provenance={'metadata_pending': True, 'relation': 'cited_by', 'source_openalex': oid,
                                          'raw_response_sha256': state['response_sha256']}) for rid in refs]
            result = _save(self.store, records)
            with self.store.db:
                for rid in refs:
                    self.store.db.execute('INSERT OR IGNORE INTO citation_assertions(provider,source_id,target_id,observed) VALUES (?,?,?,?)', ('openalex', 'https://openalex.org/' + oid, rid, time.time()))
                self.store.db.execute("""INSERT OR IGNORE INTO citation_edges(source,target,provider,relation)
                    SELECT a.work_id,b.work_id,c.provider,c.relation FROM citation_assertions c
                    JOIN identities a ON a.kind='provider:'||c.provider AND a.value=c.source_id
                    JOIN identities b ON b.kind='provider:'||c.provider AND b.value=c.target_id""")
            state['offset'] += len(refs)
            cursor_set(self.store, 'openalex-references', oid, state)
            return result
        return self._run(run)

    def alphaxiv_discover(self, query, *, bearer_token=None, enabled=False):
        if not enabled or not bearer_token:
            return DiscoveryResult('skipped', reason='alphaXiv MCP not configured; explicit bearer token and opt-in required')
        # Tool response schemas and OAuth negotiation are not assumed stable. Persist
        # official discovery output for inspection only when it contains explicit IDs.
        def run():
            headers = {'Authorization': 'Bearer ' + bearer_token, 'Accept': 'application/json, text/event-stream'}
            endpoint = 'https://api.alphaxiv.org/mcp/v1'
            def rpc(payload):
                body, response_headers = self._request('alphaxiv', endpoint, headers=headers, method='POST', payload=payload)
                session = response_headers.get('mcp-session-id')
                if session:
                    headers['Mcp-Session-Id'] = session
                text = body.decode()
                if text.startswith('event:') or text.startswith('data:'):
                    messages = [json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith('data:')]
                    return next(x for x in messages if x.get('id') == payload.get('id'))
                return json.loads(text) if text else {}
            hello = rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'jepa-discovery', 'version': '0.1'}}})
            if 'error' in hello or 'result' not in hello:
                raise SourceFailure('alphaXiv MCP initialization failed')
            headers['MCP-Protocol-Version'] = hello['result'].get('protocolVersion', '2024-11-05')
            rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            tools = rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            tool = next((t for t in tools.get('result', {}).get('tools', []) if t['name'] == 'discover_papers'), None)
            if not tool:
                return DiscoveryResult('skipped', reason='Official MCP discover_papers unavailable')
            schema = tool.get('inputSchema', {})
            if 'query' not in schema.get('properties', {}) or set(schema.get('required', [])) - {'query'}:
                return DiscoveryResult('skipped', reason='Official MCP discovery schema requires unsupported configuration')
            data = rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'discover_papers', 'arguments': {'query': query}}})
            if 'error' in data or data.get('result', {}).get('isError'):
                raise SourceFailure('alphaXiv discovery tool failed')
            # Only explicit canonical arXiv links count, never AI bibliography guesses.
            text = dumps(data.get('result', {}))
            ids = list(dict.fromkeys(re.findall(r'https://arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)', text)))[:15]
            result = _save(self.store, [record('alphaxiv', x, arxiv_id=arxiv_identity(x)[0], version=arxiv_identity(x)[1], url='https://arxiv.org/abs/' + x, provenance={'metadata_pending': True}) for x in ids])
            result.reason = 'Ranked MCP suggestions require arXiv metadata verification; not primary evidence'
            return result
        return self._run(run)


def import_arxiv_jsonl(store, path, max_records=1000, query=None, scan_limit=100_000):
    _bound(max_records, 100_000)
    _bound(scan_limit, 1_000_000)
    path = Path(path)
    st = path.stat()
    key = hashlib.sha256(dumps([str(path.resolve()), st.st_ino, st.st_size, st.st_mtime_ns, query.casefold() if query else None]).encode()).hexdigest()
    offset = cursor_get(store, 'arxiv-local', key, 0)
    result = DiscoveryResult('ok')
    with path.open('rb') as f:
        f.seek(offset)
        accepted = 0
        for scanned in range(scan_limit):
            if accepted >= max_records:
                break
            line = f.readline(2_000_001)
            if not line:
                break
            if len(line) > 2_000_000:
                result.status, result.reason = 'failed', 'Local metadata line exceeded byte bound'
                break
            try:
                p = json.loads(line)
                text = ' '.join(str(p.get(k, '')) for k in ('title', 'abstract', 'categories'))
                if query and query.casefold() not in text.casefold():
                    offset = f.tell()
                    if scanned % 1000 == 0:
                        cursor_set(store, 'arxiv-local', key, offset)
                    continue
                aid, embedded = arxiv_identity(p['id'])
                versions = p.get('versions') or []
                # Snapshot version arrays explicitly assert versions; preserve all.
                for v in versions or [{'version': embedded, 'created': p.get('update_date', '')}]:
                    version = v.get('version')
                    authors = [' '.join(x for x in [a[1], a[0], a[2] if len(a) > 2 else ''] if x).strip() for a in p.get('authors_parsed', [])]
                    r = record('arxiv-local', aid + (version or ''), arxiv_id=aid, version=version,
                               doi=p.get('doi'), title=p.get('title', ''), authors=authors,
                               published=_snapshot_date(versions[0].get('created', '')) if versions else '',
                               updated=_snapshot_date(v.get('created', '')), abstract=p.get('abstract', ''),
                               url='https://arxiv.org/abs/' + aid + (version or ''), license=p.get('license'),
                               provenance={'snapshot': key, 'byte_offset': offset, 'metadata_scope': 'snapshot-current; historical text not asserted'})
                    result.work_ids.append(store.ingest(r))
                    result.records.append(r)
            except Exception:
                result.status, result.reason = 'failed', 'Invalid local metadata record; cursor retained at failing line'
                break
            offset = f.tell()
            accepted += 1
            cursor_set(store, 'arxiv-local', key, offset)
    cursor_set(store, 'arxiv-local', key, offset)
    return result


def _snapshot_date(value):
    if not value:
        return ''
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return datetime.fromisoformat(value).isoformat()

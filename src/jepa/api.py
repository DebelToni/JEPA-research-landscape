"""Read-only agent queries over one immutable public catalogue, never the working DB."""
from collections import Counter, defaultdict
import hashlib
import json
import re
import sqlite3
import threading
from urllib.parse import parse_qsl, urlencode


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def parameters(query, allowed):
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=20)
    except ValueError:
        raise APIError(400, 'invalid_query', 'Malformed query or too many parameters.') from None
    values = dict(pairs)
    if len(values) != len(pairs) or set(values) - set(allowed):
        raise APIError(400, 'invalid_query', 'Unknown or repeated query parameter. See /openapi.json.')
    return values


def integer(values, key, default, low, high):
    try:
        value = int(values.get(key, default))
        if value < low or (high is not None and value > high):
            raise ValueError
        return value
    except ValueError:
        bounds = f'from {low} to {high}' if high is not None else f'at least {low}'
        raise APIError(400, 'invalid_query', f'{key} must be an integer {bounds}.') from None


def boolean(values, key):
    value = values.get(key)
    if value not in (None, 'true', 'false'):
        raise APIError(400, 'invalid_query', f'{key} must be true or false.')
    return None if value is None else value == 'true'


def choice(values, key, default, options):
    value = values.get(key, default)
    if value not in options:
        raise APIError(400, 'invalid_query', f'{key} must be one of: {", ".join(options)}.')
    return value


def scientific_text(extraction):
    texts = [c['text'] for claims in extraction.get('sections', {}).values() for c in claims]
    for finding in extraction.get('findings', []):
        texts.extend([finding['name'], finding['interpretation']['text']])
        texts.extend(c['text'] for c in finding.get('caveats', []))
        for result in finding.get('key_results', []):
            texts.extend(str(value) for key, value in result.items() if key not in ('id', 'evidence') and value is not None)
    return ' '.join(texts)


class Snapshot:
    def __init__(self, release):
        catalog_path = (release / 'data/catalog.json').resolve()
        manifest_path = (release / 'data/manifest.json').resolve()
        if not catalog_path.is_relative_to(release) or not manifest_path.is_relative_to(release):
            raise ValueError('Public snapshot path escapes publication')
        data = catalog_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        manifest = json.loads(manifest_path.read_text())
        if manifest['files']['catalog.json'] != digest:
            raise ValueError('Catalogue hash mismatch')
        catalog = json.loads(data)
        self.meta = {'id': digest, 'generated_at': catalog['generated_at'], 'schema_version': catalog['schema_version']}
        self.taxonomy = catalog.get('taxonomy')
        self.category_nodes = {n['id']: n for n in (self.taxonomy or {}).get('nodes', [])}
        self.coverage = catalog['coverage']
        self.papers = catalog['papers']
        self.by_id = {p['work_id']: p for p in self.papers}
        self.paper_categories = tuple(frozenset(self.categories(p)) for p in self.papers)
        counts = Counter({category: 0 for category in self.category_nodes})
        for categories in self.paper_categories:
            counts.update(categories)
        self.counts = dict(sorted(counts.items()))
        self.summaries = {p['work_id']: self.summary(p) for p in self.papers}
        self.facet_response = self._build_facets()
        self.edges = catalog['edges']
        self.outgoing, self.incoming = defaultdict(list), defaultdict(list)
        for i, edge in enumerate(self.edges):
            self.outgoing[edge['source']].append(i)
            self.incoming[edge['target']].append(i)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute("CREATE VIRTUAL TABLE search USING fts5(title,keywords,summary,body,metadata,tokenize='unicode61 remove_diacritics 2')")
        for i, paper in enumerate(self.papers):
            e = paper.get('extraction') or {}
            self.db.execute('INSERT INTO search(rowid,title,keywords,summary,body,metadata) VALUES(?,?,?,?,?,?)',
                            (i+1, paper['title'], ' '.join((paper.get('topics') or []) + e.get('keywords', [])),
                             e.get('summary', ''), scientific_text(e),
                             ' '.join((paper.get('authors') or []) + [paper.get(k) or '' for k in ('arxiv_id', 'doi', 'work_id')])))
        self.db.commit()
        self.db.execute('PRAGMA query_only=ON')

    def categories(self, paper):
        """Unique assigned nodes and ancestors, following the published hierarchy."""
        result = set()
        for category in paper.get('category_ids') or []:
            while category in self.category_nodes and category not in result:
                result.add(category)
                category = self.category_nodes[category]['parent']
        return result

    def category_counts(self):
        return self.counts

    def taxonomy_response(self):
        return {'snapshot': self.meta, 'taxonomy': self.taxonomy, 'counts': self.category_counts()}

    def links(self, work_id):
        query = urlencode({'snapshot': self.meta['id']})
        return {'api': f'/api/papers/{work_id}?{query}', 'citations': f'/api/papers/{work_id}/citations?{query}',
                'html': f'/papers/{work_id}/', 'json': f'/data/papers/{work_id}.json', 'markdown': f'/data/papers/{work_id}.md'}

    def summary(self, paper):
        e = paper.get('extraction') or {}
        q = e.get('quality')
        return {**{k: paper.get(k) for k in ('work_id', 'title', 'authors', 'published', 'arxiv_id', 'doi', 'version', 'relevance', 'topics', 'status', 'category_ids', 'classification')},
                'has_extraction': paper.get('extraction') is not None,
                'summary': e.get('summary', '')[:600],
                'quality': {k: q.get(k) for k in ('score', 'coverage', 'rubric')} if q else None,
                'links': self.links(paper['work_id'])}

    def page(self, path, params, items, limit, offset):
        next_url = None
        if offset + limit < len(items):
            next_url = path + '?' + urlencode({**params, 'limit': limit, 'offset': offset+limit, 'snapshot': self.meta['id']})
        return {'snapshot': self.meta, 'total': len(items), 'limit': limit, 'offset': offset,
                'next': next_url, 'results': items[offset:offset+limit]}

    def search(self, params):
        raw_query = params.get('q', '')
        q = raw_query.strip()
        terms = re.findall(r'[^\W_]+', q)
        if len(raw_query) > 500 or len(terms) > 32 or (q and not terms):
            raise APIError(400, 'invalid_query', 'q needs searchable words, at most 500 characters and 32 terms.')
        limit = integer(params, 'limit', 20, 1, 100)
        offset = integer(params, 'offset', 0, 0, None)
        year_from = integer(params, 'year_from', 1000, 1000, 9999)
        year_to = integer(params, 'year_to', 9999, 1000, 9999)
        if year_from > year_to:
            raise APIError(400, 'invalid_query', 'year_from must not exceed year_to.')
        extracted, rated = boolean(params, 'extracted'), boolean(params, 'rated')
        relevance = choice(params, 'relevance', None, ('direct', 'adjacent', 'context', 'uncertain')) if 'relevance' in params else None
        category = params.get('category')
        if category is not None and category not in self.category_nodes:
            raise APIError(400, 'invalid_query', f'Unknown category ID: {category}. See /api/taxonomy.')
        classification = choice(params, 'classification', None, ('classified', 'needs_review', 'insufficient_metadata', 'pending')) if 'classification' in params else None
        sort = choice(params, 'sort', 'relevance' if q else 'newest', ('relevance', 'newest', 'score'))
        min_score = None
        if 'min_score' in params:
            try:
                min_score = float(params['min_score'])
                if not 0 <= min_score <= 100:
                    raise ValueError
            except ValueError:
                raise APIError(400, 'invalid_query', 'min_score must be a finite number from 0 to 100.') from None
        if q:
            # User input is plain text, not FTS operators or SQL. All terms must match.
            match = ' AND '.join('"'+term+'"' for term in terms)
            with self.lock:
                indices = [r[0]-1 for r in self.db.execute('SELECT rowid FROM search WHERE search MATCH ? ORDER BY bm25(search,8,4,3,1,2),rowid', (match,))]
        else:
            indices = range(len(self.papers))
        matches = []
        for i in indices:
            p = self.papers[i]
            e = p.get('extraction')
            score = ((e or {}).get('quality') or {}).get('score')
            year = str(p.get('published') or '')[:4]
            if extracted is not None and (e is not None) != extracted: continue
            if rated is not None and (score is not None) != rated: continue
            if relevance and p['relevance'] != relevance: continue
            if category is not None and category not in self.paper_categories[i]: continue
            if classification is not None and (p.get('classification') or {}).get('status') != classification: continue
            if 'topic' in params and params['topic'] not in (p.get('topics') or []): continue
            if 'source' in params and params['source'] not in (p.get('sources') or []): continue
            if ('year_from' in params or 'year_to' in params) and (not year.isdigit() or not year_from <= int(year) <= year_to): continue
            if min_score is not None and (score is None or score < min_score): continue
            matches.append(p)
        if sort == 'newest' or (sort == 'relevance' and not q):
            matches.sort(key=lambda p: (p.get('published') or '', p['work_id']), reverse=True)
        elif sort == 'score':
            def score_key(p):
                score = (((p.get('extraction') or {}).get('quality')) or {}).get('score')
                return (score is None, -score if score is not None else 0, p['work_id'])
            matches.sort(key=score_key)
        result = self.page('/api/search', params, matches, limit, offset)
        result['results'] = [self.summaries[p['work_id']] for p in result['results']]
        result.update(query=q, sort=sort)
        return result

    def facets(self):
        return self.facet_response

    def _build_facets(self):
        counters = {key: Counter() for key in ('relevance', 'topic', 'source', 'year')}
        extracted = rated = 0
        for p in self.papers:
            counters['relevance'][p['relevance']] += 1
            counters['topic'].update(set(p.get('topics') or []))
            counters['source'].update(set(p.get('sources') or []))
            year = str(p.get('published') or '')[:4]
            if year.isdigit(): counters['year'][year] += 1
            e = p.get('extraction')
            extracted += e is not None
            rated += (((e or {}).get('quality')) or {}).get('score') is not None
        counters['category'] = self.category_counts()
        return {'snapshot': self.meta, 'total': len(self.papers), 'extracted': extracted, 'rated': rated,
                'facets': {key: dict(sorted(counter.items())) for key, counter in counters.items()}}

    def citations(self, work_id, params):
        direction = choice(params, 'direction', 'both', ('both', 'outgoing', 'incoming'))
        limit = integer(params, 'limit', 20, 1, 100)
        offset = integer(params, 'offset', 0, 0, None)
        indices = set()
        if direction != 'incoming': indices.update(self.outgoing[work_id])
        if direction != 'outgoing': indices.update(self.incoming[work_id])
        result = self.page(f'/api/papers/{work_id}/citations', params, sorted(indices), limit, offset)
        result['results'] = [{'edge': self.edges[i], 'paper': self.summaries[self.edges[i]['target'] if self.edges[i]['source'] == work_id else self.edges[i]['source']]} for i in result['results']]
        result.update(work_id=work_id, direction=direction)
        return result


class AgentAPI:
    """A single cached public index per server; publication switches reload atomically."""
    def __init__(self):
        self.lock = threading.Lock()
        self.release = self.snapshot = None

    def current(self, release):
        # Handler resolves the publication once, so every response uses one release.
        with self.lock:
            if release != self.release:
                try:
                    snapshot = Snapshot(release)
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                    raise APIError(503, 'snapshot_unavailable', 'Published catalogue is unavailable or invalid.') from None
                self.release, self.snapshot = release, snapshot
            return self.snapshot

    def respond(self, path, query, release):
        path = path.rstrip('/')
        match = re.fullmatch(r'/api/papers/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(/citations)?', path)
        if path == '/api/search':
            allowed = ('q', 'relevance', 'category', 'classification', 'topic', 'source', 'extracted', 'rated', 'year_from', 'year_to', 'min_score', 'sort', 'limit', 'offset', 'snapshot')
        elif match:
            allowed = ('direction', 'limit', 'offset', 'snapshot') if match[2] else ('snapshot',)
        elif path in ('/api', '/api/facets', '/api/taxonomy'):
            allowed = ('snapshot',)
        else:
            raise APIError(404, 'not_found', 'Unknown API endpoint. See /llms.txt.')
        params = parameters(query, allowed)
        snapshot = self.current(release)
        if 'snapshot' in params and params['snapshot'] != snapshot.meta['id']:
            raise APIError(409, 'snapshot_changed', 'Published snapshot changed. Restart from /api/search or /api.')
        if path == '/api/search': return snapshot.search(params)
        if path == '/api/taxonomy': return snapshot.taxonomy_response()
        if path == '/api/facets': return snapshot.facets()
        if path == '/api':
            return {'service': 'jepa-landscape', 'api_version': '1', 'snapshot': snapshot.meta, 'coverage': snapshot.coverage,
                    'endpoints': {'search': '/api/search', 'facets': '/api/facets', 'taxonomy': '/api/taxonomy', 'paper': '/api/papers/{work_id}', 'citations': '/api/papers/{work_id}/citations', 'instructions': '/llms.txt', 'openapi': '/openapi.json', 'bulk_papers': '/data/papers.jsonl', 'bulk_catalogue': '/data/catalog.json', 'manifest': '/data/manifest.json', 'sitemap': '/sitemap.xml'},
                    'limitations': ['AI assessments are provisional; source matching is not semantic or human verification.', 'Metadata-only and partial records are included. Missing scores are unknown, not zero.', 'Citation edges are observed links within this published collection, not a complete citation graph.', 'Search is lexical, not semantic. Working data appears only after publication.']}
        paper = snapshot.by_id.get(match[1])
        if paper is None:
            raise APIError(404, 'paper_not_found', 'Paper is not present in this published snapshot.')
        if match[2]: return snapshot.citations(match[1], params)
        return {'snapshot': snapshot.meta, 'paper': paper, 'links': snapshot.links(match[1])}

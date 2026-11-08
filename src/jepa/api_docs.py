"""OpenAPI 3.1 contract for the public snapshot-backed API."""


def ref(name):
    return {'$ref': '#/components/schemas/'+name}


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required)}


def array(items):
    return {'type': 'array', 'items': items}


STRING = {'type': 'string'}
NULLABLE_STRING = {'type': ['string', 'null']}
SNAPSHOT = obj({'id': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'generated_at': STRING, 'schema_version': STRING}, ('id', 'generated_at', 'schema_version'))
QUALITY = {'anyOf': [{'type': 'null'}, obj({'score': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 100}, 'coverage': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'rubric': STRING}, ('score', 'coverage', 'rubric'))]}
LINKS = obj({name: {'type': 'string', 'format': 'uri-reference'} for name in ('api', 'citations', 'html', 'json', 'markdown')}, ('api', 'citations', 'html', 'json', 'markdown'))
CLASSIFICATION = {'anyOf': [{'type': 'null'}, obj({
    'taxonomy_version': STRING, 'status': {'type': 'string', 'enum': ['classified', 'needs_review', 'insufficient_metadata', 'pending']},
    'category_ids': array(STRING), 'reason': STRING, 'basis': array(STRING), 'model': NULLABLE_STRING,
    'human_reviewed': {'type': 'boolean'}, 'created_at': STRING, 'input_sha256': STRING,
    'source_sha256': STRING, 'prompt_sha256': STRING,
}, ('taxonomy_version', 'status', 'category_ids', 'reason', 'basis', 'model', 'human_reviewed'))]}
TAXONOMY = {'anyOf': [{'type': 'null'}, obj({'version': STRING, 'nodes': array(obj({
    'id': STRING, 'parent': NULLABLE_STRING, 'label': STRING, 'description': STRING,
}, ('id', 'parent', 'label', 'description')))}, ('version', 'nodes'))]}
BIBLIOGRAPHY = {'work_id': {'type': 'string', 'format': 'uuid'}, 'title': STRING, 'authors': array(STRING),
                **{name: NULLABLE_STRING for name in ('published', 'arxiv_id', 'doi', 'version')},
                'relevance': STRING, 'topics': array(STRING), 'status': STRING,
                'category_ids': {'type': ['array', 'null'], 'items': STRING}, 'classification': CLASSIFICATION}
SUMMARY = obj({**BIBLIOGRAPHY, 'has_extraction': {'type': 'boolean'}, 'summary': {'type': 'string', 'maxLength': 600}, 'quality': QUALITY, 'links': ref('Links')}, tuple(BIBLIOGRAPHY)+('has_extraction', 'summary', 'quality', 'links'))
PAPER = obj({**BIBLIOGRAPHY, 'updated': NULLABLE_STRING, 'url': STRING, 'sources': array(STRING),
             'extraction': {'anyOf': [{'type': 'null'}, {'$ref': 'https://jepa.toni.foo/data/schema.json'}]}}, ('work_id', 'title', 'extraction'))
EDGE = obj({name: STRING for name in ('source', 'target', 'provider', 'relation')}, ('source', 'target', 'provider', 'relation'))
PAGE = {'snapshot': ref('Snapshot'), 'total': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer'}, 'offset': {'type': 'integer'}, 'next': {'type': ['string', 'null'], 'format': 'uri-reference'}}
SCHEMAS = {
    'Taxonomy': obj({'snapshot': ref('Snapshot'), 'taxonomy': TAXONOMY, 'counts': {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0}}}, ('snapshot', 'taxonomy', 'counts')),
    'Snapshot': SNAPSHOT, 'Links': LINKS, 'Summary': SUMMARY, 'Paper': PAPER, 'Edge': EDGE,
    'Error': obj({'error': obj({'code': STRING, 'message': STRING}, ('code', 'message'))}, ('error',)),
    'Search': obj({**PAGE, 'query': STRING, 'sort': STRING, 'results': array(ref('Summary'))}, tuple(PAGE)+('query', 'sort', 'results')),
    'PaperResponse': obj({'snapshot': ref('Snapshot'), 'paper': ref('Paper'), 'links': ref('Links')}, ('snapshot', 'paper', 'links')),
    'Citations': obj({**PAGE, 'work_id': STRING, 'direction': STRING, 'results': array(obj({'edge': ref('Edge'), 'paper': ref('Summary')}, ('edge', 'paper')))}, tuple(PAGE)+('work_id', 'direction', 'results')),
    'Facets': obj({'snapshot': ref('Snapshot'), 'total': {'type': 'integer'}, 'extracted': {'type': 'integer'}, 'rated': {'type': 'integer'},
                   'facets': obj({name: {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0}} for name in ('category', 'topic', 'source', 'relevance', 'year')}, ('category', 'topic', 'source', 'relevance', 'year'))}, ('snapshot', 'total', 'extracted', 'rated', 'facets')),
    'Overview': obj({'service': STRING, 'api_version': STRING, 'snapshot': ref('Snapshot'), 'coverage': {'type': 'object'}, 'endpoints': {'type': 'object', 'additionalProperties': STRING}, 'limitations': array(STRING)}, ('service', 'api_version', 'snapshot', 'coverage', 'endpoints', 'limitations')),
}


def parameter(name, schema, description, location='query'):
    return {'name': name, 'in': location, 'required': location == 'path', 'schema': schema, 'description': description}


PIN = parameter('snapshot', {'type': 'string'}, 'Optional catalogue hash from a previous response; mismatch returns 409.')
PAGING = [parameter('limit', {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}, 'Maximum results per page.'),
          parameter('offset', {'type': 'integer', 'minimum': 0, 'default': 0}, 'Zero-based offset.')]
WORK = parameter('work_id', {'type': 'string', 'format': 'uuid'}, 'Published work UUID from search results.', 'path')
SEARCH = [parameter('q', {'type': 'string', 'maxLength': 500}, 'Plain lexical search, all words required; at most 32 terms. No query operators.'),
          parameter('relevance', {'type': 'string', 'enum': ['direct', 'adjacent', 'context', 'uncertain']}, 'Relevance classification.'),
          parameter('category', STRING, 'Preferred stable category ID from /api/taxonomy; includes descendants. Unknown IDs return 400.'),
          parameter('classification', {'type': 'string', 'enum': ['classified', 'needs_review', 'insufficient_metadata', 'pending']}, 'AI metadata/summary classification status, independent of evidence quality.'),
          parameter('topic', STRING, 'Compatibility exact canonical human-readable leaf path from /api/facets; prefer category.'), parameter('source', STRING, 'Exact discovery-source label from /api/facets.'),
          parameter('extracted', {'type': 'boolean'}, 'Require an extraction (true), or metadata only (false).'),
          parameter('rated', {'type': 'boolean'}, 'Require a non-null quality score (true), or unrated (false).'),
          parameter('year_from', {'type': 'integer', 'minimum': 1000, 'maximum': 9999}, 'Inclusive lower publication year; excludes undated records.'),
          parameter('year_to', {'type': 'integer', 'minimum': 1000, 'maximum': 9999}, 'Inclusive upper publication year.'),
          parameter('min_score', {'type': 'number', 'minimum': 0, 'maximum': 100}, 'Minimum provisional score; excludes unrated records.'),
          parameter('sort', {'type': 'string', 'enum': ['relevance', 'newest', 'score']}, 'Default relevance with q, otherwise newest. Score sorts descending with unrated last.')]


def operation(name, summary, schema, params):
    return {'operationId': name, 'summary': summary, 'parameters': params + [{'name': 'If-None-Match', 'in': 'header', 'schema': STRING, 'description': 'ETag from a previous response; unchanged representations return 304.'}],
            'responses': {'200': {'description': 'Public snapshot data. Relative links resolve against the service URL.', 'headers': {'X-JEPA-Snapshot': {'description': 'Catalogue content hash.', 'schema': STRING}, 'ETag': {'description': 'Representation validator; varies with content encoding.', 'schema': STRING}}, 'content': {'application/json': {'schema': ref(schema)}}},
                          '304': {'description': 'Representation unchanged; no response body.'},
                          **{str(status): {'description': text, 'content': {'application/json': {'schema': ref('Error')}}, **({'headers': {'Retry-After': {'schema': STRING, 'description': 'Seconds to wait before retrying.'}}} if status in (429, 503) else {})} for status, text in ((400, 'Invalid query'), (404, 'Unknown route or paper'), (409, 'Published snapshot changed'), (405, 'Read-only API'), (414, 'Request target too long'), (429, 'Client request or download budget exceeded'), (503, 'Public snapshot unavailable or server busy'))}}}


OPENAPI = {
    'openapi': '3.1.0',
    'info': {'title': 'JEPA research landscape agent API', 'version': '1.0.0',
             'description': 'Read-only access to the published catalogue, not the private working database. Provisional AI assessments; missing scores are unknown. Classification is AI metadata/summary-based and separate from evidence quality. Raw scientific qualifiers are not globally harmonized. Normalized exports: /data/taxonomy.json, /data/paper-categories.jsonl, /data/findings.jsonl, /data/results.jsonl. See /llms.txt for the agent workflow and evidence limitations.'},
    'servers': [{'url': 'https://jepa.toni.foo'}], 'security': [],
    'paths': {
        '/api': {'get': operation('describeCatalogue', 'Coverage, limitations and endpoint discovery', 'Overview', [PIN])},
        '/api/search': {'get': operation('searchPapers', 'Search and filter compact paper records', 'Search', SEARCH+PAGING+[PIN])},
        '/api/taxonomy': {'get': operation('readTaxonomy', 'Published taxonomy and unique-paper counts rolled up to every ancestor; taxonomy is null when not configured', 'Taxonomy', [PIN])},
        '/api/facets': {'get': operation('listFacets', 'Global published-corpus counts and exact filter labels', 'Facets', [PIN])},
        '/api/papers/{work_id}': {'get': operation('readPaper', 'Complete public extraction, appraisal and evidence references', 'PaperResponse', [WORK, PIN])},
        '/api/papers/{work_id}/citations': {'get': operation('listCitations', 'Observed citation edges and neighboring records', 'Citations', [WORK, parameter('direction', {'type': 'string', 'enum': ['both', 'incoming', 'outgoing'], 'default': 'both'}, 'Relative to the requested paper; total counts edge observations.')]+PAGING+[PIN])},
    },
    'components': {'schemas': SCHEMAS},
}

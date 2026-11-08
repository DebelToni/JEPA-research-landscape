# Implementation contract

2026-11-07. Parallel implementation ownership and shared interfaces. PLAN.md is the research design; this file pins the initial executable interfaces. No fake data in production.

## Runtime

Python 3.12+, uv project, package `jepa` under `src/jepa`. Dependencies: httpx, pydantic v2, beautifulsoup4, defusedxml. pytest for tests. No ambient secrets in model prompts. Private data root defaults to `runtime/` and contains database, artifacts, documents, extraction records and run receipts. Public export defaults to `site/public/data/` (gitignored generated files).

## Shared JSON shapes

Source record (plain dict): `source`, `source_id`, `arxiv_id` (unversioned or null), `version` (e.g. v2 or null), `doi` (or null), `title`, `authors` (list of names), `published` (ISO string), `updated` (ISO string), `url` (public canonical), `abstract`, `license` (URL or null). Additional source fields may be stored privately. Non-arxiv identities are valid. Never infer a version from an unversioned URL.

Canonical work returned by Store: source metadata plus `work_id` (UUID), `relevance` (direct/adjacent/context/uncertain/excluded), `topics` (list), `status` (discovered/acquired/extracted/partial/failed), `extraction` (dict or null). The pipeline can enrich metadata without changing UUID. Merges require explicit ID evidence, not fuzzy title matching. Conflicts must be recorded, not auto-merged.

Extraction record shape for site and exports:
- `schema_version`: '1.0'
- `summary`: string; `keywords`: list[str]; `relevance`: enum above; `relevance_reason`: string; `topics`: list[str]
- `sections`: object mapping research_questions, contributions, mechanism, assumptions, theory, limitations, future_work, hypotheses to lists of `{text, attribution: author|analyst, evidence: list[evidence_id]}`
- `findings`: list of `{id, name, interpretation: Claim, key_results: list[Experiment], caveats: list[Claim], evidence: list[evidence_id]}`. One finding per experimental question/family, normally 1–3 decisive results, never exhaustive table transcription. The `Claim` shape is the same cited author/analyst statement used in sections. `Experiment` retains id/name/dataset/split/method/metric/value/uncertainty/protocol/evidence, with explicit label_budget where applicable; null for missing fields. Legacy `experiments` may remain in private historical records but MUST NOT enter new public exports or the UI. This supersedes the initial cell-by-cell design per Anton's 2026-11-08 correction.
- `artifacts`: list of `{kind, url, status: linked|reachable|downloaded|inspected, note}`. No executed/reproduced claims.
- `evidence`: list of `{id, source_url, locator, quote, artifact_sha256, block_id, page}`. Public exports MUST omit `quote` by default. Locators must be publicly usable section/table/page labels, not only private hashes.
- `inventory`: list of `{id, label, status: complete|partial|not_quantitative|inaccessible|unresolved, note}`.
- `quality`: `{score: number|null, coverage: number, rubric: empirical-v1|theory-unrated, dimensions: list[{name, score: 0..4|null, reason, evidence: list[evidence_id]}], flags: list[str]}`. Score calculated by code, not trusted from model. Incomplete appraisal stays unrated.
- `validation`: `{schema_valid, location_resolved, source_support_checked, human_reviewed}` booleans, checks must not be conflated.
- `processing`: model/provider/prompt hashes, usage and timestamps, kept private unless explicitly allowlisted.
- `completeness`: partial|complete. Default partial unless all declared inventory processed and validated.

Public dataset: `/data/catalog.json` = `{schema_version:'1.0', generated_at, papers:[public work], edges:[{source:work_id,target:work_id,provider,relation:'cites'}], coverage:{...}}`. Public work contains work_id, title, authors, published, updated, arxiv_id, version, doi, url, relevance, topics, status, extraction (public-safe shape above), sources (provider names only). Public paper record at `/data/papers/<work_id>.json`; Markdown optional. `/data/manifest.json` contains release hashes/counts and coverage. `/data/schema.json` describes extraction schema. UI must tolerate discovered records with extraction null and partial extraction. No sample records silently substituted for empty data.

## Ownership and APIs

Core agent owns `src/jepa/store.py`, `src/jepa/sources.py`, `tests/test_store.py`, `tests/test_sources.py`, `config/seeds.json`, `config/queries.json` only. Store(root: Path) methods: ingest(record)->work_id; get(work_id)->dict; papers()->list[dict]; set_extraction(work_id, extraction)->None; set_status(work_id,status)->None; enqueue(work_id,stage,fingerprint='v1')->None; claim(stage,lease_seconds=900)->dict|None (includes id,work_id); finish(job_id,error=None)->None; stats()->dict. Expose connection as `db` if integration needs explicit SQL. Sources module emits record dicts. Define/document callable discovery interfaces in module docstrings. Persist API pagination and quota ledgers using store.db. No CLI ownership.

Extraction agent owns `src/jepa/extract.py`, `src/jepa/documents.py`, `src/jepa/models.py`, `scripts/model-call.mjs`, `tests/test_extraction.py`, `tests/test_documents.py`, `tests/model-call.test.mjs`, `prompts/` only. API `acquire_and_parse(paper:dict, root:Path, timeout:float=120)->dict` document (blocks, artifacts and warnings); `extract_paper(paper:dict, document:dict, root:Path, timeout:float=300)->dict` extraction using configurable selected-account model adapter. Make chunking explicit; no silent truncation. Keep intermediate results for resumption. Export schema via Pydantic. Safe parsing, actual version identity, precise evidence, constrained model calls. Read installed Pi docs before adapter implementation. Parameterize Pi runtime/account/model path, no committed private paths. No downloading huge GPU models; source/HTML first, bounded PDF fallback with explicit parser quality warning if Docling unavailable.

Site agent owns `site/` except generated `site/public/data/`, `tests/browser/`, and frontend-only package files. React/TypeScript static build, paper routes /papers/<uuid>/ (prerender if practical), responsive search/facets, paper evidence and experiment tables, local citation graph using Cytoscape, dataset links, source links and methodology. Minimal light design, no hero or ornamental dashboards. No backend, accounts or unrequested social features. Build consumes real catalog.json; synthetic fixtures only in tests. Site code must never execute source HTML. Relative same-origin asset URLs. Canonical public domain jepa.toni.foo, intended repo https://github.com/DebelToni/JEPA-research-landscape. Package manager npm.

Main agent owns pyproject.toml, CLI/orchestration, exports, integration tests, docs, deployment and publication. Other agents must not change shared interfaces without reporting exact changes. Do not commit or publish independently.

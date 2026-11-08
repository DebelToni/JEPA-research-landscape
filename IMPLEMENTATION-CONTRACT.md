# Implementation contract

Shared interfaces, updated 2026-11-08. PLAN.md is the research design; `src/jepa/models.py` and the exported JSON Schema are authoritative for record fields. No synthetic data in production.

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

## Module boundaries

- `store.py`: work/version/source ledger, append-only extractions and fenced jobs. `Store(root)` owns the database; `claim` can be scoped to job IDs, and concurrent completion must supply its lease token. Source adapters persist cursors and quotas through `store.db`.
- `documents.py`: `acquire_and_parse(paper, root, timeout=120)` returns blocks, immutable artifacts and explicit parser/coverage warnings. Cache paths include parser versions. Source is never executed.
- `extract.py`: `extract_paper(paper, document, root, timeout=300)` performs checkpointed chunk extraction and within-paper reconciliation. `interpret_existing` converts historical detailed records to findings without claiming new source verification. The model emits block references and quotes; the host supplies immutable location metadata.
- `identity_review.py`: bounded bibliographic proposals only; no implicit identity, source or score mutations.
- `export.py` and `scripts/publish.py`: field-allowlisted public data, private backups, integrity-checked static promotion. Public builds reject legacy rows, unexpected roots and unmanifested data.
- `site/`: React/TypeScript prerendered catalogue and `/papers/<uuid>/` routes, findings and source links, local observed-citation graph, methodology and downloads. No public model/processing endpoint. Source markup is never executed; math rendering is untrusted. Synthetic fixtures remain isolated from production.
- `cli.py`: manual discovery, screening, identity review, time-bounded parallel processing, status and export. No recurring timer. `OPERATIONS.md` owns runtime configuration and recovery.

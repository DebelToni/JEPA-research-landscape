# JEPA research landscape

**[jepa.toni.foo](https://jepa.toni.foo)** · [Agent API](https://jepa.toni.foo/llms.txt) · [Methodology](https://jepa.toni.foo/methodology/) · [Dataset](https://jepa.toni.foo/data/catalog.json)

A continuously growing catalogue of JEPA and related predictive/world-model research. Papers have stable identities, immutable source versions, experimental interpretations with selected key numbers and source-linked per-paper assessments. The code is public; full-text caches, credentials and processing logs are private.

## Implemented

- arXiv search and version metadata, Hugging Face Papers, curated arXiv link imports, optional alphaXiv MCP and quota-limited OpenAlex citation discovery.
- Streaming local arXiv metadata JSONL import with filtering and persisted offsets, plus read-only import from retained scholarly SQLite graphs. Large snapshots are never downloaded automatically.
- SQLite identity/source/version ledger, identifier-conflict quarantine, proposal-only AI identity reviews, FTS5, resumable jobs and citation edges. Bibliography links provide citation discovery without spending OpenAlex requests.
- Sandboxed LaTeX/HTML/PDF acquisition and structured document blocks. HTML tables preserve row/cell spans; raw LaTeX and low-fidelity PDF extraction remain explicitly flagged.
- Selected-account Codex calls through Pi, chunk checkpoints, bounded validation repairs and within-paper reconciliation. Contributions, questions, mechanisms, assumptions, theory, limitations, future work, hypotheses, experimental findings and artifacts are structured JSON. Findings explain the results and retain a few decisive numbers, rather than reproducing full source tables.
- Source-location and literal-value checks, provisional rubric scores, separate relevance and extraction status. AI source checks are not independent reproduction or human review.
- Time-boxed parallel processing with usage receipts. No unattended schedule, account rotation or paid-API fallback.
- Rights-aware static exports, searchable React catalogue, prerendered paper pages, local citation graphs and downloadable JSON/JSONL/Markdown.
- Public read-only agent API over the published snapshot: lexical BM25 search, filters, scores, full paper records, citation neighbors, facets and snapshot-pinned pagination. It never opens the private working database or invokes models.

## Current limits

This is a working research pipeline, not a complete census or a verified experimental database. Records can be metadata-only or partially extracted; review and completeness fields are explicit. Queries and graph frontiers are resumable. Citation counts are not quality scores, and negative results are not discarded for failing to beat a baseline.

PDF-only parsing currently uses bounded `pdftotext`, not Docling or visual table verification. Code/dataset links are assertions unless their recorded availability says otherwise; paper code is never executed. General venue harvesting, Semantic Scholar/Crossref adapters and reversible curator merge/split operations remain unimplemented. AI identity reviews are bibliographic proposals, not automatic merges; conflicting identities remain private pending resolution. Cross-paper scientific synthesis is outside scope.

## Agent access

Start at **https://jepa.toni.foo/llms.txt**. [OpenAPI 3.1](https://jepa.toni.foo/openapi.json) describes the machine interface; `/api` reports coverage and current snapshot identity. No authentication is needed.

Example: `/api/search?q=latent%20prediction&extracted=true&min_score=50&limit=10`. Follow each result's API link for full extraction, score reasons and evidence references. `/api/facets` lists exact filter labels. Search is lexical and uses all query words, not semantic embeddings. Missing scores remain unknown, and citations are observed links rather than inferred scientific relationships.

The origin indexes only the immutable public catalogue in memory. A publication switch refreshes the index without a restart. Pagination links pin the snapshot; HTTP 409 means publication changed and the client should restart its search. Current working results are not visible until exported and published. The detailed contract is maintained in `src/jepa/agent-api.txt` and `src/jepa/api_docs.py`.

## Run locally

Requires Linux with Landlock and libseccomp for document parsing, Python 3.12, uv, Node 22+, and `pdftotext` for the fallback. Other platforms can run discovery/export/site components, but parser isolation has not been ported or verified there.

```sh
uv sync --frozen
uv run jepa discover --source core --hydrate
uv run jepa discover --source arxiv --query 'all:JEPA' --limit 100
uv run jepa status
```

Configure the selected Pi model provider as described in [operations](OPERATIONS.md). Model processing is a manually started run, not a background service:

```sh
uv run jepa --env-file runtime/config.env screen --seconds 600 --limit 400
uv run jepa --env-file runtime/config.env run --seconds 7200 --workers 6
uv run jepa export
cd site && npm ci && npm run build
```

Do not use Codex OAuth tokens as ordinary OpenAI API keys. The adapter requires a compatible installed Pi runtime and explicit account/model selection; it does not bundle a personal runtime or credentials.

## Test

```sh
uv run pytest -q
node --test tests/model-call.test.mjs
cd site
npm ci
npm test
npm run test:build
npx playwright install chromium
npm run test:browser
```

Browser/build tests use isolated synthetic data, never substitute fixtures for production records, and cover desktop/mobile paper navigation, filters, evidence, graphs, downloads and unsafe source content.

## Design and sources

- [Design plan](PLAN.md), including confirmed scope and future implementation work
- [Discovery sources](research/discovery-sources.md)
- [Content extraction tools](research/content-extraction.md)
- [Scientific scope and seed proposal](research/scope-and-quality.md)
- [Reusable components](research/reusable-components.md)
- [Code license](LICENSE) and [catalogue rights](DATA-LICENSE.md)

The research reports are dated source surveys, not measured parser comparisons. Operational status here supersedes their preliminary recommendations about pilots or corpus stages.

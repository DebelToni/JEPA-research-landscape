# Operations

## Private runtime

`runtime/` is excluded from Git. It owns the SQLite catalogue, downloaded source artifacts, normalized documents, model responses, extraction checkpoints, model configuration, run receipts, deployment recovery and immutable site releases. Preserve it independently of the public source repository. The public dataset is reproducible from this state but is not a replacement for the evidence cache or source ledger.

The deployed origin uses `deploy/jepa-landscape-site.service`, loopback port 40173 and the existing public tunnel connector. The origin serves static published files and a read-only agent API over that same snapshot. There is no public processing endpoint. The selected connector/DNS configuration and exact rollback objects are retained privately under `runtime/deploy/`; do not commit them.

## Model adapter

`scripts/model-call.mjs` takes one bounded JSON request on stdin and returns response text, stop status and actual provider-returned usage. It loads no personal agent session, tools, resource loader or context files. It resolves the selected Codex OAuth account through `ModelRuntime`; missing credentials or models fail rather than switching providers.

Set these variables in an ignored env file:

| Variable | Meaning |
| --- | --- |
| `PI_MODULES_DIR` | Directory containing installed `pi-ai/` and `pi-coding-agent/` packages |
| `JEPA_PROFILE_PATH` | JSON `{model, thinking, serviceTier}` resolved from the operator's model policy |
| `JEPA_PROVIDER` | Exact chosen Codex account/provider ID |
| `JEPA_AUTH_PATH` | Existing Pi credential store; do not copy tokens into the project |
| `JEPA_MODELS_PATH` | Optional isolated model config, e.g. an empty `{"providers":{}}` file |
| `JEPA_MODELS_STORE_PATH` | Optional private copy of Pi's cached catalog |
| `JEPA_CHUNK_CHARS` | Document chunk size; deployment uses 45000 |
| `JEPA_MAX_TOKENS` | Per-call output cap; deployment uses 16000 |

The installed runtime treats `modelsPath:null` as an in-memory model catalog even when a models-store path is supplied. To restore models newer than its built-in catalog, provide both isolated model configuration and catalog paths, and wrap the runtime's restored Codex provider rather than a bare built-in provider. Never invent a model ID or silently substitute a model when resolution fails.

Provider usage may include catalog-estimated dollar costs. These are **not subscription charges**. Run receipts retain token/call usage and mark charges unknown. The operator's actual subscription observations govern continuation. No Codex usage cap was requested for the first night's work; duration limits bound process lifecycle, not token spending.

## Discovery and quotas

`config/queries.json` and `config/seeds.json` are the editable discovery inputs. Each arXiv search invocation retrieves one page and saves its cursor. Repeat it to continue. Exhausted searches require a new overlapping date-window query for refresh. The CLI does not claim to have exhausted every configured query automatically.

`discover --source local --file metadata.jsonl --query JEPA --limit N` reads an existing arXiv metadata snapshot sequentially and filters title/abstract/categories before ingestion. Its persistent cursor is scoped to the original file and query; each call imports at most N matching records and scans at most 100,000 lines. Repeat to continue. No giant snapshot is fetched automatically.

OpenAlex reads `OPENALEX_API_KEY` from an explicitly supplied env file/environment and has a conservative local allocation of at most 20 requests per UTC day. Shared account consumption remains external. Free daily balance is distinct from prepaid/one-time balance: zero prepaid credit must not pause a positive free allocation. HTTP 429s persist a pause. Forward-citation queries require a resolved OpenAlex ID; `HTTPSources.openalex_resolve` accepts only an explicit arXiv-location match, not title similarity alone.

`discover --source cached-graph --file GRAPH.sqlite3 --limit 1000` reuses bounded topic matches from retained scholarly `index_snapshots`. It opens an immutable, query-only source connection, refuses uncheckpointed WAL, makes no network calls and never imports or starts the source application's workers. Cached arXiv hints alone do not establish identity.

`review-identities --seconds 180 --limit 20` generates cached, append-only AI proposals for conflicting IDs or matching titles/authors. It does not merge works, modify scientific appraisals or turn bibliographic similarity into confirmed identity. Conflicting identities are held out of public exports pending resolution.

AlphaXiv is optional and explicitly skipped without `ALPHAXIV_API_KEY`. Do not substitute its default AI paper report for original text. The current adapter uses official MCP discovery only.

Bibliography extraction is available as `jepa.citations.ingest_bibliographies(Store(...))`. It reads acquired HTML bibliographies and creates only explicit arXiv-link edges, preserving observed provider provenance. New linked papers need metadata hydration and screening before extraction.

## Processing and recovery

`run --seconds N --workers M` owns a finite process set; N must be at most eight hours. Workers have no ambient shell tools and parsers are filesystem/network restricted. Run again to resume valid chunk checkpoints. `--ids` scopes a run to work UUIDs/arXiv IDs; `--retry-failed` explicitly requeues failures. Partial attempts are not silently discarded, and failed retries do not replace a good previous extraction.

Python workers pin imported code until they exit. Do not assume source edits update a running worker. New prompt/parser/model revisions need explicit reprocessing with matching cache provenance. Preserve old source/record revisions; do not edit raw evidence in place. New document caches include parser versions. Legacy detailed extractions are interpreted against their original normalization with `interpret_existing`, retaining the old record privately and publishing only findings and selected results. SIGTERM checkpoints/stops an owned processing run; it does not erase valid chunks.

Review `runtime/runs/`, source cursor/quota tables and per-extraction failure receipts. `uv run python scripts/usage-report.py` produces the deduplicated private pipeline-usage receipt; it excludes development agents and calls without returned usage. A deadline interruption can leave remote completion/usage unknown; an aborted local call does not prove the provider stopped immediately. Provider/configuration errors stop the run rather than repeatedly attempting the remaining queue. Output-length, validation and per-call timeout failures remain local. Parser and PDF children are bound to parent lifetime with Linux parent-death signals. Cached chunks retain separate generation and current-validation hashes; older missing generation metadata is explicitly unknown. No scheduling timer is installed.

## Taxonomy and curated exports

`src/jepa/taxonomy.json` is the vocabulary authority. Keep category IDs stable, never let a model create new categories, and bump the taxonomy version for a definition/hierarchy change. Assigned IDs are approved leaves (up to four per paper); parents are unique-paper rollups. Free-form extraction keywords remain search text, not taxonomy dimensions.

`category_revisions` stores append-only decisions pinned to the work manifestation and taxonomy version, with rationale, input provenance and AI/human-review state. Changing a paper version does not silently inherit its old classification. `classified`, `needs_review`, `insufficient_metadata` and `pending` are distinct from relevance, extraction completeness and evidence quality. Identifier-only discoveries remain insufficient-metadata records until hydrated. Legacy `works.topics` and extraction-revision tags remain private raw history; public `topics` is a compatibility view of canonical category paths.

`classify --seconds N --workers 4` backfills unclassified metadata/summary records independently of document extraction. An isolated bad batch is retried once; repeated batch failures leave work pending. A later call resumes remaining work. Future screening returns controlled categories directly; CLI `run` executes a bounded classification preflight in a separate process before creating parser workers. This process boundary avoids inheriting HTTP/model-thread allocator arenas into parser sandboxes. Existing running workers retain their imported code, but cannot overwrite the separate curated classification table.

Exports use one consistent SQLite read transaction across metadata, current extraction revisions, classifications and edges, then release it before writing files. The current-paper JSONL, taxonomy, normalized memberships/findings/results and data dictionary are hashed together in the manifest. Composite keys preserve paper/version and finding identity. No automatic cross-paper equivalence of metrics, datasets or protocols is asserted. Keep prior extraction/classification revisions and public releases; a taxonomy migration is not permission to rewrite scientific evidence.

## Public agent API

`/llms.txt` is the agent entry point; `/openapi.json` is the OpenAPI 3.1 contract. `/api/search`, `/api/taxonomy`, `/api/facets`, `/api/papers/{work_id}` and its `/citations` subresource read only the published catalogue. They do not import the working store, acquire sources or run models. The catalogue's manifest hash is checked before constructing an in-memory SQLite FTS5 index. Static serving and extraction remain separate processes.

Each response pins one resolved publication directory. The cache reloads when the publication symlink changes; clients can pin the returned catalogue hash and receive 409 rather than silently mixing versions. Search output is compact and bounded, query operators are treated as plain words, and invalid/unknown/repeated parameters return JSON errors. Missing scores never become zero. The origin bounds concurrent connections to 32 and idle socket waits to 30 seconds; overload returns 503 with Retry-After. The timeout is not an absolute request deadline: keep the origin loopback-only behind the existing Cloudflare proxy. API/data reads permit credential-free CORS.

Agent routes and instructions are served by Python, not copied into the static build. Code changes require restarting **only `jepa-landscape-site.service`**; data promotion still needs no restart. Verify the API with both a standard Python HTTP client and a browser/client fetch, along with the existing site checks.

Cloudflare has a narrowly scoped custom rule skipping only Browser Integrity Check for GET/HEAD/OPTIONS on this hostname's `/api`, `/api/`, `/data/`, `/llms.txt` and `/openapi.json` routes. BIC otherwise rejects Python's default User-Agent. Other security products, paths and hostnames retain their existing behavior. Private configuration/recovery evidence is in `runtime/deploy/agent-api-edge-audit/`; rollback removes only that rule, not the shared ruleset.

API tests run as an isolated subprocess from `tests/test_api.py`; the detailed HTTP cases live in `tests/api_cases.py`. This keeps HTTP-thread allocator arenas out of subsequent parser tests' inherited address-space limit without weakening the parser sandbox.

## Publication

```sh
uv run jepa export
(cd site && npm run build)
uv run python scripts/publish.py
```

The exporter strips legacy row transcriptions, verbatim evidence quotations, private processing state and raw abstracts. Export backups live in private `runtime/export-backups/`, never under Vite's `public/` tree. Git ignore rules do not prevent Vite from copying a directory into the build. It validates URLs, scans for private paths/credential-like content and hashes every public data file. The publisher checks hashes and rejects unexpected build roots, unmanifested data, legacy experiment rows, hidden files and symlinks, copies a complete build into `runtime/releases/`, then atomically changes `runtime/published`. The static origin needs no restart for a new release. Retain the previous release for rollback; point the publication symlink back atomically to reverse a promotion.

Before promotion, run unit, build and browser tests, inspect representative records and review the export. After promotion, verify `/healthz`, the catalogue, a paper's HTML/JSON/evidence, graph and search on desktop/mobile. The uptime probe checks only read-only origin/public responses and startup, not scientific correctness or processing progress.

Public source commits do not publish new data automatically. CI uses synthetic fixtures and does not receive credentials or invoke models. GitHub issues are the initial correction channel; there is no public upload or execution surface.

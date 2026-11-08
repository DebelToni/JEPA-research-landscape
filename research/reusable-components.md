# Reusable components beyond paper sources

Research date: 2026-11-07. Documentation/source inspection only; no application integration or live model calls were performed.

## VibeMathed

The [live site](https://vibemathed.com/) exposes a filtered catalogue, per-entry evidence/status labels, methodology, dataset downloads and a local contribution/review process. Its [public repository](https://github.com/mrconter1/vibemathed) identifies the implementation as Next.js/TypeScript, Prisma with a Postgres-compatible database, Auth.js and Vitest; code is MIT. The live site and README have different dated entry counts, so do not treat README counts as current data.

Its [dataset licensing](https://vibemathed.com/data-license) distinguishes original CC BY 4.0 annotations from third-party quoted material. That boundary is directly useful here. The site also separates significance, verification and publication status rather than relying on one unlabeled badge.

Reuse the catalogue/evidence interaction pattern and, where useful, individual MIT components with notices. A wholesale fork would bring accounts, votes, comments, community moderation, mathematical proof verification and a database-backed public app that this project does not require. Its frontier synthesis feature is beyond the approved scope.

## Existing JEPA resource lists

[awesome-jepa](https://github.com/AbdelStark/awesome-jepa) is a useful source of paper/model/code/dataset links, not an authoritative bibliographic database or experimental assessment. GitHub's repository metadata returned no detected license on inspection; that is not a legal determination, and it is not permission to copy its prose wholesale. Follow links to primary papers, preserve discovery provenance and inspect current licensing before copying content. Our contribution is structured evidence and continuous curation, not another hand-maintained link list.

## Pi model integration

The installed Pi documentation supports three relevant boundaries:

- `ModelRuntime` for direct model access with centrally stored authentication.
- `createAgentSession` for an explicit in-process tool loop, with configurable tools, resource loader, model and session storage.
- Print/JSON/RPC CLI modes for subprocess isolation. JSON event mode is not itself a constraint on the model's output schema; inspect final stop reasons/events rather than accepting any stdout as success.

SDK credentials example `examples/sdk/09-api-keys-and-oauth.ts` demonstrates passing a `ModelRuntime` into a session. SDK and provider docs distinguish OAuth credentials from ordinary API keys. A local existing application adapter was inspected: it registers the specifically selected Codex account provider with a bare runtime and uses `completeSimple` without tools. This establishes a reusable implementation pattern, not a guarantee that arbitrary concurrency, strict JSON mode or paid API features are available through subscription auth.

Recommended contract: JSON job in, schema-checked JSON result plus usage/stop metadata out. Keep Python orchestration separate from a small TypeScript Pi adapter. Use no-tool calls for bulk work and restricted document tools for exceptions. Resolve model profiles centrally, freeze them for a job, and ensure public code does not require personal machine paths. The adapter should fail clearly when the selected provider or credentials are unavailable; no automatic account rotation or paid billing fallback.

Only the provider runtime should handle credentials. Do not copy OAuth tokens into a normal OpenAI client, expose them to extraction agents, or implement a second token-refresh mechanism. Validate the packaged SDK/export contract and current provider terms before production use. A repository that depends on a locally customized Pi runtime must document that dependency rather than claiming portability it has not tested.

## Storage, search and presentation

| Component | Reuse | Boundary |
| --- | --- | --- |
| [SQLite FTS5](https://www.sqlite.org/fts5.html) | Local full-text lookup plus ordinary SQL for records, jobs and citation edges | Enable/test FTS5 in the actual runtime; no network database needed initially |
| [Pydantic](https://docs.pydantic.dev/latest/) | Typed records and JSON Schema export | Structural validity cannot establish scientific truth |
| [Pagefind](https://pagefind.app/) | Static-site full-text search over generated public pages | Index only public permitted text; benchmark payload size as the corpus grows |
| [Cytoscape.js](https://js.cytoscape.org/) | Interactive local citation/topic graph; MIT core | Library, not a complete knowledge-graph application; no graph database required |
| [KaTeX](https://katex.org/) / [MathJax](https://www.mathjax.org/) | Mathematical rendering | Treat source as untrusted; use safe settings and do not enable arbitrary trusted HTML commands |
| React + TypeScript | Catalogue controls and paper/graph views | Prerender routes for readable/indexable pages; no account system required |

The exact frontend build tool and component versions can be selected at implementation. Avoid both a custom search engine and a large hosted vector service before ordinary retrieval is measured. Full text/embeddings are not inherently safe to publish merely because they are derived from a paper.

## What still has to be built

The reusable tools do not supply the project's central contract: stable work/version identity, reversible deduplication, comprehensive experiment inventories, exact result-row/evidence correspondence, attribution of analyst inferences, transparent appraisals and rights-aware public exports. Those are the useful custom parts. A generic paper-chat/RAG interface would not replace them.

# Static catalogue

React / TypeScript, Vite and Cytoscape. No server-side processing, credentials, accounts or external browser services. All paper content is rendered as escaped React text. Evidence and artifact URLs permit absolute credential-free HTTP(S) only.

## Production build

From `site/`:

```sh
npm ci
npm run build
```

The pipeline must first generate `site/public/data/catalog.json` using IMPLEMENTATION-CONTRACT.md schema 1.0. It must also provide `papers/<work_id>.json`, `manifest.json` and `schema.json` in that directory for download links. The site never creates or substitutes production records. Missing catalog data or invalid work UUIDs fail prerendering; an explicitly empty catalogue is valid.

Output: `site/dist/`, including `/index.html`, `/methodology/index.html`, `/papers/<uuid>/index.html`, bundled assets and copied `/data/` exports. Publish the whole directory atomically with directory-index routing and correct asset content types. No SPA rewrite is needed for known paper routes. Canonical links use `https://jepa.toni.foo`. New or changed records require rebuilding; keep exported JSON and HTML from the same build. Do not publish a synthetic test build.

## Verification

From `site/`:

```sh
npm run typecheck
npm test
npm run test:build
npx playwright install chromium
npm run test:browser
# Against an independently served real static build:
BASE_URL=https://your-test-host.example npm run test:browser
```

`test:build` asserts prerendered content, null extraction and escaped unsafe text. The default browser suite creates a separate temporary site under `$TMPDIR` or `~/tmp`, writes synthetic data there, builds it, serves it on localhost:4179 and removes it on shutdown. It does not touch `site/public/data/`. Four desktop/mobile tests exercise search, topic facets, direct paper reloads, experiment zero values, evidence anchors, null extraction, JSON downloads, methodology, graph visibility and unsafe link/HTML handling. External-base mode skips fixture-specific assertions and uses the target's actual catalogue; an empty catalogue cannot exercise paper flows.

## Contract boundaries

Facets use available public contract fields: relevance, topic, year, completeness, rated/unrated appraisal, artifact kind and processing status. Title, authors, keywords, summary and mechanism text are searchable. Prediction space, conditioning, modality and task have no separate fields in the pinned public shape, so they are not guessed from prose or exposed as fabricated facets. Add structured exporter fields before adding those filters.

All eight section groups, experiment inventory and rows, artifact availability, evidence locators, dimension-level appraisal and independent validation flags are displayed. Null extraction is explicit; absent fields in partial records remain unreported. Appraisal is displayed from the code-derived export, never recomputed by the browser. No quote field is rendered or indexed. Publication rights and export allowlisting remain the exporter's responsibility: raw exported JSON is itself public.

The graph contains only observed `cites` edges incident to the selected paper. Node taps navigate to stable paper routes; a textual edge list preserves provider attribution and accessibility. Missing local endpoint records appear only in the textual list. No influence, agreement, inferred topic edges or corpus synthesis is generated.

The browser currently fetches the complete public catalogue for local search and graph data. Large releases may need a dedicated search index and sharded metadata after measurement. The Cytoscape bundle currently triggers Vite's nonfatal 500 kB chunk warning.

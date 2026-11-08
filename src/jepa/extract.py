"""Chunked, checkpointed per-paper extraction using scripts/model-call.mjs.

Environment: JEPA_MODEL_ADAPTER (optional script override), JEPA_CHUNK_CHARS
(default 18000), JEPA_MAX_TOKENS (12000); adapter runtime configuration documented
in scripts/model-call.mjs. A resolved profile JSON is frozen at job start.
Failed chunks are retried only on a later explicit extract_paper invocation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from .models import Evidence, Extraction, Inventory, Reconciliation
from .documents import digest

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "extract.txt"
ADAPTER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "model-call.mjs"
WEIGHTS = {"claim_support": .30, "comparisons": .25, "generalization": .15,
           "reporting": .15, "calibration": .15}
IMPLEMENTATION_SHA = digest(Path(__file__).read_bytes())


def model_schema():
    """Models choose evidence blocks; the host supplies immutable locations."""
    schema = Extraction.model_json_schema()
    evidence = schema['$defs']['Evidence']
    for key in ('source_url', 'locator', 'artifact_sha256', 'page'):
        evidence['properties'].pop(key, None)
        if key in evidence.get('required', []): evidence['required'].remove(key)
    return schema


def materialize_record(text, document):
    value = json.loads(text)
    blocks = {block['id']: block for block in document['blocks']}
    for evidence in value.get('evidence', []):
        block = blocks.get(evidence.get('block_id'))
        if block is None: raise ValueError('Evidence block does not resolve')
        for key in ('source_url', 'locator', 'artifact_sha256', 'page'):
            evidence.setdefault(key, block.get(key))
    return validate_record(Extraction.model_validate(value), document)


def normalize(text: str) -> str:
    return " ".join(text.split())


def numeric_value(value: str | None) -> float | None:
    if value is None:
        return None
    # Only unambiguous scalars; do not misparse ranges, locale commas or ±.
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*%?\s*", value)
    if not match:
        return None
    number = float(match[1])
    return number if abs(number) < float("inf") else None


def validate_record(record: Extraction, document: dict) -> Extraction:
    """Deterministic location/quote and literal result checks, NOT semantic review."""
    blocks = {block["id"]: block for block in document["blocks"]}
    evidence = {}
    for entry in record.evidence:
        if entry.id in evidence:
            raise ValueError("Duplicate evidence ID")
        block = blocks.get(entry.block_id)
        if not block or any(getattr(entry, key) != block.get(key) for key in
                            ("artifact_sha256", "source_url", "locator", "page")):
            raise ValueError("Evidence location does not resolve")
        if normalize(entry.quote) not in normalize(block["text"]):
            raise ValueError("Evidence quote is absent from actual block")
        evidence[entry.id] = entry

    def check_refs(refs):
        if any(ref not in evidence for ref in refs):
            raise ValueError("Unknown evidence reference")

    for claims in record.sections.model_dump().values():
        for claim in claims:
            check_refs(claim["evidence"])
    ids = set()
    finding_ids = set()
    rows = list(record.experiments)
    for finding in record.findings:
        if finding.id in finding_ids:
            raise ValueError("Duplicate finding ID")
        finding_ids.add(finding.id)
        check_refs(finding.evidence)
        for claim in [finding.interpretation, *finding.caveats]:
            check_refs(claim.evidence)
        for row in finding.key_results:
            if row.split and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*%\s*", row.split):
                raise ValueError("Experiment split is a label budget; use label_budget")
        rows.extend(finding.key_results)
    errors = []
    for index, row in enumerate(rows):
        if row.id in ids:
            raise ValueError("Duplicate experiment ID")
        ids.add(row.id)
        check_refs(row.evidence)
        quotes = [normalize(evidence[ref].quote) for ref in row.evidence]
        for field in ("value", "dataset", "split", "label_budget", "method", "metric", "units", "uncertainty", "protocol"):
            value = getattr(row, field)
            if value and not any(normalize(value) in quote for quote in quotes):
                errors.append(f"Experiment row {index} {field} not literally grounded")
        row.numeric_value = numeric_value(row.value)
        if row.numeric_value is not None and not any(
                re.search(r"(?<![\w.+-])" + re.escape(normalize(row.value)) + r"(?![\w.])", quote)
                for quote in quotes):
            errors.append(f"Numeric value in row {index} matches only part of another token")
        if row.direction and not any(row.direction in quote.lower() for quote in quotes):
            errors.append(f"Metric direction in row {index} is not literally grounded")
    if errors:
        raise ValueError('; '.join(errors[:32]))
    seen = set()
    for dimension in record.quality.dimensions:
        if dimension.name not in WEIGHTS or dimension.name in seen:
            raise ValueError("Unknown or duplicate quality dimension")
        seen.add(dimension.name)
        check_refs(dimension.evidence)
        if dimension.score is not None and not dimension.evidence:
            raise ValueError("Scored dimension requires evidence")
    record.validation.schema_valid = True
    record.validation.location_resolved = True
    record.validation.source_support_checked = False
    record.validation.human_reviewed = False
    record.quality.score = None
    record.quality.coverage = 0
    record.completeness = "partial"
    return record


def calculate_quality(record: Extraction, appraisal_complete: bool) -> None:
    quality = record.quality
    quality.score = None
    quality.coverage = 0
    if quality.rubric == "theory-unrated":
        return
    assessed = [dimension for dimension in quality.dimensions if dimension.score is not None]
    quality.coverage = round(sum(WEIGHTS[d.name] for d in assessed), 6)
    if appraisal_complete and quality.coverage >= .8:
        quality.score = round(100 * sum(WEIGHTS[d.name] * d.score / 4 for d in assessed) / quality.coverage, 2)
    if "AI-generated provisional appraisal; not human reviewed" not in quality.flags:
        quality.flags.append("AI-generated provisional appraisal; not human reviewed")


def chunk_document(document: dict, max_chars: int = 18000) -> list[list[dict]]:
    if max_chars < 1000 or max_chars > 100000:
        raise ValueError("Chunk bound must be 1000..100000 characters")
    chunks, current, size = [], [], 0
    for block in document["blocks"]:
        # Oversized blocks are explicitly partitioned; evidence still resolves to
        # the immutable original block, never a fabricated page or location.
        text = block["text"]
        for offset in range(0, len(text), max_chars):
            part = {**block, "text": text[offset:offset + max_chars], "chunk_offset": offset}
            if current and size + len(part["text"]) > max_chars:
                chunks.append(current)
                current, size = [], 0
            current.append(part)
            size += len(part["text"])
    if current:
        chunks.append(current)
    return chunks


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        json.dump(value, file, ensure_ascii=False, sort_keys=True)
        name = file.name
    os.replace(name, path)


def safe_validation_error(error: Exception) -> str:
    # Pydantic errors can contain source text or arbitrary model output.
    if hasattr(error, "errors"):
        return "schema_invalid: " + "; ".join(
            ".".join(map(str, item["loc"])) + ":" + item["type"]
            for item in error.errors(include_input=False, include_url=False))
    message = str(error)
    allowed = ("Evidence", "Unknown", "Duplicate", "Experiment", "Numeric", "Metric", "Scored", "Empty")
    return message if message.startswith(allowed) else "invalid_model_record"


def validated_call(payload, document, directory, label, deadline, env, parse):
    original = payload
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError("deadline")
        call_timeout = min(600, remaining)
        payload = {**payload, "timeout_ms": max(1, int((call_timeout - .5) * 1000))}
        response = call_model(payload, call_timeout, env)
        atomic_json(directory / f"response-{label}-{time.time_ns()}.json",
                    {**response, "stage": label, "attempt": attempt,
                     "input_sha256": digest(json.dumps(payload, sort_keys=True).encode())})
        if response.get("stopReason") != "stop" or response.get("rejected"):
            raise RuntimeError("incomplete_response")
        try:
            return parse(response["text"])
        except (ValueError, KeyError) as error:
            diagnostic = safe_validation_error(error)
            atomic_json(directory / f"failure-{label}-{time.time_ns()}.json",
                        {"code": "validation_failed", "validation": diagnostic, "attempt": attempt})
            if attempt:
                raise ValueError(diagnostic) from None
            payload = {**original, "prompt": original["prompt"] + "\n" + json.dumps({
                "repair": "Correct the previous JSON using ONLY the original evidence. Return full corrected JSON. Use null for ungrounded optional result fields; never fabricate quotes.",
                "validation_error": diagnostic, "previous_response": response["text"]})}


def call_model(payload: dict, timeout: float, env: dict) -> dict:
    result = subprocess.run(["node", env.get("JEPA_MODEL_ADAPTER", str(ADAPTER_PATH))],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError("Selected-account model adapter failed")
    return json.loads(result.stdout)


def extract_paper(paper: dict, document: dict, root: Path, timeout: float = 300) -> dict:
    deadline = time.monotonic() + timeout
    if not document.get("blocks"):
        raise ValueError("Document has no blocks")
    if paper.get("arxiv_id"):
        identity = re.sub(r"v\d+$", "", paper["arxiv_id"])
        embedded = re.search(r"v\d+$", paper["arxiv_id"])
        requested = paper.get("version") or (embedded[0] if embedded else None)
        if (document.get("arxiv_id") != identity or not document.get("version_validated") or
                (requested and requested != document.get("version"))):
            raise ValueError("Unvalidated or mismatched document version")
    env = dict(os.environ)
    profile_path = env.get("JEPA_PROFILE_PATH")
    if not profile_path:
        raise ValueError("JEPA_PROFILE_PATH must point to a centrally resolved profile")
    profile_bytes = Path(profile_path).read_bytes()
    profile = json.loads(profile_bytes)
    if not profile.get("model") or not profile.get("thinking"):
        raise ValueError("Invalid resolved profile")
    selected_kinds = {block.get("kind") for block in document["blocks"]}
    primary_warnings = list(document.get("warnings", []))
    if selected_kinds.isdisjoint({"raw_tex", "pdf_text"}):
        primary_warnings = [w for w in primary_warnings if not w.startswith((
            "raw_tex_not_expanded", "pdftotext_low_fidelity", "source:", "pdf:"))]
    prompt = PROMPT_PATH.read_text()
    schema = model_schema()
    chunks = chunk_document(document, int(env.get("JEPA_CHUNK_CHARS", "18000")))
    doc_hash = digest(json.dumps(document, sort_keys=True).encode())
    adapter_hash = digest(Path(env.get("JEPA_MODEL_ADAPTER", str(ADAPTER_PATH))).read_bytes())
    config = {"profile": profile, "provider": env.get("JEPA_PROVIDER"),
              "transport": env.get("JEPA_TRANSPORT", "sse"), "adapter": adapter_hash,
              "prompt": digest(prompt.encode()), "schema": schema, "document": doc_hash,
              "paper": {key: paper.get(key) for key in ("title", "arxiv_id", "version", "doi")},
              "chunk_chars": env.get("JEPA_CHUNK_CHARS", "18000"),
              "max_tokens": int(env.get("JEPA_MAX_TOKENS", "12000"))}
    job_hash = digest(json.dumps(config, sort_keys=True).encode())
    directory = Path(root) / "extractions" / job_hash
    directory.mkdir(parents=True, exist_ok=True)
    # Freeze caller-resolved selection, never auth credentials.
    frozen_profile = directory / "profile.json"
    atomic_json(frozen_profile, profile)
    env["JEPA_PROFILE_PATH"] = str(frozen_profile.resolve())
    records, receipts, failed = [], [], []
    for index, blocks in enumerate(chunks):
        path = directory / f"chunk-{index:05d}.json"
        chunk_doc = {**document, "blocks": blocks}
        if path.exists():
            checkpoint = json.loads(path.read_text())
            record = validate_record(Extraction.model_validate(checkpoint["record"]), chunk_doc)
            receipts.append(checkpoint["metadata"])
            records.append((index, record))
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            failed.append({"chunk": index, "error": "deadline"})
            continue
        payload = {"system": prompt,
                   "prompt": json.dumps({"schema": schema, "paper": {key: paper.get(key) for key in
                                          ("title", "arxiv_id", "version", "doi")},
                                         "chunk_index": index, "chunk_count": len(chunks),
                                         "blocks": blocks, "parser_warnings": document.get("warnings", [])}),
                   "timeout_ms": max(1, int((remaining - .5) * 1000)),
                   "max_tokens": config["max_tokens"]}
        try:
            def parse_chunk(text):
                record = materialize_record(text, chunk_doc)
                if not record.evidence and not record.inventory:
                    raise ValueError("Empty extraction is not a processed document chunk")
                return record
            record = validated_call(payload, chunk_doc, directory, f"{index:05d}", deadline, env, parse_chunk)
            metadata = {"chunk": index}
            atomic_json(path, {"record": record.model_dump(), "metadata": metadata})
            records.append((index, record))
        except (ValueError, RuntimeError, KeyError, TimeoutError, subprocess.SubprocessError) as error:
            code = ("deadline" if isinstance(error, subprocess.TimeoutExpired) or isinstance(error, TimeoutError)
                    else "validation_failed" if isinstance(error, ValueError) else "model_call_failed")
            failed.append({"chunk": index, "error": code})
            atomic_json(directory / f"chunk-{index:05d}-failure.json", failed[-1])
    # All provider-returned usage, including failed validation and prior attempts,
    # remains available on resume. Absent usage is unknown, never assumed zero.
    receipts = [{key: value for key, value in json.loads(path.read_text()).items() if key != "text"}
                for path in sorted(directory.glob("response-*.json"))]
    result = Extraction()
    result.processing = {"job_sha256": job_hash, "document_sha256": doc_hash,
                         "prompt_sha256": config["prompt"], "adapter_sha256": adapter_hash,
                         "profile": profile, "receipts": receipts, "failed_chunks": failed,
                         "chunks_total": len(chunks), "chunks_validated": len(records),
                         "timestamp": datetime.now(timezone.utc).isoformat()}
    # Prefix chunk-local IDs. Merge only validated objects, never let one chunk
    # claim it has processed another or elevate artifact availability.
    for index, record in records:
        prefix = f"c{index}:"
        for evidence in record.evidence:
            evidence.id = prefix + evidence.id
        for field in type(record.sections).model_fields:
            for claim in getattr(record.sections, field):
                claim.evidence = [prefix + ref for ref in claim.evidence]
                getattr(result.sections, field).append(claim)
        for row in record.experiments:
            row.id = prefix + row.id
            row.evidence = [prefix + ref for ref in row.evidence]
            result.experiments.append(row)
        for finding in record.findings:
            finding.id = prefix + finding.id
            finding.evidence = [prefix + ref for ref in finding.evidence]
            for claim in [finding.interpretation, *finding.caveats]:
                claim.evidence = [prefix + ref for ref in claim.evidence]
            for row in finding.key_results:
                row.id = prefix + row.id
                row.evidence = [prefix + ref for ref in row.evidence]
            result.findings.append(finding)
        result.evidence.extend(record.evidence)
        result.inventory.extend(Inventory(id=prefix + item.id, label=item.label,
                                          status=item.status, note=item.note) for item in record.inventory)
        for artifact in record.artifacts:
            # Model links are assertions, not network observations.
            artifact.status = "linked"
            result.artifacts.append(artifact)
        result.keywords = sorted(set(result.keywords + record.keywords))
        result.topics = sorted(set(result.topics + record.topics))
    for index in range(len(chunks)):
        result.inventory.append(Inventory(id=f"chunk:{index}", label=f"Document chunk {index+1}/{len(chunks)}",
            status="partial" if index in {i for i, _ in records} else "unresolved",
            note="Mechanically checked extraction; semantic support and inventory coverage await review"))
    reconciliation = None
    reconcile_prompt = (PROMPT_PATH.parent / "reconcile.txt").read_text()
    reconcile_config = {"record": result.model_dump(exclude={"processing"}), "prompt": reconcile_prompt,
                        "schema": Reconciliation.model_json_schema(),
                        "implementation": IMPLEMENTATION_SHA, "config": config}
    reconcile_hash = digest(json.dumps(reconcile_config, sort_keys=True).encode())
    reconcile_path = directory / f"reconcile-{reconcile_hash}.json"
    def parse_reconciliation(text):
        candidate = Reconciliation.model_validate_json(text)
        refs = {e.id for e in result.evidence}
        for cited in [candidate.summary, candidate.relevance_reason, candidate.paper_scope]:
            if not cited.evidence or any(ref not in refs for ref in cited.evidence):
                raise ValueError("Unknown reconciliation evidence reference")
        for revision in candidate.revisions:
            if any(ref not in refs for ref in revision.evidence):
                raise ValueError("Unknown revision evidence reference")
        probe = Extraction(evidence=result.evidence, quality=candidate.quality, findings=candidate.findings)
        validate_record(probe, document)
        return candidate
    if records:
        try:
            if reconcile_path.exists():
                reconciliation = parse_reconciliation(reconcile_path.read_text())
            elif not failed:
                reconciliation = validated_call({"system": reconcile_prompt,
                    "prompt": json.dumps({"schema": Reconciliation.model_json_schema(),
                        "validated_record": result.model_dump(), "scope": document.get("scope"),
                        "selected_warnings": document.get("warnings", [])}),
                    "max_tokens": config["max_tokens"]}, document, directory, "reconcile", deadline, env,
                    parse_reconciliation)
                atomic_json(reconcile_path, reconciliation.model_dump())
        except (ValueError, RuntimeError, KeyError, TimeoutError, subprocess.SubprocessError):
            result.processing["reconciliation_failure"] = "reconciliation_failed"
    if reconciliation:
        result.findings = reconciliation.findings
        if reconciliation.inventory is not None:
            result.inventory = reconciliation.inventory
        result.processing["revisions"] = [r.model_dump() for r in reconciliation.revisions]
        result.quality = reconciliation.quality
        result.summary = reconciliation.summary.text
        result.relevance = reconciliation.relevance
        result.relevance_reason = reconciliation.relevance_reason.text
        result.keywords = reconciliation.keywords
        result.topics = reconciliation.topics
        result.processing["paper_scope"] = reconciliation.paper_scope.model_dump()
        result.processing["summary_evidence"] = reconciliation.summary.evidence
        result.processing["relevance_evidence"] = reconciliation.relevance_reason.evidence
        result.processing["reconciliation_sha256"] = reconcile_hash
    else:
        result.quality.flags.append("Paper-wide appraisal pending within-paper reconciliation")
        if paper.get('relevance') in ('direct', 'adjacent', 'context', 'uncertain', 'excluded'):
            result.relevance = paper['relevance']
            result.relevance_reason = 'Abstract-based screening; full-paper reconciliation pending.'
    result.quality.flags.extend(primary_warnings)
    result = validate_record(result, document)
    appraisal_complete = (reconciliation is not None and len(records) == len(chunks)
                          and not primary_warnings)
    calculate_quality(result, appraisal_complete=appraisal_complete)
    result.processing["implementation_sha256"] = IMPLEMENTATION_SHA
    result.processing["receipts"] = [{key: value for key, value in json.loads(path.read_text()).items() if key != "text"}
        for path in sorted(directory.glob("response-*.json"))]
    output = result.model_dump()
    atomic_json(directory / "extraction.json", output)
    return output


def interpret_existing(paper: dict, record: dict, document: dict, root: Path,
                       timeout: float = 300) -> dict:
    """Interpret a validated detailed record without rerunning document extraction.

    Writes an immutable-input-linked private candidate, never updates the store.
    Mechanical checks do not certify source semantics or figure coverage.
    """
    deadline = time.monotonic() + timeout
    original_hash = digest(json.dumps(record, sort_keys=True).encode())
    source = validate_record(Extraction.model_validate(record), document)
    if paper.get("arxiv_id"):
        identity = re.sub(r"v\d+$", "", paper["arxiv_id"])
        version = paper.get("version")
        if (document.get("arxiv_id") != identity or not document.get("version_validated")
                or (version and version != document.get("version"))):
            raise ValueError("Unvalidated or mismatched document version")
    # Supply original source context as well as historical selected quotes. This
    # permits cited corrections without trusting old inventory prose as evidence.
    known = {e.id for e in source.evidence}
    for block in document["blocks"]:
        evidence_id = "source:" + block["id"]
        if evidence_id not in known:
            source.evidence.append(Evidence(id=evidence_id, quote=block["text"],
                **{k: block.get(k) for k in
                   ("block_id", "artifact_sha256", "source_url", "locator", "page")
                   if k != "block_id"}, block_id=block["id"]))
    env = dict(os.environ)
    profile = json.loads(Path(env["JEPA_PROFILE_PATH"]).read_text())
    if not profile.get("model") or not profile.get("thinking"):
        raise ValueError("Invalid resolved profile")
    prompt = (PROMPT_PATH.parent / "reconcile.txt").read_text()
    config = {"input_record_sha256": original_hash,
              "document_sha256": digest(json.dumps(document, sort_keys=True).encode()),
              "prompt_sha256": digest(prompt.encode()), "profile": profile,
              "implementation_sha256": IMPLEMENTATION_SHA,
              "provider": env.get("JEPA_PROVIDER"),
              "paper": {key: paper.get(key) for key in ('work_id', 'title', 'arxiv_id', 'version', 'doi')}}
    job_hash = digest(json.dumps(config, sort_keys=True).encode())
    directory = Path(root) / "interpretations" / job_hash
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(directory / "profile.json", profile)
    env["JEPA_PROFILE_PATH"] = str((directory / "profile.json").resolve())

    def parse(text):
        candidate = Reconciliation.model_validate_json(text)
        probe = Extraction(evidence=source.evidence, findings=candidate.findings,
                           quality=candidate.quality)
        validate_record(probe, document)
        refs = {e.id for e in source.evidence}
        for claim in [candidate.summary, candidate.relevance_reason, candidate.paper_scope,
                      *candidate.revisions]:
            if any(ref not in refs for ref in claim.evidence):
                raise ValueError("Unknown reconciliation evidence reference")
        return candidate

    candidate = validated_call({"system": prompt,
        "prompt": json.dumps({"schema": Reconciliation.model_json_schema(),
            "paper": config['paper'], "validated_record": source.model_dump(),
            "scope": document.get("scope"), "source_audit": document.get("source_audit"),
            "selected_warnings": document.get("warnings", [])}),
        "max_tokens": int(env.get("JEPA_MAX_TOKENS", "12000"))},
        document, directory, "interpret", deadline, env, parse)
    output = source.model_copy(deep=True)
    output.experiments = []
    output.findings = candidate.findings
    output.summary = candidate.summary.text
    output.relevance = candidate.relevance
    output.relevance_reason = candidate.relevance_reason.text
    output.keywords, output.topics = candidate.keywords, candidate.topics
    output.quality = candidate.quality
    if candidate.inventory is not None:
        output.inventory = candidate.inventory
    output.processing = {"stage": "interpret_existing", **config,
        "parent_processing": source.processing,
        "summary_evidence": candidate.summary.evidence,
        "relevance_evidence": candidate.relevance_reason.evidence,
        "paper_scope": candidate.paper_scope.model_dump(),
        "revisions": [r.model_dump() for r in candidate.revisions],
        "semantic_limits": "Literal grounding only; source semantics, figure coverage and human review unverified",
        "candidate_path": str(directory / "extraction.json"),
        "receipts": [{k: v for k, v in json.loads(p.read_text()).items() if k != "text"}
                     for p in sorted(directory.glob("response-*.json"))]}
    validate_record(output, document)
    selected_kinds = {block.get('kind') for block in document['blocks']}
    warnings = list(document.get('warnings', []))
    if selected_kinds.isdisjoint({'raw_tex', 'pdf_text'}):
        warnings = [w for w in warnings if not w.startswith(('raw_tex_not_expanded', 'pdftotext_low_fidelity', 'source:', 'pdf:'))]
    parent = record.get('processing', {})
    covered = bool(parent.get('chunks_total')) and parent.get('chunks_validated') == parent.get('chunks_total')
    calculate_quality(output, appraisal_complete=covered and not warnings)
    result = output.model_dump()
    used = set()
    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'evidence' and isinstance(item, list) and all(isinstance(x, str) for x in item):
                    used.update(item)
                elif key not in ('processing', 'evidence'):
                    references(item)
        elif isinstance(value, list):
            for item in value: references(item)
    references(result)
    used.update(candidate.summary.evidence + candidate.relevance_reason.evidence + candidate.paper_scope.evidence)
    for revision in candidate.revisions: used.update(revision.evidence)
    result['evidence'] = [entry for entry in result['evidence'] if entry['id'] in used]
    atomic_json(directory / "extraction.json", result)
    return result

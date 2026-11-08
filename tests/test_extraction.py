import json
import pytest
from jepa import extract as x
from jepa.models import Extraction
from jepa.documents import _block, digest


def document(text="method M dataset D metric accuracy value 94.5% test protocol fixed"):
    return {"blocks": [_block(text, digest(text.encode()), "Results Table 1", "https://example.org/p")], "warnings": []}


def record(doc):
    block = doc["blocks"][0]
    evidence = {"id": "e1", "source_url": block["source_url"], "locator": block["locator"],
                "quote": block["text"], "artifact_sha256": block["artifact_sha256"],
                "block_id": block["id"], "page": None}
    return Extraction.model_validate({"evidence": [evidence], "sections": {"contributions": [
        {"text": "M reports 94.5% accuracy.", "attribution": "author", "evidence": ["e1"]}]},
        "experiments": [{"id": "r1", "name": "test", "dataset": "D", "method": "M",
            "metric": "accuracy", "value": "94.5%", "numeric_value": 999, "evidence": ["e1"]}]})


def test_host_materializes_evidence_locations():
    doc=document()
    result=record(doc).model_dump()
    entry=result['evidence'][0]
    result['evidence']=[{key:entry[key] for key in ('id','block_id','quote')}]
    checked=x.materialize_record(json.dumps(result),doc)
    assert checked.evidence[0].source_url==doc['blocks'][0]['source_url']
    assert checked.evidence[0].artifact_sha256==doc['blocks'][0]['artifact_sha256']
    assert set(x.model_schema()['$defs']['Evidence']['properties'])=={'id','block_id','quote'}


def test_reports_all_result_qualifier_errors():
    doc=document(); output=record(doc)
    output.experiments[0].dataset='wrong dataset'
    output.experiments[0].protocol='wrong protocol'
    with pytest.raises(ValueError) as error:x.validate_record(output,doc)
    assert 'dataset' in str(error.value) and 'protocol' in str(error.value)


def test_grounding_and_numeric():
    doc = document()
    result = x.validate_record(record(doc), doc)
    assert result.experiments[0].numeric_value == 94.5
    assert result.validation.schema_valid and result.validation.location_resolved
    assert not result.validation.source_support_checked
    assert not result.validation.human_reviewed


@pytest.mark.parametrize("field,value", [("quote", "invented"), ("block_id", "fake"),
    ("artifact_sha256", "0"*64), ("page", 2), ("locator", "Table 999"), ("source_url", "https://wrong.org")])
def test_false_locations(field, value):
    doc = document()
    output = record(doc)
    setattr(output.evidence[0], field, value)
    with pytest.raises(ValueError):
        x.validate_record(output, doc)


def test_missing_headers_and_unknown_evidence():
    doc = document()
    output = record(doc)
    output.experiments[0].dataset = "CIFAR"
    with pytest.raises(ValueError, match="dataset"):
        x.validate_record(output, doc)
    output = record(doc)
    output.sections.contributions[0].evidence = ["missing"]
    with pytest.raises(ValueError):
        x.validate_record(output, doc)


@pytest.mark.parametrize("value,expected", [("-2.3", -2.3), ("1e-3", .001), ("94%", 94),
    ("2 ± 1", None), ("1,234", None), ("NaN", None), ("1-3", None), (None, None)])
def test_numeric(value, expected):
    assert x.numeric_value(value) == expected


def test_schema():
    assert Extraction.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValueError):
        Extraction.model_validate({"surprise": "field"})


def test_chunk_no_truncation():
    doc = document("a" * 2501)
    chunks = x.chunk_document(doc, 1000)
    assert [len(c[0]["text"]) for c in chunks] == [1000, 1000, 501]
    assert "".join(b["text"] for c in chunks for b in c) == doc["blocks"][0]["text"]


def test_score_is_code_calculated():
    result = Extraction.model_validate({"quality": {"score": 999, "dimensions": [
        {"name": name, "score": 3, "reason": "reported", "evidence": ["e"]} for name in x.WEIGHTS]}})
    x.calculate_quality(result, True)
    assert result.quality.score == 75 and result.quality.coverage == 1
    x.calculate_quality(result, False)
    assert result.quality.score is None
    result.quality.dimensions = result.quality.dimensions[:1]
    x.calculate_quality(result, True)
    assert result.quality.score is None


def test_resume_and_partial_failure(monkeypatch, tmp_path):
    profile = tmp_path / "selected.json"
    profile.write_text(json.dumps({"model": "selected", "thinking": "low"}))
    monkeypatch.setenv("JEPA_PROFILE_PATH", str(profile))
    monkeypatch.setenv("JEPA_CHUNK_CHARS", "1000")
    doc = document()
    doc["blocks"].append(document("second " * 140)["blocks"][0])
    calls = []
    def call(payload, timeout, env):
        source = json.loads(payload["prompt"])
        calls.append(source["chunk_index"])
        if source["chunk_index"] == 1:
            return {"text": "{}", "stopReason": "length", "usage": {"output": 4}}
        one = {"blocks": source["blocks"]}
        return {"text": record(one).model_dump_json(), "stopReason": "stop", "usage": {"output": 50}}
    monkeypatch.setattr(x, "call_model", call)
    output = x.extract_paper({}, doc, tmp_path)
    assert output["processing"]["chunks_validated"] == 1
    assert output["completeness"] == "partial"
    assert output["processing"]["failed_chunks"]
    calls.clear()
    output = x.extract_paper({}, doc, tmp_path)
    assert calls == [1]
    assert output["evidence"][0]["id"] == "c0:e1"
    assert output["quality"]["score"] is None
    assert len(output["processing"]["receipts"]) == 3
    assert sum(r["usage"]["output"] for r in output["processing"]["receipts"]) == 58


def test_wrong_version(monkeypatch, tmp_path):
    doc = document()
    with pytest.raises(ValueError, match="version"):
        x.extract_paper({"arxiv_id": "2401.12345", "version": "v2"}, doc, tmp_path)



def test_numeric_substring_rejected():
    doc = document("method M dataset D metric accuracy value 194.5%")
    with pytest.raises(ValueError, match="part of another"):
        x.validate_record(record(doc), doc)


def test_identity_mismatch(tmp_path):
    doc = {**document(), "arxiv_id": "2401.11111", "version": "v2", "version_validated": True}
    with pytest.raises(ValueError, match="version"):
        x.extract_paper({"arxiv_id": "2401.22222", "version": "v2"}, doc, tmp_path)



def test_real_adapter_subprocess_and_resume(monkeypatch, tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text('{"model":"fixture","thinking":"low"}')
    adapter = tmp_path / "fixture.mjs"
    doc = document()
    output = {"text": record(doc).model_dump_json(), "stopReason": "stop", "usage": {"input": 10, "output": 20}}
    adapter.write_text("""
import {readFile, writeFile} from 'node:fs/promises';
let input=''; for await (const part of process.stdin) input+=part;
if(input.includes(process.env.JEPA_TEST_SECRET)) process.exit(2);
const profile=JSON.parse(await readFile(process.env.JEPA_PROFILE_PATH,'utf8'));
if(profile.model!=='fixture') process.exit(3);
const response=%s;
if (JSON.parse(input).system.startsWith('Reconcile')) {
 const claim={text:'M',attribution:'analyst',evidence:['c0:e1']};
 response.text=JSON.stringify({summary:claim,relevance:'direct',relevance_reason:claim,paper_scope:claim,keywords:[],topics:[],quality:{}});
}
process.stdout.write(JSON.stringify(response));
""" % json.dumps(output))
    monkeypatch.setenv("JEPA_MODEL_ADAPTER", str(adapter))
    monkeypatch.setenv("JEPA_PROFILE_PATH", str(profile))
    monkeypatch.setenv("JEPA_TEST_SECRET", "DO_NOT_INCLUDE_AMBIENT_SECRETS")
    result = x.extract_paper({}, doc, tmp_path / "runtime")
    assert result["processing"]["chunks_validated"] == 1
    assert result["experiments"][0]["numeric_value"] == 94.5
    assert result["processing"]["receipts"][0]["usage"]["input"] == 10
    # Cached checkpoint works even if the node executable becomes unavailable.
    monkeypatch.setattr(x, "call_model", lambda *args: pytest.fail("unexpected resumed call"))
    resumed = x.extract_paper({}, doc, tmp_path / "runtime")
    assert resumed["evidence"] == result["evidence"]


def test_bounded_repair_preserves_receipts(monkeypatch, tmp_path):
    doc = document()
    bad = record(doc)
    bad.experiments[0].direction = "higher"
    calls = []
    def call(payload, timeout, env):
        calls.append(payload)
        return {"text": (bad if len(calls) == 1 else record(doc)).model_dump_json(),
                "stopReason": "stop", "usage": {"output": 7}}
    monkeypatch.setattr(x, "call_model", call)
    result = x.validated_call({"system": "extract", "prompt": json.dumps(doc)}, doc, tmp_path,
        "test", x.time.monotonic()+30, {}, lambda text: x.validate_record(Extraction.model_validate_json(text), doc))
    assert result.experiments[0].direction is None
    assert len(calls) == 2
    assert "Metric direction in row 0 is not literally grounded" in calls[1]["prompt"]
    assert len(list(tmp_path.glob("response-*.json"))) == 2


def test_safe_schema_diagnostic():
    try:
        Extraction.model_validate({"relevance": "secret-token"})
    except ValueError as error:
        assert "secret-token" not in x.safe_validation_error(error)


def finding_record(doc):
    from jepa.models import Finding
    r = record(doc)
    row = r.experiments.pop()
    r.findings = [Finding(id="f1", name="main outcome",
        interpretation=r.sections.contributions[0], key_results=[row],
        caveats=[r.sections.contributions[0].model_copy(deep=True)], evidence=["e1"])]
    return r


def test_findings_api_and_legacy_compatibility():
    from jepa.models import Finding, Experiment
    assert Extraction().findings == []
    assert Finding.model_json_schema()["properties"]["key_results"]["maxItems"] == 5
    assert "label_budget" in Experiment.model_fields
    assert record(document()).experiments


def test_nested_finding_grounding_and_label_budget():
    doc = document("method M dataset D metric accuracy value 94.5% test protocol fixed 1% labels")
    r = finding_record(doc)
    row = r.findings[0].key_results[0]
    row.label_budget = "1%"
    assert x.validate_record(r, doc).findings[0].key_results[0].numeric_value == 94.5
    assert row.split is None
    row.split = "1%"
    with pytest.raises(ValueError, match="label budget"):
        x.validate_record(r, doc)
    row.split = None
    row.label_budget = "10%"
    with pytest.raises(ValueError, match="label_budget"):
        x.validate_record(r, doc)


@pytest.mark.parametrize("target", ["interpretation", "caveat", "finding", "number"])
def test_reject_bad_nested_finding(target):
    doc = document()
    r = finding_record(doc)
    f = r.findings[0]
    if target == "interpretation": f.interpretation.evidence = ["unknown"]
    elif target == "caveat": f.caveats[0].evidence = ["unknown"]
    elif target == "finding": f.evidence = ["unknown"]
    else: f.key_results[0].value = "94.6%"
    with pytest.raises(ValueError): x.validate_record(r, doc)


def test_existing_interpretation_no_full_rerun(monkeypatch, tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"model": "selected", "thinking": "low"}))
    monkeypatch.setenv("JEPA_PROFILE_PATH", str(profile))
    doc = document()
    original = record(doc).model_dump()
    before = json.dumps(original, sort_keys=True)
    claim = original["sections"]["contributions"][0]
    calls = []
    def call(payload, timeout, env):
        data = json.loads(payload["prompt"])
        calls.append(data)
        assert "validated_record" in data and "chunk_index" not in data
        result = {"summary": claim, "relevance": "direct", "relevance_reason": claim,
                  "paper_scope": claim, "keywords": [], "topics": [], "quality": {},
                  "findings": [finding_record(doc).findings[0].model_dump()],
                  "revisions": [claim]}
        return {"text": json.dumps(result), "stopReason": "stop", "usage": {"output": 10}}
    monkeypatch.setattr(x, "call_model", call)
    output = x.interpret_existing({}, original, doc, tmp_path)
    assert len(calls) == 1
    assert json.dumps(original, sort_keys=True) == before
    assert output["experiments"] == [] and len(output["findings"]) == 1
    assert output["processing"]["input_record_sha256"] == digest(before.encode())
    assert not output["validation"]["source_support_checked"]
    assert not output["validation"]["human_reviewed"]
    assert output["processing"]["revisions"] == [claim]
    assert __import__('pathlib').Path(output["processing"]["candidate_path"]).exists()


def test_chunk_findings_namespaced_then_reconciled(monkeypatch, tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"model": "selected", "thinking": "low"}))
    monkeypatch.setenv("JEPA_PROFILE_PATH", str(profile))
    monkeypatch.setenv("JEPA_CHUNK_CHARS", "1000")
    doc = document()
    doc["blocks"].append(document("second " * 140)["blocks"][0])
    def call(payload, timeout, env):
        data = json.loads(payload["prompt"])
        if "chunk_index" in data:
            r = finding_record({"blocks": data["blocks"]})
            # Second block contains no numeric result; only its cited interpretation.
            if data["chunk_index"]:
                r.findings[0].key_results = []
            return {"text": r.model_dump_json(), "stopReason": "stop"}
        r = data["validated_record"]
        assert [f["id"] for f in r["findings"]] == ["c0:f1", "c1:f1"]
        assert r["findings"][1]["caveats"][0]["evidence"] == ["c1:e1"]
        assert r["findings"][0]["key_results"][0]["id"] == "c0:r1"
        claim = {"text": "paper scope", "attribution": "analyst", "evidence": ["c0:e1", "c1:e1"]}
        merged = r["findings"][0]
        merged["evidence"] = claim["evidence"]
        result = {"summary": claim, "relevance": "direct", "relevance_reason": claim,
                  "paper_scope": claim, "keywords": [], "topics": [], "quality": {},
                  "findings": [merged]}
        return {"text": json.dumps(result), "stopReason": "stop"}
    monkeypatch.setattr(x, "call_model", call)
    output = x.extract_paper({}, doc, tmp_path)
    assert len(output["findings"]) == 1
    assert output["findings"][0]["evidence"] == ["c0:e1", "c1:e1"]

import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('usage_report',Path(__file__).resolve().parents[1]/'scripts/usage-report.py')
usage=importlib.util.module_from_spec(spec);spec.loader.exec_module(usage)


def test_returned_usage_deduplicates_without_claiming_charges(tmp_path):
    response={'provider':'synthetic','model':'synthetic','started_at':'start','ended_at':'end',
              'stopReason':'stop','usage':{'input':3,'output':2,'totalTokens':5,'cost':{'total':999}}}
    path=tmp_path/'extractions/job';path.mkdir(parents=True)
    (path/'response-1.json').write_text(json.dumps(response))
    run=tmp_path/'runs/job';run.mkdir(parents=True)
    (run/'1-usage.jsonl').write_text(json.dumps(response)+'\n')
    result=usage.report(tmp_path)
    assert result['recorded_pipeline_calls']==1
    assert result['provider_returned_tokens']['totalTokens']==5
    assert result['subscription_charges'].startswith('unknown')
    assert '999' not in json.dumps(result)

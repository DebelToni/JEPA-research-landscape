"""Metadata/summary classification, separate from immutable scientific extractions."""
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import time
import uuid
from .extract import call_model, atomic_json, _resolved_profile
from . import taxonomy

PROMPT = ('Classify research papers using the supplied controlled taxonomy. '
          'Return JSON {"papers":[{"work_id":string,"category_ids":[string],'
          '"status":"classified|needs_review|insufficient_metadata","reason":string}]}. '
          'Exactly one result per input work_id. Reasons must be concise (at most 300 characters). '
          'Categories describe research topics, not proof of novelty, rigor or scientific quality. '
          'A descriptive title can justify topic classification even without an abstract. '
          'Use the closest applicable defined leaf; do not require an exact wording match or a specialized leaf for every subfield. '
          'Conceptual viewpoints can be classified by their central theoretical subject. '
          + taxonomy.instructions())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def paper_input(p):
    value = {'work_id': p['work_id'], 'version': p.get('version'), 'title': p.get('title') or ''}
    if p.get('abstract'): value['abstract_excerpt'] = p['abstract'][:6000]
    summary = (p.get('extraction') or {}).get('summary')
    if summary: value['extraction_summary'] = summary[:2400]
    return value


def classification(row, source, model, fingerprint):
    result = {'taxonomy_version': taxonomy.VERSION, 'status': row['status'],
              'category_ids': sorted(row['category_ids']), 'reason': row['reason'],
              'basis': [k for k in ('title', 'abstract_excerpt', 'extraction_summary') if source.get(k)],
              'model': model, 'human_reviewed': False, 'input_sha256': fingerprint, 'source_sha256': digest(source),
              'prompt_sha256': digest(PROMPT),
              'created_at': datetime.now(timezone.utc).isoformat()}
    taxonomy.validate_assignment(result)
    if result['status'] == 'pending' or len(result['reason']) > 300:
        raise ValueError('Invalid classification status or overlong reason')
    return result


def _batch(papers, deadline, env, directory, profile):
    inputs = [paper_input(p) for p in papers]
    key = digest(inputs)
    atomic_json(directory/f'{key}-input.json', {'papers': inputs, 'taxonomy_sha256': taxonomy.SHA256,
                                              'prompt_sha256': digest(PROMPT), 'profile': profile})
    last_error = None
    for attempt in range(2):
        remaining = deadline-time.monotonic()
        if remaining < 2: raise TimeoutError('Classification deadline')
        timeout = min(180, remaining)
        payload = {'system': PROMPT, 'prompt': json.dumps(inputs, ensure_ascii=False),
                   'max_tokens': 8000, 'timeout_ms': max(1, int((timeout-.5)*1000))}
        if last_error:
            payload['prompt'] += '\nThe previous attempt was invalid. Return all requested UUIDs with approved leaf IDs and valid JSON.'
        try:
            response = call_model(payload, timeout, env)
            atomic_json(directory/f'{key}-response-{attempt}.json', response)
            with (directory/f'{key}-usage.jsonl').open('a') as out:
                out.write(json.dumps({k:v for k,v in response.items() if k != 'text'})+'\n')
            if response.get('stopReason') != 'stop': raise RuntimeError('Classification call incomplete')
            rows = json.loads(response['text'])['papers']
            expected = {p['work_id'] for p in papers}
            if len(rows) != len(papers) or {r['work_id'] for r in rows} != expected:
                raise ValueError('Classification identity mismatch')
            by_id = {p['work_id']: (p, value) for p, value in zip(papers, inputs)}
            records = []
            for row in rows:
                p, value = by_id[row['work_id']]
                fingerprint = digest({'input': value, 'taxonomy': taxonomy.SHA256, 'prompt': digest(PROMPT), 'profile': profile})
                result = classification(row, value, response.get('model'), fingerprint)
                records.append((p['work_id'], p['manifestation_id'], result))
            return records
        except (ValueError, KeyError, TypeError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
            last_error = error
            if attempt == 1: raise
            time.sleep(min(2, max(0, deadline-time.monotonic())))
    raise last_error


def classify(store, seconds=1800, workers=4, limit=None, ids=None):
    if not 0 < seconds <= 28800 or not 1 <= workers <= 8:
        raise ValueError('Classification duration must be <=8h; workers must be 1..8')
    started = time.monotonic();deadline = started+seconds
    directory = store.root/'runs'/('classify-'+str(uuid.uuid4()));directory.mkdir(parents=True)
    candidates = [];counts = Counter();failures = []
    for p in store.papers():
        if ids and p['work_id'] not in ids and p.get('arxiv_id') not in ids: continue
        current = p['classification']
        if current['status'] == 'classified': continue
        if current['status'] in ('needs_review', 'insufficient_metadata') and current.get('source_sha256') == digest(paper_input(p)) and current.get('prompt_sha256') == digest(PROMPT): continue
        if not p.get('title'):
            if current['status'] != 'insufficient_metadata':
                source = paper_input(p)
                result = classification({'status':'insufficient_metadata','category_ids':[],
                                         'reason':'No paper title or sufficient bibliographic description is available.'},
                                        source, None, digest({'input':source,'taxonomy':taxonomy.SHA256}))
                if store.set_classification(p['work_id'],p['manifestation_id'],result):
                    counts['insufficient_metadata'] += 1
            continue
        candidates.append(p)
    if limit is not None: candidates = candidates[:limit]
    if candidates:
        env = dict(os.environ);profile = _resolved_profile(env)
        atomic_json(directory/'profile.json', profile);env['JEPA_PROFILE_PATH'] = str((directory/'profile.json').resolve())
        batches = deque(candidates[i:i+24] for i in range(0,len(candidates),24))
        active = {}
        # This command has no document parsers. CLI run invokes it in a separate
        # process so thread allocator arenas are never inherited by parser forks.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while (batches or active) and time.monotonic() < deadline:
                while batches and len(active) < workers and len(failures) < 3 and deadline-time.monotonic() > 2:
                    batch = batches.popleft()
                    active[pool.submit(_batch,batch,deadline,env,directory,profile)] = [p['work_id'] for p in batch]
                if not active: break
                done, _ = wait(active, timeout=min(1,max(0,deadline-time.monotonic())), return_when=FIRST_COMPLETED)
                for future in done:
                    work_ids = active.pop(future)
                    try:
                        for wid, mid, record in future.result():
                            if store.set_classification(wid,mid,record): counts[record['status']] += 1
                    except (ValueError, KeyError, TypeError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
                        failures.append({'work_ids':work_ids,'error':type(error).__name__})
                if len(failures) >= 3: batches.clear()
            # Calls are already bounded by the shared deadline. Keep completed
            # results even if the final poll landed at the boundary.
            for future, work_ids in active.items():
                try:
                    for wid, mid, record in future.result():
                        if store.set_classification(wid,mid,record): counts[record['status']] += 1
                except (ValueError, KeyError, TypeError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
                    failures.append({'work_ids':work_ids,'error':type(error).__name__})
    receipt = {'run_id':directory.name,'taxonomy_version':taxonomy.VERSION,'taxonomy_sha256':taxonomy.SHA256,
               'elapsed_seconds':time.monotonic()-started,'counts':dict(counts),'failed_batches':failures,
               'remaining':sum(p['classification']['status']=='pending' for p in store.papers())}
    atomic_json(directory.with_suffix('.json'),receipt)
    return receipt

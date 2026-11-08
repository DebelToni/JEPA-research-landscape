"""Synthetic process lifetimes and mocked provider/cache regression tests only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from jepa import documents as d, extract as x, cli
from jepa.models import Extraction
from jepa.store import Store
from test_extraction import document, record
from test_cli_integration import add, fake_parse


def wait_for(predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('synthetic process did not reach expected state')


def dead(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split(') ')[1].startswith('Z')
    except FileNotFoundError:
        return True


@pytest.mark.parametrize('mode', ['parser', 'pdf'])
@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGKILL])
def test_spawn_sites_die_with_parent(tmp_path, mode, stop_signal):
    """Exercise both real spawn sites, without parsing or invoking real PDF code."""
    marker = tmp_path/'pid'
    fake = tmp_path/'pdftotext'
    fake.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    fake.chmod(0o700)
    script = f'''
from jepa import documents as d
from pathlib import Path
import os,time
marker = Path({str(marker)!r})
if {mode!r} == 'parser':
    def sandbox(workspace):
        marker.write_text(str(os.getpid()))
        time.sleep(60)
    d._sandbox = sandbox
    d.isolated_parse(b'x', 'html', 'sha', 'https://example.org', 60)
else:
    d.shutil.which = lambda _: {str(fake)!r}
    d._parse_pdf(Path({str(tmp_path/'input')!r}), 'sha', 'https://example.org')
'''
    parent = subprocess.Popen([sys.executable, '-c', script], start_new_session=True)
    pid = None
    try:
        wait_for(lambda: marker.exists() and marker.read_text().isdigit())
        pid = int(marker.read_text())
        assert not dead(pid)
        parent.send_signal(stop_signal)
        parent.wait(timeout=5)
        wait_for(lambda: dead(pid))
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if pid and not dead(pid):
            os.kill(pid, signal.SIGKILL)


def test_parent_race_check():
    # A wrong expected PPID models death before prctl registration.
    child = subprocess.Popen([sys.executable, '-c',
        'from jepa.documents import _parent_death_kill; _parent_death_kill(-1)'])
    assert child.wait(timeout=5) == -signal.SIGKILL


def test_parser_timeout_kills_owned_descendants(tmp_path, monkeypatch):
    marker = tmp_path/'pid'
    # The executed synthetic parser spawns an owned descendant.
    script = tmp_path/'parser.py'
    script.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
        f'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\nPath({str(marker)!r}).write_text(str(p.pid))\ntime.sleep(60)\n')
    monkeypatch.setattr(d, '__file__', str(script))
    monkeypatch.setattr(d, '_sandbox', lambda workspace: None)
    pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            d.isolated_parse(b'x', 'html', 'sha', 'https://example.org', .3)
        pid = int(marker.read_text())
        wait_for(lambda: dead(pid))
    finally:
        if pid and not dead(pid):
            os.kill(pid, signal.SIGKILL)


def setup_profile(tmp_path, monkeypatch):
    profile = tmp_path/'profile.json'
    profile.write_text(json.dumps({'model': 'mock', 'thinking': 'low'}))
    monkeypatch.setenv('JEPA_PROFILE_PATH', str(profile))
    return profile


@pytest.mark.parametrize('legacy', [False, True])
def test_generation_separate_from_validation(tmp_path, monkeypatch, legacy):
    setup_profile(tmp_path, monkeypatch)
    doc = document()
    def validated(payload, document, directory, label, deadline, env, parse):
        if label == 'reconcile':
            raise ValueError('mock local failure')
        return record(document)
    monkeypatch.setattr(x, 'validated_call', validated)
    monkeypatch.setattr(x, 'IMPLEMENTATION_SHA', 'generation-A')
    first = x.extract_paper({}, doc, tmp_path, 5)
    checkpoint = next((tmp_path/'extractions').glob('*/chunk-00000.json'))
    if legacy:
        saved = json.loads(checkpoint.read_text())
        saved['metadata'] = {'chunk': 0}
        checkpoint.write_text(json.dumps(saved))
    before = checkpoint.read_bytes()
    monkeypatch.setattr(x, 'IMPLEMENTATION_SHA', 'validation-B')
    def no_chunk(*args):
        assert args[3] == 'reconcile', 'valid checkpoint should be reused'
        raise ValueError('mock local failure')
    monkeypatch.setattr(x, 'validated_call', no_chunk)
    second = x.extract_paper({}, doc, tmp_path, 5)
    meta = second['processing']['chunk_provenance'][0]
    assert meta['generation_implementation_sha256'] == ('unknown-legacy' if legacy else 'generation-A')
    assert meta['validation_implementation_sha256'] == 'validation-B'
    assert second['processing']['job_sha256'] == first['processing']['job_sha256']
    assert checkpoint.read_bytes() == before


def test_provider_error_stops_chunks(tmp_path, monkeypatch):
    setup_profile(tmp_path, monkeypatch)
    doc = document(); doc['blocks'] *= 3
    monkeypatch.setattr(x, 'chunk_document', lambda *args: [[b] for b in doc['blocks']])
    calls = []
    def fail(*args):
        calls.append(1)
        return {'stopReason': 'error', 'text': 'mock'}
    monkeypatch.setattr(x, 'call_model', fail)
    with pytest.raises(x.ProviderUnavailable):
        x.extract_paper({}, doc, tmp_path, 5)
    assert len(calls) == 1


@pytest.mark.parametrize('reason', ['length', 'stop', 'aborted'])
def test_local_response_failure_does_not_stop_run(tmp_path, monkeypatch, reason):
    setup_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(x, 'call_model', lambda *args: {'stopReason': reason, 'text': '{}'})
    output = x.extract_paper({}, document(), tmp_path, 5)
    assert output['processing']['failed_chunks']


def test_profile_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('JEPA_PROFILE_PATH', str(tmp_path/'missing'))
    with pytest.raises(x.ProviderUnavailable):
        x.extract_paper({}, document(), tmp_path, 5)


@pytest.mark.parametrize('response', [subprocess.CompletedProcess([], 1, ''),
    subprocess.CompletedProcess([], 0, 'not json'), subprocess.CompletedProcess([], 0, '{}')])
def test_adapter_failure(response, monkeypatch):
    monkeypatch.setattr(x.subprocess, 'run', lambda *args, **kwargs: response)
    with pytest.raises(x.ProviderUnavailable):
        x.call_model({}, 1, {})


def test_provider_failure_stops_run_queue(tmp_path, monkeypatch):
    store = Store(tmp_path)
    targets = [add(store, n) for n in range(1, 4)]
    monkeypatch.setattr(cli, 'acquire_and_parse', fake_parse)
    def fail(*args):
        raise x.ProviderUnavailable('mock')
    monkeypatch.setattr(cli, 'extract_paper', fail)
    try:
        receipt = cli.process(store, 3, workers=1)
        assert sum(e.get('reason') == 'provider unavailable' for e in receipt['events']) == 1
        assert store.db.execute('SELECT sum(attempts) FROM jobs').fetchone()[0] == 1
        assert store.stats()['jobs']['pending'] == 2
    finally:
        store.close()


@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGKILL])
def test_parent_death_cascades_through_both_spawn_sites(tmp_path, stop_signal):
    parser_pid = tmp_path/'parser-pid'
    pdf_pid = tmp_path/'pdf-pid'
    fake = tmp_path/'pdftotext'
    fake.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pdf_pid)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    fake.chmod(0o700)
    parser = tmp_path/'parser.py'
    parser.write_text('from jepa import documents as d\nfrom pathlib import Path\nimport os\n'
        f'Path({str(parser_pid)!r}).write_text(str(os.getpid()))\n'
        f'd.shutil.which=lambda _: {str(fake)!r}\n'
        f'd._parse_pdf(Path({str(tmp_path/"input")!r}), "sha", "https://example.org")\n')
    script = ('from jepa import documents as d\n'
        f'd.__file__={str(parser)!r}\n'
        'd._sandbox=lambda workspace: None\n'
        'd.isolated_parse(b"x", "pdf", "sha", "https://example.org", 60)\n')
    parent = subprocess.Popen([sys.executable, '-c', script], start_new_session=True)
    pids = []
    try:
        wait_for(lambda: pdf_pid.exists() and pdf_pid.read_text().isdigit())
        pids = [int(p.read_text()) for p in (parser_pid, pdf_pid)]
        assert all(not dead(pid) for pid in pids)
        parent.send_signal(stop_signal)
        parent.wait(timeout=5)
        wait_for(lambda: all(dead(pid) for pid in pids))
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        for path in (parser_pid, pdf_pid):
            if path.exists() and path.read_text().isdigit():
                pid = int(path.read_text())
                if not dead(pid):
                    os.kill(pid, signal.SIGKILL)


def test_provider_stop_cleans_active_sibling(tmp_path, monkeypatch):
    store = Store(tmp_path)
    targets = [add(store, n) for n in range(1, 4)]
    monkeypatch.setattr(cli, 'acquire_and_parse', fake_parse)
    marker = tmp_path/'sibling-pid'
    def extract(paper, *args):
        if paper['work_id'] == targets[0]:
            wait_for(lambda: marker.exists())
            raise x.ProviderUnavailable('mock')
        marker.write_text(str(os.getpid()))
        time.sleep(60)
    monkeypatch.setattr(cli, 'extract_paper', extract)
    started = time.monotonic()
    try:
        receipt = cli.process(store, 3, workers=2)
        assert time.monotonic()-started < 1.5
        assert any(e.get('reason') == 'provider unavailable' for e in receipt['events'])
        assert any(e['status'] == 'interrupted' for e in receipt['events'])
        assert dead(int(marker.read_text()))
        assert store.db.execute('SELECT sum(attempts) FROM jobs').fetchone()[0] == 2
        assert store.stats()['jobs']['pending'] == 2
    finally:
        store.close()


def test_interpret_profile_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('JEPA_PROFILE_PATH', str(tmp_path/'missing'))
    doc = document()
    with pytest.raises(x.ProviderUnavailable):
        x.interpret_existing({}, record(doc).model_dump(), doc, tmp_path, 5)

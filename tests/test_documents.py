import io
import socket
import tarfile
from pathlib import Path
import pytest
from jepa import documents as d


def archive(name="paper.tex", kind=None, data=b"hello"):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        info = tarfile.TarInfo(name)
        if kind:
            info.type = kind
            info.linkname = "/etc/passwd"
        else:
            info.size = len(data)
        tar.addfile(info, None if kind else io.BytesIO(data))
    return out.getvalue()


@pytest.mark.parametrize("name,kind", [("../escape", None), ("/escape", None), ("a\\b", None),
    ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE)])
def test_archive_rejects(name, kind):
    with pytest.raises(ValueError):
        d.source_files(archive(name, kind))


def test_expansion_and_members(monkeypatch):
    monkeypatch.setattr(d, "MAX_EXPANDED", 2)
    with pytest.raises(ValueError):
        d.source_files(archive())


def test_source_no_execution(tmp_path):
    data = br"\documentclass{article}\input{/etc/passwd}\write18{touch OWNED}"
    blocks = d.isolated_parse(data, "source", d.digest(data), "https://example.org/source", 10)
    assert "write18" in blocks[0]["text"]
    assert not (tmp_path / "OWNED").exists()


def test_html_is_nonexecuting_and_stable():
    data = b'<html><article><h2 id="results">Results</h2><script>steal()</script><p>accuracy 94%</p><table><tr><td>95</td></tr></table></article></html>'
    sha = d.digest(data)
    a = d.parse_html(data, sha, "https://example.org/paper")
    assert a == d.parse_html(data, sha, "https://example.org/paper")
    assert not any("steal" in x["text"] for x in a)
    assert len(a) == 3 and a[-1]["kind"] == "table"


@pytest.mark.parametrize("url", ["http://example.org", "https://a:b@example.org", "https://example.org:8443", "file:///etc/passwd", "https://example.org/#secret"])
def test_bad_urls(url):
    with pytest.raises(ValueError):
        d.public_address(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "100.64.0.1"])
def test_private_dns(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (ip, 443))])
    with pytest.raises(ValueError):
        d.public_address("https://example.org/paper")


def test_version_not_inferred_from_old_history_or_url():
    data = b'<html><meta name="citation_arxiv_id" content="2401.12345"><p>arXiv:2401.12345 (this version, v2)</p><a href="/abs/2401.12345v1">v1 history</a></html>'
    assert d.validate_arxiv_version(data, "2401.12345", None) == "v2"
    with pytest.raises(ValueError):
        d.validate_arxiv_version(data, "2401.12345", "v1")
    with pytest.raises(ValueError):
        d.validate_arxiv_version(b'<html>unversioned</html>', "2401.12345", "v1")


def test_immutable(tmp_path):
    sha = d.immutable(tmp_path, b"source")
    assert d.immutable(tmp_path, b"source") == sha
    (tmp_path / "artifacts" / sha).write_bytes(b"bad")
    with pytest.raises(ValueError):
        d.immutable(tmp_path, b"source")


def test_acquire_html_and_source(monkeypatch, tmp_path):
    abstract = b'<html><p>arXiv:2401.12345v2</p></html>'
    html = b'<html><article><h1>arXiv:2401.12345v2</h1><p>A method.</p></article></html>'
    def fetch(url, timeout):
        if "/abs/" in url:
            return abstract, url
        if "/html/" in url:
            return html, url
        if "/src/" in url:
            return br"\documentclass{article}\begin{document}source", url
        raise ValueError("PDF fixture unavailable")
    monkeypatch.setattr(d, "fetch", fetch)
    doc = d.acquire_and_parse({"arxiv_id": "2401.12345", "version": "v2"}, tmp_path)
    assert doc["version"] == "v2" and doc["version_validated"]
    assert len(doc["artifacts"]) == 2
    assert doc["blocks"]


def test_pdf_fallback_label(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "fetch", lambda url, timeout: (b"%PDF-1.4 fake", url))
    monkeypatch.setattr(d, "isolated_parse", lambda data, kind, sha, url, timeout:
                        [d._block("numbers", sha, "PDF physical page 1", url, "pdf_text", 1)])
    doc = d.acquire_and_parse({"url": "https://example.org/p.pdf"}, tmp_path)
    assert "low_fidelity" in doc["warnings"][0]
    assert doc["blocks"][0]["page"] == 1



def test_parser_sandbox_denies_secrets_network_and_external_write(tmp_path):
    import json
    import subprocess
    import sys
    workspace = tmp_path / "sandbox"
    workspace.mkdir()
    secret = tmp_path / "credential"
    secret.write_text("synthetic secret")
    code = """
import json, socket
results = []
for action in [lambda: open(%r).read(), lambda: socket.socket(), lambda: open(%r, 'w')]:
    try:
        action()
        results.append('UNSAFE')
    except PermissionError:
        results.append('denied')
print(json.dumps(results))
""" % (str(secret), str(tmp_path / "escape"))
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=workspace,
        capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin:/bin"},
        preexec_fn=lambda: d._sandbox(str(workspace)))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["denied"] * 3


def test_fetch_pins_sni_and_rejects_private_redirect(monkeypatch):
    import httpx
    requests = []
    factory = httpx.Client
    def handler(request):
        requests.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.org"
        assert request.extensions["sni_hostname"] == "example.org"
        return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k:
        [(None, None, None, None, ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443))])
    monkeypatch.setattr(httpx, "Client", lambda **kw: factory(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError, match="Non-public"):
        d.fetch("https://example.org/paper", 5)
    assert len(requests) == 1


def test_actual_pdf_text_parser(tmp_path):
    import shutil
    if not shutil.which("pdftotext"):
        pytest.skip("optional pdftotext not installed")
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    content = b'BT /F1 12 Tf 72 720 Td (Accuracy 94.5 percent) Tj ET'
    objects.append(b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream')
    # Build genuine PDF cross-reference offsets; no third-party generator.
    data = b'%PDF-1.4\n'.replace(b'\\n', b'\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{i} 0 obj\n'.encode().replace(b'\\n', b'\n') + obj.replace(b'\\n', b'\n') + b'\nendobj\n'.replace(b'\\n', b'\n')
    start = len(data)
    data += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode().replace(b'\\n', b'\n')
    for offset in offsets[1:]:
        data += f'{offset:010d} 00000 n \n'.encode().replace(b'\\n', b'\n')
    data += f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode().replace(b'\\n', b'\n')
    blocks = d.isolated_parse(data, "pdf", d.digest(data), "https://example.org/paper.pdf", 10)
    assert blocks[0]["page"] == 1
    assert "94.5" in blocks[0]["text"]


def test_arxiv_html_table_grid_caption_and_public_anchor():
    import json
    data = b'''<article><section id="S4"><h2>4 Results</h2>
    <figure class="ltx_table" id="S4.T2">
    <figcaption class="ltx_caption">Table 2: Top-1 accuracy (%).</figcaption>
    <table class="ltx_tabular"><thead>
    <tr><th rowspan="2">Method</th><th colspan="2">Dataset</th></tr>
    <tr><th>ImageNet</th><th>Places</th></tr></thead><tbody>
    <tr><td>JEPA</td><td>81.2</td><td>54.0</td></tr>
    <tr><td>Baseline</td><td></td><td>52.1</td></tr>
    </tbody></table></figure><p>See Table 2.</p></section></article>'''
    blocks = d.parse_html(data, d.digest(data), 'https://arxiv.org/html/2401.12345v2')
    table = next(block for block in blocks if block['kind'] == 'table')
    grid = json.loads(table['text'])['rows']
    assert grid[0]['cells'][0] == dict(column=1, kind='th', rowspan=2, colspan=1, text='Method')
    assert grid[0]['cells'][1]['colspan'] == 2
    assert [cell['column'] for cell in grid[1]['cells']] == [2, 3]
    assert [cell['text'] for cell in grid[2]['cells']] == ['JEPA', '81.2', '54.0']
    assert grid[3]['cells'][1]['text'] == ''
    assert 'Table 2: Top-1 accuracy (%)' in table['locator']
    assert '#S4.T2' in table['locator']
    assert table['source_url'].endswith('#S4.T2')
    assert blocks[-1]['source_url'].endswith('#S4')
    assert blocks == d.isolated_parse(data, 'html', d.digest(data), 'https://arxiv.org/html/2401.12345v2', 10)


def test_html_math_preserves_source_semantics_without_guessing():
    data = br'''<article><section id="S2"><h2>Objective</h2><p>Loss
    <math alttext="x^2"><semantics><msup><mi>x</mi><mn>2</mn></msup>
    <annotation encoding="application/x-tex">x^{2}</annotation></semantics></math>
    and <math><mfrac><mi>a</mi><mi>b</mi></mfrac></math>.</p>
    <math alttext="y_t"><mi>y</mi></math>
    <script src="https://example.org/execute">touch OWNED</script></section></article>'''
    blocks = d.parse_html(data, d.digest(data), 'https://arxiv.org/html/2401.12345v2')
    assert '[source TeX: x^{2}]' in blocks[1]['text']
    assert '<mfrac><mi>a</mi><mi>b</mi></mfrac>' in blocks[1]['text']
    assert r'\frac' not in blocks[1]['text']
    assert '[source TeX: y_t]' in blocks[2]['text']
    assert not any('OWNED' in block['text'] for block in blocks)


def test_html_native_caption_spans_and_encoded_ids():
    import json
    data = b'''<article><section id="results"><h2>Results</h2>
    <table id="tab:one two"><caption>Table 1: Scores</caption><tbody>
    <tr><th rowspan="0">Group</th><td colspan="2">10</td></tr>
    <tr><td>11</td><td>12</td></tr></tbody>
    <tbody><tr><td>New group</td><td>13</td></tr></tbody></table></section></article>'''
    table = d.parse_html(data, d.digest(data), 'https://example.org/paper#old')[-1]
    grid = json.loads(table['text'])
    assert grid['caption'] == 'Table 1: Scores'
    assert grid['rows'][0]['cells'][0]['rowspan'] == 0
    assert grid['rows'][1]['cells'][0]['column'] == 2
    assert grid['rows'][2]['cells'][0]['column'] == 1
    assert table['source_url'] == 'https://example.org/paper#tab%3Aone%20two'
    assert d.PARSER_VERSION == 'bounded-text-v2-html-grid'

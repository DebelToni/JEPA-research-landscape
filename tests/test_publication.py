import importlib.util
import json
from pathlib import Path
import threading
from functools import partial
from http.server import ThreadingHTTPServer
import httpx
from jepa.serve import Handler
from jepa.citations import bibliography_ids


def test_bibliography_only_explicit_links():
    html=b'<a href="https://arxiv.org/abs/1111.11111">not a reference</a><li class="ltx_bibitem"><a href="https://arxiv.org/abs/2301.08243v3">IJEPA</a><a href="https://doi.org/10.48550/arXiv.2411.04983">DINO</a></li>'
    assert bibliography_ids(html)==['2301.08243','2411.04983']


def test_static_origin_boundaries(tmp_path):
    root=tmp_path/'release';root.mkdir();(root/'index.html').write_text('<h1>JEPA</h1>')
    (root/'data').mkdir();(root/'data/manifest.json').write_text(json.dumps({'schema_version':'1.0','coverage':{'papers':2,'extracted':1},'generated_at':'now'}))
    (root/'.env').write_text('SECRET');outside=tmp_path/'secret';outside.write_text('SECRET');(root/'escape').symlink_to(outside)
    (root/'data.previous').mkdir();(root/'data.previous/catalog.json').write_text('PRIVATE_LEGACY_ROWS')
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,publication=root))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as c:
            assert c.get('/').status_code==200
            assert c.get('/healthz').json()['papers']==2
            assert c.get('/.env').status_code==404
            assert c.get('/data.previous/catalog.json').status_code==404
            assert c.get('/data/').status_code==404
            assert c.get('/escape').status_code==404
            assert c.post('/run').status_code==501
            assert c.get('/').headers['cache-control']=='no-cache'
            assert "script-src 'self'" in c.get('/').headers['content-security-policy']
    finally:
        server.shutdown();server.server_close();thread.join(2)

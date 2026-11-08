"""Read-only public origin: static files and agent queries over published snapshots."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
import io
import threading
from .api import AgentAPI, APIError
from .api_docs import OPENAPI

AGENT_INSTRUCTIONS = Path(__file__).with_name('agent-api.txt').read_bytes()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, publication, api=None, **kwargs):
        self.publication = Path(publication)
        self.api = api
        super().__init__(*args, directory=str(self.publication.resolve()), **kwargs)

    def bytes_response(self, body, content_type, status=200, snapshot=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if snapshot:
            self.send_header('X-JEPA-Snapshot', snapshot)
        if status == 405:
            self.send_header('Allow', 'GET, HEAD, OPTIONS')
        self.end_headers()
        return io.BytesIO(body)

    def json_response(self, value, status=200):
        return self.bytes_response(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8',
                                   status, value.get('snapshot', {}).get('id'))

    def send_head(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == '/llms.txt':
            return self.bytes_response(AGENT_INSTRUCTIONS, 'text/plain; charset=utf-8')
        if path == '/openapi.json':
            return self.json_response(OPENAPI)
        if path == '/api' or path.startswith('/api/'):
            try:
                if self.api is None:
                    raise APIError(503, 'snapshot_unavailable', 'Agent API is not configured.')
                return self.json_response(self.api.respond(path, parsed.query, Path(self.directory)))
            except APIError as error:
                return self.json_response({'error': {'code': error.code, 'message': error.message}}, error.status)
        allowed_roots = {'', 'healthz', 'index.html', 'catalog-index.json', 'assets', 'data', 'papers', 'methodology', 'THIRD-PARTY-LICENSES.txt'}
        if (path.lstrip('/').split('/')[0] not in allowed_roots or '\\' in path
                or any(p.startswith('.') or p.endswith('.previous') for p in path.split('/') if p)):
            self.send_error(404)
            return None
        if path == '/healthz':
            try:
                manifest = json.loads((Path(self.directory)/'data/manifest.json').read_text())
                body = json.dumps({'service':'jepa-landscape','schema_version':manifest['schema_version'],
                                   'papers':manifest['coverage']['papers'],
                                   'extracted':manifest['coverage']['extracted'],
                                   'generated_at':manifest['generated_at']}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body))); self.end_headers()
                return io.BytesIO(body)
            except (OSError, ValueError, KeyError):
                self.send_error(503)
                return None
        target = Path(self.translate_path(self.path)).resolve()
        if not target.is_relative_to(Path(self.directory)):
            self.send_error(404)
            return None
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def parse_request(self):
        if not super().parse_request():
            return False
        path = unquote(urlsplit(self.path).path)
        if (path == '/api' or path.startswith('/api/')) and self.command not in ('GET', 'HEAD', 'OPTIONS'):
            with self.json_response({'error': {'code': 'method_not_allowed', 'message': 'This API is read-only.'}}, 405) as body:
                self.copyfile(body, self.wfile)
            return False
        return True

    def do_OPTIONS(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/api' or path.startswith(('/api/', '/data/')) or path in ('/llms.txt', '/openapi.json'):
            self.send_response(204)
            self.send_header('Allow', 'GET, HEAD, OPTIONS')
            self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Accept, Content-Type')
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            self.send_error(501)

    def end_headers(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/api' or path.startswith(('/api/', '/data/')) or path in ('/llms.txt', '/openapi.json'):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Expose-Headers', 'X-JEPA-Snapshot')
        self.send_header('Cache-Control','public, max-age=31536000, immutable' if urlsplit(self.path).path.startswith('/assets/') else 'no-cache')
        self.send_header('Link', '</llms.txt>; rel="describedby", </openapi.json>; rel="service-desc"; type="application/vnd.oai.openapi+json"')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def log_message(self, *_args):
        pass  # Do not retain arbitrary visitor paths or queries.


class PublicServer(ThreadingHTTPServer):
    """Bound threads and idle socket lifetime on the public origin."""
    max_connections = 32

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        request.settimeout(30)
        if not self.slots.acquire(blocking=False):
            body = b'{"error":{"code":"busy","message":"Server busy. Retry later."}}'
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nRetry-After: 5\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            except OSError:
                pass  # A disconnected client cannot receive the bounded refusal.
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication',type=Path,required=True)
    parser.add_argument('--port',type=int,default=40173)
    args=parser.parse_args()
    server=PublicServer(('127.0.0.1',args.port),partial(Handler,publication=args.publication,api=AgentAPI()))
    server.serve_forever()

if __name__=='__main__':main()

"""Read-only public origin: static files and agent queries over published snapshots."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import ipaddress
import os
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
import io
import threading
from .api import AgentAPI, APIError
from .api_docs import OPENAPI
from .http_cache import Document, ResponseCache, ClientBudgets, wants_gzip, matches_etag

AGENT_INSTRUCTIONS = Document(Path(__file__).with_name('agent-api.txt').read_bytes(), 'text/plain; charset=utf-8')
OPENAPI_DOCUMENT = Document(json.dumps(OPENAPI, ensure_ascii=False).encode(), 'application/json; charset=utf-8')


class Handler(SimpleHTTPRequestHandler):
    # HTTP/1.0 deliberately releases a thread after each response. Cloudflare
    # handles public HTTP/2/3; idle upstream keep-alives cannot occupy all slots.
    def __init__(self, *args, publication, api=None, **kwargs):
        self.publication = Path(publication)
        self.api = api
        self.response_status = 200
        super().__init__(*args, directory=str(self.publication.resolve()), **kwargs)

    def send_response(self, code, message=None):
        self.response_status = code
        super().send_response(code, message)

    def representation(self, size, content_type, etag=None, encoding=None, snapshot=None, modified=None):
        unchanged = etag and matches_etag(self.headers.get('If-None-Match', ''), etag)
        if not unchanged and self.command != 'HEAD':
            wait = self.server.budgets.charge(self.client_key, size=size)
            if wait:
                return self.error_document(429, 'rate_limited', 'Download budget exceeded. Retry later.', wait)
        self.send_response(304 if unchanged else 200)
        self.send_header('Content-Type', content_type)
        if not unchanged:
            self.send_header('Content-Length', str(size))
        if etag:
            self.send_header('ETag', etag)
        if encoding:
            self.send_header('Content-Encoding', encoding)
        if snapshot:
            self.send_header('X-JEPA-Snapshot', snapshot)
        if modified is not None:
            self.send_header('Last-Modified', self.date_time_string(modified))
        self.end_headers()
        return None if unchanged else True

    def document_response(self, document):
        compressed = wants_gzip(self.headers.get('Accept-Encoding', '')) and document.compressed is not None
        body = document.compressed if compressed else document.body
        result = self.representation(len(body), document.content_type,
                                     document.gzip_etag if compressed else document.etag,
                                     'gzip' if compressed else None, document.snapshot)
        return io.BytesIO(body) if result is True else result

    def error_document(self, status, code, message, retry=None):
        body = json.dumps({'error': {'code': code, 'message': message}}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if retry is not None:
            self.send_header('Retry-After', str(retry))
        if status == 405:
            self.send_header('Allow', 'GET, HEAD, OPTIONS')
        self.end_headers()
        return io.BytesIO(body)

    def cached_json(self, key, create):
        document = self.server.responses.get(key)
        if document is None:
            value = create()
            document = Document(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8',
                                value.get('snapshot', {}).get('id'))
            self.server.responses.put(key, document)
        return self.document_response(document)

    def send_head(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == '/llms.txt':
            return self.document_response(AGENT_INSTRUCTIONS)
        if path == '/openapi.json':
            return self.document_response(OPENAPI_DOCUMENT)
        if path == '/api' or path.startswith('/api/'):
            try:
                if self.api is None:
                    raise APIError(503, 'snapshot_unavailable', 'Agent API is not configured.')
                return self.cached_json((self.directory, path, parsed.query),
                                        lambda: self.api.respond(path, parsed.query, Path(self.directory)))
            except APIError as error:
                return self.error_document(error.status, error.code, error.message)
        allowed_roots = {'', 'healthz', 'index.html', 'catalog-index.json', 'assets', 'data', 'papers', 'methodology',
                         'THIRD-PARTY-LICENSES.txt', 'robots.txt', 'sitemap.xml'}
        if (path.lstrip('/').split('/')[0] not in allowed_roots or '\\' in path
                or any(p.startswith('.') or p.endswith('.previous') for p in path.split('/') if p)):
            self.send_error(404)
            return None
        if path == '/healthz':
            def health():
                manifest = json.loads((Path(self.directory)/'data/manifest.json').read_text())
                return {'service': 'jepa-landscape', 'schema_version': manifest['schema_version'],
                        'papers': manifest['coverage']['papers'], 'extracted': manifest['coverage']['extracted'],
                        'generated_at': manifest['generated_at']}
            try:
                return self.cached_json((self.directory, path), health)
            except (OSError, ValueError, KeyError):
                return self.error_document(503, 'snapshot_unavailable', 'Published catalogue is unavailable.')
        root = Path(self.directory)
        target = Path(self.translate_path(self.path)).resolve()
        if not target.is_relative_to(root):
            self.send_error(404)
            return None
        if target.is_dir():
            if not parsed.path.endswith('/'):
                return super().send_head()  # Standard directory redirect, never a listing.
            target = (target/'index.html').resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self.send_error(404)
            return None
        content_type = self.guess_type(str(target))
        encoding = None
        if wants_gzip(self.headers.get('Accept-Encoding', '')):
            variant = (root/'.http'/(str(target.relative_to(root))+'.gz')).resolve()
            if not variant.is_relative_to(root/'.http'):
                self.send_error(404)
                return None
            if variant.is_file():
                target, encoding = variant, 'gzip'
        try:
            source = target.open('rb')
        except OSError:
            self.send_error(404)
            return None
        stat = os.fstat(source.fileno())
        # Release paths are immutable and content-addressed. Metadata also
        # distinguishes mutable development fixtures, without rereading files.
        identity = f'{target}:{stat.st_mtime_ns}:{stat.st_size}'.encode()
        etag = '"'+hashlib.sha256(identity).hexdigest()+'"'
        result = self.representation(stat.st_size, content_type, etag, encoding, modified=stat.st_mtime)
        if result is True:
            return source
        source.close()
        return result

    def list_directory(self, path):
        self.send_error(404)
        return None

    def parse_request(self):
        if not super().parse_request():
            return False
        peer = ipaddress.ip_address(self.client_address[0])
        forwarded = self.headers.get('CF-Connecting-IP') if peer.is_loopback else None
        try:
            client = ipaddress.ip_address(forwarded) if forwarded else peer
        except ValueError:
            with self.error_document(400, 'invalid_client', 'Invalid proxy client address.') as body:
                if self.command != 'HEAD':
                    self.copyfile(body, self.wfile)
            return False
        if client.version == 6 and client.ipv4_mapped:
            client = client.ipv4_mapped
        if client.version == 6:
            client = ipaddress.ip_network(f'{client}/64', strict=False).network_address
        self.client_key = str(client)
        wait = self.server.budgets.charge(self.client_key, requests=1)
        error = None
        if wait:
            error = (429, 'rate_limited', 'Request budget exceeded. Retry later.', wait)
        elif len(self.path) > 8192:
            error = (414, 'uri_too_long', 'Request target exceeds 8192 characters.')
        else:
            try:
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc:
                    raise ValueError
            except ValueError:
                error = (400, 'invalid_uri', 'Use an origin-relative request target.')
        if error is None and self.command not in ('GET', 'HEAD', 'OPTIONS'):
            error = (405, 'method_not_allowed', 'This service is read-only.')
        if error:
            with self.error_document(*error) as body:
                if self.command != 'HEAD':
                    self.copyfile(body, self.wfile)
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Allow', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Accept, Content-Type, If-None-Match')
        self.send_header('Access-Control-Max-Age', '3600')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def end_headers(self):
        path = unquote(getattr(self, 'path', '').split('?', 1)[0])
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers', 'X-JEPA-Snapshot, ETag, Last-Modified, Retry-After')
        cache_control = 'public, max-age=31536000, immutable' if path.startswith('/assets/') else 'no-cache'
        if self.response_status >= 400 or path == '/healthz':
            cache_control = 'no-store'
        self.send_header('Cache-Control', cache_control)
        self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Link', '</llms.txt>; rel="describedby", </openapi.json>; rel="service-desc"; type="application/vnd.oai.openapi+json"')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def log_message(self, *_args):
        pass  # Do not retain arbitrary visitor paths or queries.


class PublicServer(ThreadingHTTPServer):
    """Bound threads, idle socket lifetime, response-cache bytes and client state."""
    max_connections = 32
    request_queue_size = 128

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(self.max_connections)
        self.responses = ResponseCache()
        self.budgets = ClientBudgets()
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        request.settimeout(10)
        if not self.slots.acquire(blocking=False):
            body = b'{"error":{"code":"busy","message":"Server busy. Retry later."}}'
            try:
                request.setblocking(False)  # Refusal must not block the accept loop.
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nRetry-After: 5\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            except OSError:
                pass
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--port', type=int, default=40173)
    args = parser.parse_args()
    server = PublicServer(('127.0.0.1', args.port), partial(Handler, publication=args.publication, api=AgentAPI()))
    server.serve_forever()

if __name__ == '__main__':
    main()

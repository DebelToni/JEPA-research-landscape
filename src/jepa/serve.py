"""Read-only static origin. Publication directory is an atomic release symlink."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, publication, **kwargs):
        self.publication = Path(publication)
        super().__init__(*args, directory=str(self.publication.resolve()), **kwargs)

    def send_head(self):
        path = unquote(urlsplit(self.path).path)
        allowed_roots = {'', 'healthz', 'index.html', 'assets', 'data', 'papers', 'methodology', 'THIRD-PARTY-LICENSES.txt'}
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
                import io
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

    def end_headers(self):
        self.send_header('Cache-Control','public, max-age=31536000, immutable' if urlsplit(self.path).path.startswith('/assets/') else 'no-cache')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def log_message(self, *_args):
        pass  # Do not retain arbitrary visitor paths or queries.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication',type=Path,required=True)
    parser.add_argument('--port',type=int,default=40173)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,publication=args.publication))
    server.serve_forever()

if __name__=='__main__':main()

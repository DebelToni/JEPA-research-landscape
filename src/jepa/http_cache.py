"""Bounded, process-local HTTP representations and client fairness budgets."""
from collections import OrderedDict
from dataclasses import dataclass, field
import gzip
import hashlib
import math
import threading
import time


@dataclass
class Document:
    body: bytes
    content_type: str
    snapshot: str | None = None
    compressed: bytes | None = field(init=False, default=None)

    def __post_init__(self):
        if len(self.body) >= 512:
            compressed = gzip.compress(self.body, compresslevel=3, mtime=0)
            if len(compressed) < len(self.body):
                self.compressed = compressed
        self.etag = '"'+hashlib.sha256(self.body).hexdigest()+'"'
        self.gzip_etag = ('"'+hashlib.sha256(self.compressed).hexdigest()+'"') if self.compressed else None

    @property
    def size(self):
        return len(self.body) + len(self.compressed or b'')


class ResponseCache:
    def __init__(self, max_bytes=32*1024*1024, max_entries=256):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries, self.size, self.lock = OrderedDict(), 0, threading.Lock()

    def get(self, key):
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                self.entries.move_to_end(key)
            return value

    def put(self, key, value):
        if value.size > min(self.max_bytes, 1024*1024):
            return
        with self.lock:
            old = self.entries.pop(key, None)
            if old is not None:
                self.size -= old.size
            self.entries[key] = value
            self.size += value.size
            while self.size > self.max_bytes or len(self.entries) > self.max_entries:
                self.size -= self.entries.popitem(last=False)[1].size


class ClientBudgets:
    """No disk logs. At most 4096 client identities; large responses incur debt."""
    request_rate, request_burst = 20, 80
    byte_rate, byte_burst = 8*1024*1024, 64*1024*1024
    max_clients = 4096

    def __init__(self):
        self.clients, self.lock = OrderedDict(), threading.Lock()

    def charge(self, client, *, requests=0, size=0, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            previous, tokens, budget = self.clients.pop(client, (now, self.request_burst, self.byte_burst))
            elapsed = max(0, now-previous)
            tokens = min(self.request_burst, tokens+elapsed*self.request_rate)
            budget = min(self.byte_burst, budget+elapsed*self.byte_rate)
            # One large download can exceed the burst, but the full size is
            # charged so it cannot be repeated at an unlimited sustained rate.
            wait = max(0, (requests-tokens)/self.request_rate,
                       (min(size, self.byte_burst)-budget)/self.byte_rate if size else 0)
            if not wait:
                tokens -= requests
                budget -= size
            self.clients[client] = (now, tokens, budget)
            if len(self.clients) > self.max_clients:
                self.clients.popitem(last=False)
            return math.ceil(wait)


def wants_gzip(header):
    qualities = {}
    for part in header.lower().split(','):
        coding, *parameters = part.strip().split(';')
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith('q='):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0
        qualities[coding] = quality
    return 0 < qualities.get('gzip', qualities.get('*', 0)) <= 1


def matches_etag(header, etag):
    return any(value.strip().removeprefix('W/') in ('*', etag.removeprefix('W/'))
               for value in header.split(','))

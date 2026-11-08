"""Controlled public acquisition and bounded, non-executing document parsing.

arXiv requires an authoritative abstract page with an explicit version marker.
Other sources require paper['document_url'] (or url) and retain version=None.
PDF parser: pdftotext only, explicitly low fidelity; no model auto-downloads.
No TeX expansion is performed. Source blocks are raw TeX, not rendered tables.
"""
from __future__ import annotations
import gzip
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit, urljoin, quote

MAX_BYTES = 24 * 1024 * 1024
MAX_EXPANDED = 48 * 1024 * 1024
MAX_FILES = 1500
PARSER_VERSION = "bounded-text-v2-html-grid"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def immutable(root: Path, data: bytes) -> str:
    sha = digest(data)
    directory = root / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / sha
    try:
        with path.open("xb") as file:
            file.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("Artifact hash collision or damaged cache")
    return sha


def public_address(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or
            parts.password or parts.port not in (None, 443) or parts.fragment):
        raise ValueError("Only credential-free public HTTPS URLs are allowed")
    addresses = {item[4][0] for item in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Non-public destination rejected")
    return parts.hostname, sorted(addresses)[0]


def fetch(url: str, timeout: float = 120) -> tuple[bytes, str]:
    """Pin validated IP per hop, preserve TLS SNI; bypass ambient proxy/auth."""
    import httpx
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        for _ in range(6):
            host, ip = public_address(url)
            parts = urlsplit(url)
            address = f"[{ip}]" if ":" in ip else ip
            pinned = parts._replace(netloc=address).geturl()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Acquisition deadline")
            with client.stream("GET", pinned, headers={"Host": host, "User-Agent": "JEPA-landscape/1.0", "Accept-Encoding": "identity"},
                               extensions={"sni_hostname": host}, timeout=remaining) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("HTTP compressed transfer rejected; bounded source compression is parsed separately")
                if int(response.headers.get("content-length", "0")) > MAX_BYTES:
                    raise ValueError("Download too large")
                data = bytearray()
                for chunk in response.iter_raw(chunk_size=65536):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Acquisition deadline")
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Download too large")
                return bytes(data), url
    raise ValueError("Too many redirects")


def validate_arxiv_version(data: bytes, arxiv_id: str, version: str | None) -> str:
    """Require content identity, not an unversioned or merely requested URL."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    text = soup.get_text(" ", strip=True)
    ids = set(re.findall(r"arXiv:\s*" + re.escape(arxiv_id) + r"(v\d+)\b", text))
    for meta in soup.find_all("meta", attrs={"name": "citation_pdf_url"}):
        match = re.search(re.escape(arxiv_id) + r"(v\d+)\b", meta.get("content", ""))
        if match:
            ids.add(match[1])
    identity = soup.find("meta", attrs={"name": "citation_arxiv_id"})
    identity_ok = identity and identity.get("content", "").removesuffix(version or "") == arxiv_id
    if identity_ok or re.search(r"arXiv:\s*" + re.escape(arxiv_id) + r"\b", text):
        ids.update(re.findall(r"this version,?\s*(v\d+)", text, re.I))
        for span in soup.select(".arxivid .version, .arxiv-id .version"):
            ids.update(re.findall(r"v\d+", span.get_text()))
    if len(ids) != 1:
        raise ValueError("Missing or conflicting explicit current arXiv version")
    actual = ids.pop()
    if version and actual != version:
        raise ValueError("Requested arXiv version does not match source content")
    return actual


def _block(text: str, sha: str, locator: str, source_url: str, kind="paragraph", page=None) -> dict:
    return {"id": digest((sha + "\0" + locator + "\0" + text).encode()), "text": text,
            "artifact_sha256": sha, "locator": locator, "source_url": source_url,
            "kind": kind, "page": page}


def _html_text(node) -> str:
    """Keep source math, not a guessed conversion of presentation text."""
    from bs4 import BeautifulSoup
    copy = BeautifulSoup(str(node), "html.parser")
    for math in copy.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": re.compile(r"^(application/x-tex|application/x-latex)$", re.I)})
        tex = annotation.get_text(strip=True) if annotation else math.get("alttext")
        # MathML retains fractions, scripts, operators and their nesting when
        # there is no source TeX. Never infer LaTeX from flattened glyphs.
        math.replace_with(" " + ("[source TeX: " + tex + "]" if tex else str(math)) + " ")
    return copy.get_text(" ", strip=True)


def _html_table(node) -> str:
    """Sparse grid: cells retain physical coordinates and original spans.

    Do not repeat spanning values as if the author reported extra results.
    rowspan=0 means the remainder of its HTML row group.
    """
    rows = [row for row in node.find_all("tr") if row.find_parent("table") is node]
    occupied = []
    grid = []
    for r, row in enumerate(rows, 1):
        occupied = [span for span in occupied if span[0] >= r]
        cells = []
        column = 1
        for cell in row.find_all(["th", "td"], recursive=False):
            def span(name):
                raw = str(cell.get(name, "1"))
                return int(raw) if re.fullmatch(r"[0-9]{1,5}", raw) else 1
            colspan = max(1, min(span("colspan"), 1000))
            rowspan = min(span("rowspan"), 65534)
            if rowspan == 0:
                group = row.parent
                height = sum(other.parent is group for other in rows[r - 1:])
            else:
                height = rowspan
            while True:
                overlaps = [end for _, start, end in occupied
                            if start < column + colspan and end > column]
                if not overlaps:
                    break
                column = max(overlaps)
            cells.append({"column": column, "kind": cell.name,
                          "rowspan": rowspan, "colspan": colspan,
                          "text": _html_text(cell)})
            # Only track actual rows: malicious span values must not expand
            # into an arbitrarily large synthetic grid.
            occupied.append((min(r + height - 1, len(rows)), column, column + colspan))
            column += colspan
        grid.append({"row": r, "cells": cells})
    caption = node.find("caption", recursive=False)
    return json.dumps({"caption": _html_text(caption) if caption else None,
                       "rows": grid}, ensure_ascii=False)


def parse_html(data: bytes, sha: str, url: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    for node in soup(["script", "style", "iframe", "object", "embed", "nav", "form"]):
        node.decompose()
    body = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    section = "Document"
    section_anchor = None
    for index, node in enumerate(body.find_all(["h1", "h2", "h3", "h4", "p", "table", "figcaption", "pre", "li", "math"])):
        if node.find_parent(["table", "p", "pre", "li", "figcaption", "math", "h1", "h2", "h3", "h4"]):
            continue
        text = _html_table(node) if node.name == "table" else _html_text(node)
        if not text:
            continue
        ancestors = [node, *node.parents]
        if node.name in ("h1", "h2", "h3", "h4"):
            section = text
            section_anchor = next((item.get("id") for item in ancestors if item.get("id")), None)
        anchor = next((item.get("id") for item in ancestors if item.get("id")), None) or section_anchor
        labels = [section]
        for item in ancestors:
            if item.name in ("figure", "table"):
                caption = item.find(["figcaption", "caption"], recursive=False)
                if caption:
                    label = _html_text(caption)
                    if label and label not in labels:
                        labels.append(label)
        labels.append(f"element #{anchor}" if anchor else "no source anchor")
        # Ordinal remains an internal disambiguator, never a fabricated source ID.
        labels.append(f"{node.name} block {index + 1}")
        source_url = url
        parts = urlsplit(url)
        if anchor and parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password:
            source_url = parts._replace(fragment=quote(anchor, safe="")).geturl()
        blocks.append(_block(text, sha, "; ".join(labels), source_url,
                             "table" if node.name == "table" else "paragraph"))
    return blocks


def source_files(data: bytes) -> dict[str, bytes]:
    """Never extract an archive. Reject links/devices, traversal and expansion bombs."""
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as file:
            data = file.read(MAX_EXPANDED + 1)
        if len(data) > MAX_EXPANDED:
            raise ValueError("Source expansion limit")
    if data.startswith(b"%PDF") or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError("Source response is not TeX")
    files = {}
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    except tarfile.ReadError:
        if b"\\documentclass" not in data and b"\\begin{document}" not in data:
            raise ValueError("Unknown source format")
        return {"source.tex": data}
    with archive:
        for index, member in enumerate(archive):
            path = PurePosixPath(member.name)
            if index >= MAX_FILES or path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ValueError("Unsafe archive path or file count")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Archive links and special files forbidden")
            total += member.size
            if total > MAX_EXPANDED or member.size > MAX_BYTES:
                raise ValueError("Source expansion limit")
            if member.name in files:
                raise ValueError("Duplicate archive member")
            stream = archive.extractfile(member)
            files[member.name] = stream.read(member.size + 1)
    return files


def parse_source(data: bytes, sha: str, url: str) -> list[dict]:
    blocks = []
    for name, value in sorted(source_files(data).items()):
        if not name.endswith((".tex", ".bbl", ".bib")):
            continue
        lines = value.decode("utf-8", errors="replace").splitlines()
        for start in range(0, len(lines), 60):
            text = "\n".join(lines[start:start + 60])
            if text.strip():
                blocks.append(_block(text, sha, f"Source {name}, lines {start+1}–{min(start+60, len(lines))}", url, "raw_tex"))
    return blocks


def _limits():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_EXPANDED, MAX_EXPANDED))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _sandbox(workspace: str):
    """Linux Landlock filesystem allowlist + seccomp network/process isolation.

    Fail closed if either facility is unavailable. No ambient home, auth, project
    data or network is accessible to the parser or its pdftotext child.
    """
    import ctypes
    import errno
    _limits()
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
        raise OSError("Cannot disable privilege escalation")
    abi = libc.syscall(444, 0, 0, 1)  # landlock_create_ruleset VERSION
    if abi < 1:
        raise OSError("Landlock required for document parsers")
    # ABI v1 filesystem rights; REFER (v2), TRUNCATE (v3) and IOCTL_DEV (v5).
    rights = (1 << 13) - 1
    if abi >= 2:
        rights |= 1 << 13
    if abi >= 3:
        rights |= 1 << 14
    if abi >= 5:
        rights |= 1 << 15
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    rules = Ruleset(rights)
    fd = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if fd < 0:
        raise OSError("Cannot create parser filesystem sandbox")
    readonly = (1 << 0) | (1 << 2) | (1 << 3)  # execute, read file, read dir
    allowed = [(path, readonly) for path in {sys.prefix, sys.base_prefix, "/usr", "/lib", "/lib64", "/etc/fonts"}]
    allowed += [(str(Path(__file__).resolve()), (1 << 2)), (workspace, rights),
                ("/dev/null", (1 << 1) | (1 << 2))]
    try:
        for path, access in allowed:
            if not Path(path).exists():
                continue
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(access, parent)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0):
                    raise OSError("Cannot add parser filesystem rule")
            finally:
                os.close(parent)
        if libc.syscall(446, fd, 0):
            raise OSError("Cannot enter parser filesystem sandbox")
    finally:
        os.close(fd)
    # The library was loaded before filesystem confinement.
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7fff0000)  # ALLOW by default
    if not context:
        raise OSError("Cannot create parser syscall sandbox")
    try:
        for name in ("socket", "socketpair", "connect", "ptrace", "process_vm_readv", "process_vm_writev",
                     "mount", "umount2", "unshare", "setns", "bpf", "keyctl", "perf_event_open"):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
                raise OSError("Cannot add parser syscall rule")
        if seccomp.seccomp_load(context):
            raise OSError("Cannot enter parser syscall sandbox")
    finally:
        seccomp.seccomp_release(context)


def isolated_parse(data: bytes, kind: str, sha: str, url: str, timeout: float) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="jepa-parser-") as workspace:
        source = Path(workspace) / "input"
        source.write_bytes(data)
        command = [sys.executable, "-I", str(Path(__file__).resolve()), "--parse", kind, str(source), sha, url]
        with subprocess.Popen(command, cwd=workspace, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                              preexec_fn=lambda: _sandbox(workspace)) as process:
            try:
                stdout, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill the owned group before reaping the child (avoid PGID reuse).
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            if process.returncode:
                raise ValueError("Bounded parser failed")
        if len(stdout) > MAX_EXPANDED:
            raise ValueError("Parser output limit")
        return json.loads(stdout)


def _parse_pdf(path: Path, sha: str, url: str) -> list[dict]:
    executable = shutil.which("pdftotext")
    if not executable:
        raise ValueError("pdftotext is unavailable")
    output = path.with_suffix(".txt")
    subprocess.run([executable, "-layout", "-enc", "UTF-8", str(path), str(output)],
                   check=True, timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if output.stat().st_size > MAX_EXPANDED:
        raise ValueError("PDF output limit")
    blocks = []
    for page, text in enumerate(output.read_text().split("\f"), 1):
        for index in range(0, len(text), 6000):
            piece = text[index:index + 6000]
            if piece.strip():
                blocks.append(_block(piece, sha, f"PDF physical page {page}, text span {index+1}", url, "pdf_text", page))
    return blocks


def acquire_and_parse(paper: dict, root: Path, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    root = Path(root)
    artifacts, warnings, blocks = [], [], []
    selected = None
    acquisition_warnings = []
    arxiv_id = paper.get("arxiv_id")
    version = paper.get("version")
    if arxiv_id:
        match = re.fullmatch(r"((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(v\d+)?", arxiv_id)
        if not match:
            raise ValueError("Invalid arXiv identifier")
        arxiv_id, embedded = match.groups()
        if embedded and version and embedded != version:
            raise ValueError("Conflicting arXiv versions")
        version = embedded or version
        if version and not re.fullmatch(r"v[1-9]\d*", version):
            raise ValueError("Invalid version")
        abstract, _ = fetch(f"https://arxiv.org/abs/{arxiv_id}{version or ''}", max(0.1, deadline-time.monotonic()))
        version = validate_arxiv_version(abstract, arxiv_id, version)
        immutable(root, abstract)
        identifier = arxiv_id + version
        candidates = [("html", f"https://arxiv.org/html/{identifier}"),
                      ("source", f"https://arxiv.org/src/{identifier}"),
                      ("pdf", f"https://arxiv.org/pdf/{identifier}")]
    else:
        candidates = [("auto", paper.get("document_url") or paper["url"])]
    for kind, url in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            warnings.append("acquisition_deadline")
            break
        try:
            data, final_url = fetch(url, min(remaining, 30))
            if arxiv_id and (urlsplit(final_url).hostname not in ("arxiv.org", "export.arxiv.org") or identifier not in final_url):
                raise ValueError("Version-specific transport changed identity")
            if kind == "auto":
                kind = "pdf" if data.startswith(b"%PDF") else "html"
            if kind == "pdf" and not data.startswith(b"%PDF"):
                raise ValueError("Not a PDF response")
            if kind == "html":
                if not re.search(br"<(?:html|article|!doctype)\b", data[:10000], re.I):
                    raise ValueError("Not an HTML response")
                if arxiv_id:
                    validate_arxiv_version(data, arxiv_id, version)
            sha = immutable(root, data)
            parsed = isolated_parse(data, kind, sha, final_url, max(.1, deadline-time.monotonic()))
            if kind == "pdf" and arxiv_id:
                extracted = " ".join(block["text"] for block in parsed)
                actual_versions = set(re.findall(re.escape(arxiv_id) + r"(v\d+)\b", extracted))
                if actual_versions != {version}:
                    raise ValueError("PDF embedded arXiv version is not confirmed")
            artifacts.append({"sha256": sha, "kind": kind, "source_url": final_url,
                              "version": version, "parser": PARSER_VERSION, "bytes": len(data)})
            artifacts[-1]["version_validation"] = (
                "embedded-content" if arxiv_id and kind in ("html", "pdf") else
                "authoritative-abstract-and-versioned-endpoint" if arxiv_id else "not-established")
            artifact_warnings = []
            if kind == "source":
                artifact_warnings.append("raw_tex_not_expanded_include_order_and_tables_unverified")
            if kind == "pdf":
                artifact_warnings.append("pdftotext_low_fidelity_no_table_structure_or_bounding_boxes; Docling_not_configured")
            artifacts[-1]["warnings"] = artifact_warnings
            if not blocks and parsed:
                blocks = parsed
                selected = {"kind": kind, "sha256": sha}
                warnings = artifact_warnings.copy()
        except (ValueError, OSError, subprocess.SubprocessError, TimeoutError) as error:
            acquisition_warnings.append({"kind": kind, "code": type(error).__name__})
        except Exception as error:
            # Network errors may include private request details: persist only type.
            if error.__class__.__module__.startswith("httpx"):
                acquisition_warnings.append({"kind": kind, "code": "network_error"})
            else:
                raise
    if not blocks:
        raise ValueError("No usable document blocks acquired")
    document = {"schema_version": "1.0", "arxiv_id": arxiv_id, "version": version,
                "version_validated": bool(arxiv_id), "blocks": blocks, "artifacts": artifacts,
                "warnings": warnings, "selected_representation": selected,
                "acquisition_warnings": acquisition_warnings, "parser": PARSER_VERSION,
                "scope": "acquired main document; supplementary discovery not established"}
    document["document_sha256"] = immutable(root, json.dumps(document, sort_keys=True).encode())
    return document


if __name__ == "__main__" and len(sys.argv) == 6 and sys.argv[1] == "--parse":
    _, _, kind, filename, sha, url = sys.argv
    path = Path(filename)
    if kind == "pdf":
        result = _parse_pdf(path, sha, url)
    else:
        result = (parse_html if kind == "html" else parse_source)(path.read_bytes(), sha, url)
    sys.stdout.write(json.dumps(result))

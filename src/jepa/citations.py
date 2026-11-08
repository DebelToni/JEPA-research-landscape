"""Citation edges from explicit arXiv links in acquired HTML bibliographies."""
from pathlib import Path
import json
import re
import time
from bs4 import BeautifulSoup
from .sources import record
from .store import arxiv_identity


def bibliography_ids(html: bytes):
    soup=BeautifulSoup(html,'html.parser')
    found=set()
    for item in soup.select('.ltx_bibitem'):
        for link in item.find_all('a',href=True):
            href=link['href']
            match=re.search(r'https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?|[\w.-]+/\d{7}(?:v\d+)?)',href)
            if not match:
                match=re.search(r'https?://doi\.org/10\.48550/arXiv\.(\d{4}\.\d{4,5}(?:v\d+)?)',href,re.I)
            if match:
                found.add(arxiv_identity(match[1])[0])
    return sorted(found)


def ingest_bibliographies(store):
    documents=store.root/'documents';edges=0;new=0
    for path in documents.glob('*.json'):
        doc=json.loads(path.read_text());aid=doc.get('arxiv_id')
        if not aid:continue
        source=store.db.execute("SELECT work_id FROM identities WHERE kind='arxiv' AND value=?",(aid,)).fetchone()
        if not source:continue
        for artifact in doc.get('artifacts',[]):
            if artifact['kind']!='html':continue
            for target in bibliography_ids((store.root/'artifacts'/artifact['sha256']).read_bytes()):
                if target==aid:continue
                existed=store.db.execute("SELECT work_id FROM identities WHERE kind='arxiv' AND value=?",(target,)).fetchone()
                wid=store.ingest(record('arxiv-bibliography',target,arxiv_id=target,url='https://arxiv.org/abs/'+target,
                                       provenance={'metadata_pending':True,'citing_arxiv':aid,'artifact_sha256':artifact['sha256']}))
                new+=not bool(existed)
                with store.db:
                    edges+=store.db.execute('INSERT OR IGNORE INTO citation_edges(source,target,provider,relation) VALUES (?,?,?,?)',
                                           (source[0],wid,'arxiv-bibliography','cites')).rowcount
    return {'new_candidates':new,'new_edges':edges}

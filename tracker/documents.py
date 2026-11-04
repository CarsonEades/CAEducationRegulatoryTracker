"""Download a linked document and pull out its text for keyword matching."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from bs4 import BeautifulSoup

from tracker.http import Fetcher, FetchError


@dataclass
class FetchedDocument:
    sha256: str | None
    text: str | None
    error: str | None = None


def fetch_document(fetcher: Fetcher, url: str) -> FetchedDocument:
    try:
        resp = fetcher.get(url)
    except FetchError as e:
        return FetchedDocument(None, None, str(e))
    return document_from_response(resp, url)


def document_from_response(resp, url: str) -> FetchedDocument:
    data = resp.content
    sha = hashlib.sha256(data).hexdigest()
    ctype = resp.headers.get("content-type", "").lower()
    try:
        text = extract_text(data, ctype, url)
    except Exception as e:  # a malformed file shouldn't stop the run
        return FetchedDocument(sha, None, f"could not read text: {e}")
    if text is None:
        return FetchedDocument(sha, None, f"unsupported file type ({ctype or 'unknown'})")
    return FetchedDocument(sha, text)


def extract_text(data: bytes, content_type: str, url: str) -> str | None:
    """Decide the format from the bytes first, then the server's content type,
    then the URL. Servers often return an HTML error page at a .pdf URL."""
    path = url.lower().split("?", 1)[0]
    if data[:5] == b"%PDF-":
        return pdf_text(data)
    if data[:2] == b"PK" and ("wordprocessingml" in content_type or path.endswith(".docx")):
        return docx_text(data)
    if data[:2] == b"PK" and ("presentationml" in content_type or path.endswith(".pptx")):
        return pptx_text(data)
    if "html" in content_type:
        return html_text(data)
    if content_type.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    if "pdf" in content_type or path.endswith(".pdf"):
        return None  # claims to be a PDF but isn't one
    if path.endswith((".htm", ".html", ".asp", ".aspx")):
        return html_text(data)
    return None


def pdf_text(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def html_text(data: bytes) -> str:
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    root = soup.find("main") or soup.body or soup
    return root.get_text(" ", strip=True)


def docx_text(data: bytes) -> str:
    import re
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def pptx_text(data: bytes) -> str:
    """Text of each slide, in slide order."""
    import re
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1])[1]))
        parts = []
        for n in slides:
            xml = z.read(n).decode("utf-8", errors="replace")
            parts.append(" ".join(re.findall(r"<a:t>([^<]*)</a:t>", xml)))
    return "\n".join(parts)

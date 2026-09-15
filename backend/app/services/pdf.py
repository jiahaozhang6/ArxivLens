import asyncio
import re
import tarfile
from html.parser import HTMLParser
from io import BytesIO

import httpx
from pypdf import PdfReader

from app.config import get_settings

MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_HTML_BYTES = 15 * 1024 * 1024


def _request_headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "User-Agent": f"arxiv-research-digest/0.1 ({settings.arxiv_contact_email})"
    }


async def _download_bytes(url: str, max_bytes: int) -> bytes:
    settings = get_settings()
    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url, headers=_request_headers()) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", 0))
            if content_length > max_bytes:
                raise ValueError(f"Download is larger than the {max_bytes // 1024 // 1024} MB limit")
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Download exceeded the {max_bytes // 1024 // 1024} MB limit")
                chunks.append(chunk)
    return b"".join(chunks)


async def download_pdf(url: str) -> bytes:
    return await _download_bytes(url, MAX_PDF_BYTES)


def _extract_text_sync(pdf_bytes: bytes, max_pages: int, max_chars: int) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    sections: list[str] = []
    total_chars = 0
    for page_number, page in enumerate(reader.pages[:max_pages], start=1):
        text = (page.extract_text() or "").replace(chr(0), "").strip()
        if not text:
            continue
        remaining = max_chars - total_chars
        if remaining <= 0:
            break
        text = text[:remaining]
        sections.append(f"\n--- Page {page_number} ---\n{text}")
        total_chars += len(text)
    return "".join(sections).strip()


async def extract_pdf_text(url: str) -> str:
    settings = get_settings()
    pdf_bytes = await download_pdf(url)
    text = await asyncio.to_thread(
        _extract_text_sync,
        pdf_bytes,
        settings.pdf_max_pages,
        settings.pdf_max_chars,
    )
    if len(text) < 500:
        raise ValueError("PDF text extraction returned too little readable text")
    return text


def _decode_source(value: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="ignore")


def _tex_candidate_score(name: str, text: str) -> tuple[int, int]:
    score = 0
    lowered = name.casefold()
    if "\\documentclass" in text:
        score += 5
    if "\\begin{document}" in text:
        score += 5
    if any(part in lowered for part in ("main", "paper", "manuscript", "article")):
        score += 2
    if any(part in lowered for part in ("appendix", "supp", "template", "example")):
        score -= 3
    return score, len(text)


def _inline_tex_files(main_text: str, files: dict[str, str], depth: int = 0) -> str:
    if depth >= 4:
        return main_text

    def replace(match: re.Match[str]) -> str:
        requested = match.group(1).strip()
        candidates = [requested, f"{requested}.tex"]
        normalized = requested.replace("\\", "/")
        candidates.extend([normalized, f"{normalized}.tex"])
        for candidate in candidates:
            if candidate in files:
                return _inline_tex_files(files[candidate], files, depth + 1)
        return ""

    return re.sub(r"\\(?:input|include)\{([^}]+)\}", replace, main_text)


def _extract_tex_text_sync(source_bytes: bytes, max_chars: int) -> str:
    with tarfile.open(fileobj=BytesIO(source_bytes), mode="r:*") as archive:
        files: dict[str, str] = {}
        for member in archive.getmembers():
            if not member.isfile() or not member.name.casefold().endswith(".tex"):
                continue
            if member.size > 8 * 1024 * 1024:
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                files[member.name.replace("\\", "/")] = _decode_source(handle.read())
    if not files:
        raise ValueError("arXiv source package contains no TeX files")
    main_name = max(files, key=lambda name: _tex_candidate_score(name, files[name]))
    text = _inline_tex_files(files[main_name], files)
    text = re.sub(r"(?m)(?<!\\)%.*$", "", text)
    text = re.sub(r"\\(?:bibliography|bibliographystyle)\{[^}]*\}", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 500:
        raise ValueError("TeX extraction returned too little readable text")
    return text[:max_chars]


async def extract_tex_text(arxiv_id: str, version: int) -> str:
    settings = get_settings()
    suffix = f"v{version}" if version > 0 else ""
    source = await _download_bytes(
        f"https://arxiv.org/e-print/{arxiv_id}{suffix}",
        MAX_SOURCE_BYTES,
    )
    return await asyncio.to_thread(_extract_tex_text_sync, source, settings.pdf_max_chars)


class _ReadableHTMLParser(HTMLParser):
    ignored_tags = {"script", "style", "nav", "header", "footer", "svg", "math"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in self.ignored_tags:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in {"p", "div", "section", "article", "h1", "h2", "h3", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.ignored_tags and self.ignored_depth:
            self.ignored_depth -= 1
        elif not self.ignored_depth and tag in {"p", "div", "section", "article", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def _extract_html_text_sync(html_bytes: bytes, max_chars: int) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(_decode_source(html_bytes))
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 500:
        raise ValueError("HTML extraction returned too little readable text")
    return text[:max_chars]


async def extract_html_text(arxiv_id: str, version: int) -> str:
    settings = get_settings()
    suffix = f"v{version}" if version > 1 else ""
    html_bytes = await _download_bytes(
        f"https://arxiv.org/html/{arxiv_id}{suffix}",
        MAX_HTML_BYTES,
    )
    return await asyncio.to_thread(
        _extract_html_text_sync,
        html_bytes,
        settings.pdf_max_chars,
    )


async def extract_arxiv_text(
    arxiv_id: str,
    version: int,
    pdf_url: str,
) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    for mode, extractor in (
        ("tex", lambda: extract_tex_text(arxiv_id, version)),
        ("html", lambda: extract_html_text(arxiv_id, version)),
        ("pdf", lambda: extract_pdf_text(pdf_url)),
    ):
        try:
            return await extractor(), mode, errors
        except Exception as exc:
            errors.append(f"{mode}: {type(exc).__name__}: {exc}")
    raise ValueError("All full-text extraction methods failed: " + "; ".join(errors))

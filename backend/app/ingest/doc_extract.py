from __future__ import annotations
from datetime import datetime, timezone
from typing import Tuple, Dict, Any
from pypdf import PdfReader
import io

def extract_md_text(md_bytes: bytes) -> Tuple[str, str, Dict[str, Any]]:
    """
    Extract Markdown text and a best-effort title (first heading).
    """
    text = md_bytes.decode("utf-8", errors="ignore").strip()
    title = "Markdown Document"
    for line in text.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip() or title
            break
    meta = {
        "file_type": "md",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    return title, text, meta

def extract_pdf_text(
    pdf_bytes: bytes,
    max_pages: int | None = None,
    max_chars_total: int | None = None,
):
    """
    Extract PDF text per page and return (title, full_text, metadata, pages[])
    pages[] is a list of page texts (1-index mapping handled by caller).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    return _extract_from_reader(
        reader,
        max_pages=max_pages,
        max_chars_total=max_chars_total,
    )

def extract_pdf_text_from_path(
    path: str,
    max_pages: int | None = None,
    max_chars_total: int | None = None,
):
    """
    Extract PDF text from a file path without loading all bytes into memory.
    """
    reader = PdfReader(path, strict=False)
    return _extract_from_reader(
        reader,
        max_pages=max_pages,
        max_chars_total=max_chars_total,
    )

def _extract_from_reader(
    reader: PdfReader,
    max_pages: int | None = None,
    max_chars_total: int | None = None,
):
    pages = []
    total_chars = 0
    page_limit = max_pages
    truncated_pages = False
    for idx, page in enumerate(reader.pages):
        if page_limit is not None and idx >= page_limit:
            truncated_pages = True
            break
        try:
            txt = (page.extract_text() or "").strip()
        except Exception:
            txt = ""
        if txt:
            pages.append(txt)
            total_chars += len(txt)
        else:
            pages.append("")
        if max_chars_total is not None and total_chars >= max_chars_total:
            break
    full_text = "\n\n".join([p for p in pages if p])

    meta = {
        "file_type": "pdf",
        "page_count": len(pages),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    if truncated_pages:
        meta["truncated_pages"] = True
        meta["page_limit"] = max_pages
    if max_chars_total is not None and total_chars >= max_chars_total:
        meta["truncated_chars"] = True
        meta["char_limit"] = max_chars_total
    title = "PDF Document"
    return title, full_text, meta, pages

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

def extract_pdf_text(pdf_bytes: bytes):
    """
    Extract PDF text per page and return (title, full_text, metadata, pages[])
    pages[] is a list of page texts (1-index mapping handled by caller).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return _extract_from_reader(reader)

def extract_pdf_text_from_path(path: str):
    """
    Extract PDF text from a file path without loading all bytes into memory.
    """
    reader = PdfReader(path)
    return _extract_from_reader(reader)

def _extract_from_reader(reader: PdfReader):
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    full_text = "\n\n".join([p for p in pages if p])

    meta = {
        "file_type": "pdf",
        "page_count": len(reader.pages),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    title = "PDF Document"
    return title, full_text, meta, pages

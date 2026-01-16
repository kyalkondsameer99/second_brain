from __future__ import annotations
from urllib.parse import urlparse
from datetime import datetime, timezone
import time
import requests
import trafilatura

def extract_web_text(url: str):
    """
    Fetch and extract main content from a URL.
    Returns: (title, text, metadata dict)
    """
    max_bytes = 1_000_000
    max_seconds = 15
    resp = requests.get(
        url,
        timeout=(10, 20),
        headers={"User-Agent": "Mozilla/5.0"},
        stream=True,
    )
    if not resp.ok:
        raise ValueError("Failed to fetch URL content")
    buf = bytearray()
    started = time.time()
    for chunk in resp.iter_content(chunk_size=8192):
        if not chunk:
            continue
        buf.extend(chunk)
        if len(buf) >= max_bytes:
            break
        if time.time() - started > max_seconds:
            break
    resp.close()
    downloaded = buf.decode(resp.encoding or "utf-8", errors="ignore")

    text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    if not text:
        raise ValueError("Failed to extract readable text from URL")

    meta_obj = trafilatura.extract_metadata(downloaded)
    title = getattr(meta_obj, "title", None) if meta_obj else None
    domain = urlparse(url).netloc

    meta = {
        "domain": domain,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if len(buf) >= max_bytes:
        meta["truncated"] = True
        meta["original_byte_count"] = len(buf)
    if time.time() - started > max_seconds:
        meta["time_limited"] = True
    if meta_obj:
        if getattr(meta_obj, "author", None):
            meta["author"] = meta_obj.author
        if getattr(meta_obj, "date", None):
            meta["published_date"] = meta_obj.date

    return (title or url), text, meta

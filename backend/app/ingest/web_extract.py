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
    downloaded = None
    fetch_error = None
    started = time.time()

    try:
        resp = requests.get(
            url,
            timeout=(10, 20),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            stream=True,
            allow_redirects=True,
        )
        if not resp.ok:
            fetch_error = f"status_{resp.status_code}"
        else:
            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
                if time.time() - started > max_seconds:
                    break
            downloaded = buf.decode(resp.encoding or "utf-8", errors="ignore")
        resp.close()
    except Exception as e:
        fetch_error = str(e)

    if not downloaded:
        try:
            downloaded = trafilatura.fetch_url(url)
        except Exception as e:
            fetch_error = fetch_error or str(e)

    if not downloaded:
        # Last-resort fallback using Jina AI reader (handles bot protections)
        try:
            jina_url = f"https://r.jina.ai/http://{url.replace('https://', '').replace('http://', '')}"
            resp = requests.get(jina_url, timeout=(10, 20))
            if resp.ok and resp.text:
                downloaded = resp.text
        except Exception as e:
            fetch_error = fetch_error or str(e)

    text = None
    if downloaded:
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    if not text:
        raise ValueError("Failed to fetch URL content")

    meta_obj = trafilatura.extract_metadata(downloaded)
    title = getattr(meta_obj, "title", None) if meta_obj else None
    domain = urlparse(url).netloc

    meta = {
        "domain": domain,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if downloaded and len(downloaded.encode("utf-8", errors="ignore")) >= max_bytes:
        meta["truncated"] = True
        meta["original_byte_count"] = len(downloaded)
    if time.time() - started > max_seconds:
        meta["time_limited"] = True
    if fetch_error:
        meta["fetch_error"] = fetch_error
    if meta_obj:
        if getattr(meta_obj, "author", None):
            meta["author"] = meta_obj.author
        if getattr(meta_obj, "date", None):
            meta["published_date"] = meta_obj.date

    return (title or url), text, meta

from __future__ import annotations

def chunk_text(text: str, max_chars: int = 2000, overlap: int = 200) -> list[str]:
    """
    Deterministic chunker:
    - splits by paragraph first
    - enforces max_chars by sliding window with overlap
    """
    text = (text or "").strip()
    if not text:
        return []

    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""

    def flush(b: str):
        b = b.strip()
        if b:
            chunks.append(b)

    for p in paras:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}".strip()
        else:
            flush(buf)
            buf = p
    flush(buf)

    # enforce max size
    final: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
        else:
            i = 0
            while i < len(c):
                j = min(len(c), i + max_chars)
                final.append(c[i:j].strip())
                i = max(0, j - overlap)

    return [c for c in (x.strip() for x in final) if c]

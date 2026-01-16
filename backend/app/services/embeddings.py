from __future__ import annotations
from typing import List
from openai import OpenAI
from app.config import settings

_client = OpenAI(api_key=settings.OPENAI_API_KEY)

def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a list of texts using OpenAI embeddings.
    """
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    resp = _client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
        timeout=60,
    )
    return [d.embedding for d in resp.data]

def to_pgvector_literal(vec: List[float]) -> str:
    """
    Convert python list[float] -> pgvector literal string: '[0.1,0.2,...]'
    This avoids driver adaptation issues.
    """
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

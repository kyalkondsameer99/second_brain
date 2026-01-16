from __future__ import annotations
from typing import List, Dict, Tuple
from openai import OpenAI
from app.config import settings

_client = OpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """You are a Second Brain assistant.

Rules:
- Answer ONLY using the provided evidence blocks.
- If evidence is insufficient, say so and specify what is missing.
- Be concise, accurate, and grounded.
- Do not invent citations or facts not present in evidence.
"""

def answer_question(query: str, evidence_blocks: List[Dict]) -> Tuple[str, str]:
    """
    RAG synthesis: query + curated evidence -> answer with [1],[2] citations.
    """
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    evidence_text = "\n\n".join(
        f"[{i+1}] {b['text']}\nCITATION: {b['citation']}"
        for i, b in enumerate(evidence_blocks)
    )

    user_prompt = f"""QUESTION:
{query}

EVIDENCE BLOCKS:
{evidence_text}

Write the answer. When you make a claim, add citation markers like [1], [2] referencing the evidence blocks.
"""

    resp = _client.chat.completions.create(
        model=settings.CHAT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        timeout=120,
    )
    return resp.choices[0].message.content, settings.CHAT_MODEL

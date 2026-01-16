from __future__ import annotations
from typing import Optional, List, Dict
from sqlalchemy import text
from app.services.embeddings import embed_texts, to_pgvector_literal

def _norm(vals: List[float]) -> List[float]:
    if not vals:
        return []
    mn, mx = min(vals), max(vals)
    if mx - mn < 1e-12:
        return [1.0 for _ in vals]
    return [(v - mn) / (mx - mn) for v in vals]

def _format_citation(r) -> str:
    title = r.get("title") or "Untitled"
    src = r.get("source_uri") or ""
    ptype = r.get("pointer_type") or ""
    ps = r.get("pointer_start") or ""
    pe = r.get("pointer_end") or ""

    if ptype == "AUDIO_MS":
        return f"{title} ({src}) [audio {ps}-{pe}ms]"
    if ptype == "PDF_PAGE":
        return f"{title} ({src}) [pages {ps}-{pe}]"
    if ptype == "URL":
        return f"{title} ({src})"
    if ptype == "NOTE_RANGE":
        return f"{title} [note]"
    if ptype == "IMAGE_REF":
        return f"{title} [image]"
    return f"{title} ({src})"

def hybrid_retrieve(
    db,
    user_id: str,
    query_text: str,
    item_id: Optional[str] = None,
    time_start: Optional[str] = None,  # ISO string expected
    time_end: Optional[str] = None,
    top_k: int = 8,
) -> List[Dict]:
    """
    Hybrid retrieval:
    1) vector similarity (pgvector cosine distance)
    2) keyword search (FTS)
    3) optional time filter/boost
    4) fuse + diversity cap per item
    Returns evidence blocks with citations.
    """
    q_vec_lit = None
    try:
        q_vec = embed_texts([query_text])[0]
        q_vec_lit = to_pgvector_literal(q_vec)
    except Exception:
        q_vec_lit = None

    # Optional time filter:
    # - Prefer chunk_time_start; fallback to knowledge_items.source_time
    item_clause = ""
    time_clause = ""
    params = {
        "user_id": user_id,
        "q": query_text,
        "k": top_k * 4,
    }
    if item_id:
        item_clause = "AND c.item_id = :item_id"
        params["item_id"] = item_id
    if q_vec_lit is not None:
        params["q_emb"] = q_vec_lit
    if time_start and time_end:
        time_clause = """
        AND (
          (c.chunk_time_start IS NOT NULL AND c.chunk_time_start BETWEEN :time_start AND :time_end)
          OR
          (c.chunk_time_start IS NULL AND ki.source_time IS NOT NULL AND ki.source_time BETWEEN :time_start AND :time_end)
        )
        """
        params["time_start"] = time_start
        params["time_end"] = time_end

    vec_rows = []
    if q_vec_lit is not None:
        # Vector candidates (cosine distance operator: <=> with vector_cosine_ops index)
        vec_sql = text(f"""
          SELECT
            c.id as chunk_id, c.item_id, c.text,
            (1 - (c.embedding <=> CAST(:q_emb AS vector))) as sim,
            c.pointer_type, c.pointer_start, c.pointer_end,
            c.chunk_time_start, c.chunk_time_end,
            ki.title, ki.source_uri, ki.source_type
          FROM chunks c
          JOIN knowledge_items ki ON ki.id = c.item_id
          WHERE c.user_id = :user_id
            AND c.embedding IS NOT NULL
            {item_clause}
            {time_clause}
          ORDER BY (c.embedding <=> CAST(:q_emb AS vector)) ASC
          LIMIT :k
        """)
        vec_rows = db.execute(vec_sql, params).mappings().all()

    # Keyword candidates
    kw_sql = text(f"""
      SELECT
        c.id as chunk_id, c.item_id, c.text,
        ts_rank_cd(c.tsv, plainto_tsquery('english', :q)) as rank,
        c.pointer_type, c.pointer_start, c.pointer_end,
        c.chunk_time_start, c.chunk_time_end,
        ki.title, ki.source_uri, ki.source_type
      FROM chunks c
      JOIN knowledge_items ki ON ki.id = c.item_id
      WHERE c.user_id = :user_id
        AND c.tsv @@ plainto_tsquery('english', :q)
        {item_clause}
        {time_clause}
      ORDER BY rank DESC
      LIMIT :k
    """)
    kw_rows = db.execute(kw_sql, params).mappings().all()

    if not vec_rows and not kw_rows and item_id:
        fallback_sql = text("""
          SELECT
            c.id as chunk_id, c.item_id, c.text,
            0.0 as sim,
            0.0 as rank,
            c.pointer_type, c.pointer_start, c.pointer_end,
            c.chunk_time_start, c.chunk_time_end,
            ki.title, ki.source_uri, ki.source_type
          FROM chunks c
          JOIN knowledge_items ki ON ki.id = c.item_id
          WHERE c.user_id = :user_id AND c.item_id = :item_id
          ORDER BY c.chunk_index ASC
          LIMIT :k
        """)
        kw_rows = db.execute(fallback_sql, params).mappings().all()

    vec_sims = _norm([float(r["sim"]) for r in vec_rows])
    kw_ranks = _norm([float(r["rank"]) for r in kw_rows])

    merged = {}
    for i, r in enumerate(vec_rows):
        cid = str(r["chunk_id"])
        merged.setdefault(cid, {"row": r, "sem": 0.0, "kw": 0.0})
        merged[cid]["sem"] = max(merged[cid]["sem"], vec_sims[i])

    for i, r in enumerate(kw_rows):
        cid = str(r["chunk_id"])
        merged.setdefault(cid, {"row": r, "sem": 0.0, "kw": 0.0})
        merged[cid]["kw"] = max(merged[cid]["kw"], kw_ranks[i])

    scored = []
    for cid, obj in merged.items():
        # Weights are simple and stable; adjust if needed.
        score = 0.6 * obj["sem"] + 0.4 * obj["kw"]
        scored.append((score, obj["row"]))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Diversity: cap chunks per item
    per_item = {}
    evidence = []
    for score, r in scored:
        item_id = str(r["item_id"])
        per_item[item_id] = per_item.get(item_id, 0) + 1
        if per_item[item_id] > 3:
            continue

        evidence.append({
            "chunk_id": str(r["chunk_id"]),
            "item_id": item_id,
            "text": r["text"],
            "citation": _format_citation(r),
            "score": float(score),
        })
        if len(evidence) >= top_k:
            break

    return evidence

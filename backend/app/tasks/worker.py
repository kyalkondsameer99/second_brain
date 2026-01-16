from __future__ import annotations
import os
import json
import hashlib
import concurrent.futures
from datetime import datetime, timezone
from urllib.parse import urlparse
from sqlalchemy import text
from openai import OpenAI

from app.config import settings
from app.db import SessionLocal
from app.ingest.chunking import chunk_text
from app.ingest.web_extract import extract_web_text
from app.ingest.doc_extract import extract_pdf_text, extract_pdf_text_from_path, extract_md_text
from app.services.embeddings import embed_texts, to_pgvector_literal
from celery.exceptions import SoftTimeLimitExceeded
from app.tasks.celery_app import celery_app

_client = OpenAI(api_key=settings.OPENAI_API_KEY)

UPLOAD_DIR = "/app/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def _now_utc():
    return datetime.now(timezone.utc)

def _embed_or_none(texts: list[str]) -> list[list[float] | None]:
    if not settings.OPENAI_API_KEY:
        return [None for _ in texts]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(embed_texts, texts)
            return future.result(timeout=70)
    except Exception:
        return [None for _ in texts]

def _run_with_timeout(fn, timeout_s: int, *args, **kwargs):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout_s)

def _simple_chunks(text: str, max_chars: int = 2000) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [text[i:i + max_chars].strip() for i in range(0, len(text), max_chars) if text[i:i + max_chars].strip()]

def _set_status(db, item_id: str, status: str, err: str | None = None):
    db.rollback()
    db.execute(
        text("UPDATE knowledge_items SET status=:s, error_message=:e WHERE id=:id"),
        {"s": status, "e": err, "id": item_id},
    )
    db.commit()

def _merge_metadata(db, item_id: str, patch: dict):
    db.execute(
        text("UPDATE knowledge_items SET metadata = metadata || CAST(:p AS jsonb) WHERE id=:id"),
        {"p": json.dumps(patch), "id": item_id},
    )
    db.commit()

def _insert_chunks(
    db,
    user_id: str,
    item_id: str,
    chunk_texts: list[str],
    pointer_type: str,
    pointer_start: str,
    pointer_end: str,
    chunk_time_start=None,
    chunk_time_end=None,
):
    """
    Insert chunks and embeddings. Uses pgvector literal casting to avoid adapter issues.
    """
    if not chunk_texts:
        return

    embeddings = _embed_or_none(chunk_texts)
    for idx, (txt, emb) in enumerate(zip(chunk_texts, embeddings)):
        chash = hashlib.sha256(txt.encode("utf-8")).hexdigest()
        emb_lit = to_pgvector_literal(emb) if emb is not None else None

        db.execute(text("""
          INSERT INTO chunks (
            id, user_id, item_id, chunk_index, text, embedding, chunk_hash,
            pointer_type, pointer_start, pointer_end,
            chunk_time_start, chunk_time_end,
            created_at
          ) VALUES (
            gen_random_uuid(), :user_id, :item_id, :chunk_index, :text, CAST(:embedding AS vector), :chunk_hash,
            :pointer_type, :pointer_start, :pointer_end,
            :cts, :cte,
            :created_at
          )
          ON CONFLICT (item_id, chunk_index) DO NOTHING
        """), {
            "user_id": user_id,
            "item_id": item_id,
            "chunk_index": idx,
            "text": txt,
            "embedding": emb_lit,
            "chunk_hash": chash,
            "pointer_type": pointer_type,
            "pointer_start": pointer_start,
            "pointer_end": pointer_end,
            "cts": chunk_time_start,
            "cte": chunk_time_end,
            "created_at": _now_utc(),
        })
    db.commit()

def _insert_chunks_no_embed(
    db,
    user_id: str,
    item_id: str,
    chunk_texts: list[str],
    pointer_type: str,
    pointer_start: str,
    pointer_end: str,
    chunk_time_start=None,
    chunk_time_end=None,
):
    if not chunk_texts:
        return

    for idx, txt in enumerate(chunk_texts):
        chash = hashlib.sha256(txt.encode("utf-8")).hexdigest()
        db.execute(text("""
          INSERT INTO chunks (
            id, user_id, item_id, chunk_index, text, embedding, chunk_hash,
            pointer_type, pointer_start, pointer_end,
            chunk_time_start, chunk_time_end,
            created_at
          ) VALUES (
            gen_random_uuid(), :user_id, :item_id, :chunk_index, :text, NULL, :chunk_hash,
            :pointer_type, :pointer_start, :pointer_end,
            :cts, :cte,
            :created_at
          )
          ON CONFLICT (item_id, chunk_index) DO NOTHING
        """), {
            "user_id": user_id,
            "item_id": item_id,
            "chunk_index": idx,
            "text": txt,
            "chunk_hash": chash,
            "pointer_type": pointer_type,
            "pointer_start": pointer_start,
            "pointer_end": pointer_end,
            "cts": chunk_time_start,
            "cte": chunk_time_end,
            "created_at": _now_utc(),
        })
    db.commit()

@celery_app.task(name="ingest_web", soft_time_limit=50, time_limit=60)
def ingest_web(item_id: str, user_id: str, url: str):
    db = SessionLocal()
    try:
        _set_status(db, item_id, "PROCESSING", None)

        try:
            title, text_content, meta = _run_with_timeout(extract_web_text, 40, url)
        except SoftTimeLimitExceeded:
            title, text_content, meta = url, "", {"extract_error": "soft_time_limit"}
        except Exception as e:
            title, text_content, meta = url, "", {"extract_error": str(e)}
        domain = urlparse(url).netloc

        derived_key = None
        if text_content:
            derived_key = os.path.join(UPLOAD_DIR, f"{item_id}_web.txt")
            with open(derived_key, "w", encoding="utf-8") as f:
                f.write(text_content)

        # Update item
        db.execute(text("""
          UPDATE knowledge_items
          SET title=:title, source_uri=:url, source_type='WEB', derived_text_object_key=:derived_key
          WHERE id=:id
        """), {"title": title, "url": url, "derived_key": derived_key, "id": item_id})
        db.commit()

        _merge_metadata(db, item_id, {"domain": domain, **meta})

        try:
            chunks = _run_with_timeout(_simple_chunks, 10, text_content)
        except SoftTimeLimitExceeded:
            chunks = _simple_chunks(text_content)
        except Exception:
            chunks = _simple_chunks(text_content)
        if len(chunks) > 50:
            chunks = chunks[:50]
        # Full extraction, but avoid embedding API to keep web ingest stable.
        _insert_chunks_no_embed(db, user_id, item_id, chunks, "URL", url, "")

        _set_status(db, item_id, "READY", None)

    except Exception as e:
        _set_status(db, item_id, "FAILED", str(e))
    finally:
        db.close()

@celery_app.task(name="ingest_audio", soft_time_limit=180, time_limit=210)
def ingest_audio(item_id: str, user_id: str, file_path: str):
    db = SessionLocal()
    try:
        _set_status(db, item_id, "PROCESSING", None)

        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")

        max_bytes = 25 * 1024 * 1024
        try:
            if os.path.getsize(file_path) > max_bytes:
                raise ValueError("audio_file_too_large_max_25mb")
        except OSError:
            raise ValueError("audio_file_missing")

        with open(file_path, "rb") as f:
            tr = _client.audio.transcriptions.create(
                model=settings.TRANSCRIBE_MODEL,
                file=f,
                timeout=120,
            )
        transcript = (tr.text or "").strip()
        if not transcript:
            raise ValueError("Empty transcript")

        max_chars = 60000
        truncated = len(transcript) > max_chars
        if truncated:
            transcript = transcript[:max_chars]

        derived_key = None
        if transcript:
            derived_key = os.path.join(UPLOAD_DIR, f"{item_id}_audio.txt")
            with open(derived_key, "w", encoding="utf-8") as f:
                f.write(transcript)

        if derived_key:
            db.execute(
                text("UPDATE knowledge_items SET derived_text_object_key=:k WHERE id=:id"),
                {"k": derived_key, "id": item_id},
            )
            db.commit()

        _merge_metadata(
            db,
            item_id,
            {
                "transcript_preview": transcript[:800],
                "transcript_truncated": truncated,
            },
        )

        try:
            chunks = _run_with_timeout(chunk_text, 20, transcript)
        except SoftTimeLimitExceeded:
            chunks = _simple_chunks(transcript)
        except Exception:
            chunks = _simple_chunks(transcript)
        if len(chunks) > 50:
            chunks = chunks[:50]

        # For a minimal implementation, we store AUDIO_MS pointers as 0-0.
        # Keep audio ingestion stable by skipping embeddings here; keyword search still works.
        _insert_chunks_no_embed(db, user_id, item_id, chunks, "AUDIO_MS", "0", "0")

        _set_status(db, item_id, "READY", None)
    except SoftTimeLimitExceeded:
        _set_status(db, item_id, "FAILED", "audio_ingest_timeout")
    except Exception as e:
        _set_status(db, item_id, "FAILED", str(e))
    finally:
        db.close()

@celery_app.task(name="ingest_document", soft_time_limit=120, time_limit=150)
def ingest_document(item_id: str, user_id: str, file_path: str, doc_type: str):
    """
    doc_type: 'pdf' or 'md'
    """
    db = SessionLocal()
    try:
        _set_status(db, item_id, "PROCESSING", None)

        max_bytes = 25 * 1024 * 1024
        try:
            if os.path.getsize(file_path) > max_bytes:
                raise ValueError("document_file_too_large_max_25mb")
        except OSError:
            raise ValueError("document_file_missing")

        if doc_type == "pdf":
            title, full_text, meta, pages = extract_pdf_text_from_path(file_path)

            db.execute(text("""
              UPDATE knowledge_items
              SET title=:title, source_uri=:uri, source_type='PDF'
              WHERE id=:id
            """), {"title": title, "uri": os.path.basename(file_path), "id": item_id})
            db.commit()
            _merge_metadata(db, item_id, meta)

            # Chunk per page first (keeps citation simple and trustworthy)
            # Each page may produce multiple chunks via chunk_text.
            chunk_idx = 0
            max_total_chunks = 80
            for i, page_text in enumerate(pages, start=1):
                if not page_text.strip():
                    continue
                page_chunks = chunk_text(page_text)
                if not page_chunks:
                    continue
                # Insert page chunks; pointer is page i. Skip embeddings for stability.
                for txt in page_chunks:
                    chash = hashlib.sha256(txt.encode("utf-8")).hexdigest()
                    db.execute(text("""
                      INSERT INTO chunks (
                        id, user_id, item_id, chunk_index, text, embedding, chunk_hash,
                        pointer_type, pointer_start, pointer_end, created_at
                      ) VALUES (
                        gen_random_uuid(), :user_id, :item_id, :chunk_index, :text, NULL, :chunk_hash,
                        'PDF_PAGE', :ps, :pe, :created_at
                      )
                      ON CONFLICT (item_id, chunk_index) DO NOTHING
                    """), {
                        "user_id": user_id,
                        "item_id": item_id,
                        "chunk_index": chunk_idx,
                        "text": txt,
                        "chunk_hash": chash,
                        "ps": str(i),
                        "pe": str(i),
                        "created_at": _now_utc(),
                    })
                    chunk_idx += 1
                    if chunk_idx >= max_total_chunks:
                        break
                db.commit()
                if chunk_idx >= max_total_chunks:
                    break

        elif doc_type == "md":
            with open(file_path, "rb") as f:
                data = f.read()
            title, md_text, meta = extract_md_text(data)

            db.execute(text("""
              UPDATE knowledge_items
              SET title=:title, source_uri=:uri, source_type='MARKDOWN'
              WHERE id=:id
            """), {"title": title, "uri": os.path.basename(file_path), "id": item_id})
            db.commit()
            _merge_metadata(db, item_id, meta)

            md_chunks = chunk_text(md_text)
            if len(md_chunks) > 80:
                md_chunks = md_chunks[:80]
            _insert_chunks_no_embed(db, user_id, item_id, md_chunks, "NOTE_RANGE", "0", "0")

        else:
            raise ValueError("Unsupported doc_type; must be 'pdf' or 'md'")

        _set_status(db, item_id, "READY", None)
    except SoftTimeLimitExceeded:
        _set_status(db, item_id, "FAILED", "document_ingest_timeout")
    except Exception as e:
        _set_status(db, item_id, "FAILED", str(e))
    finally:
        db.close()

@celery_app.task(name="ingest_image_metadata")
def ingest_image_metadata(item_id: str, user_id: str, raw_object_key: str, description_text: str, tags_csv: str):
    """
    Minimal image support:
    - store image file (already uploaded)
    - index associated text (description + tags) as a chunk so it becomes searchable
    """
    db = SessionLocal()
    try:
        _set_status(db, item_id, "PROCESSING", None)

        tags = [t.strip() for t in (tags_csv or "").split(",") if t.strip()]
        payload = {
            "tags": tags,
            "description_text": description_text or "",
        }
        _merge_metadata(db, item_id, payload)

        searchable = "\n".join([
            f"Description: {description_text or ''}".strip(),
            f"Tags: {', '.join(tags)}" if tags else "Tags:",
        ]).strip()

        chunks = chunk_text(searchable) or [searchable]
        _insert_chunks(db, user_id, item_id, chunks, "IMAGE_REF", raw_object_key, "")

        _set_status(db, item_id, "READY", None)
    except Exception as e:
        _set_status(db, item_id, "FAILED", str(e))
    finally:
        db.close()

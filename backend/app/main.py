from __future__ import annotations
import os
import uuid
from fastapi import FastAPI, UploadFile, File, Form
from fastapi import Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.retrieval import hybrid_retrieve
from app.services.llm import answer_question
from app.tasks.worker import ingest_audio, ingest_web, ingest_document, ingest_image_metadata

UPLOAD_DIR = "/app/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_DOC_BYTES = 25 * 1024 * 1024

# For a prototype: fixed demo user
DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"

app = FastAPI(title="Second Brain API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class WebIngestReq(BaseModel):
    url: str

class ChatReq(BaseModel):
    query: str
    item_id: str | None = None
    time_start: str | None = None  # ISO timestamps optional
    time_end: str | None = None

@app.get("/health")
def health():
    return {"ok": True}

def _create_item(db: Session, source_type: str, title: str | None, source_uri: str | None, raw_object_key: str | None):
    item_id = str(uuid.uuid4())
    db.execute(text("""
      INSERT INTO knowledge_items (id, user_id, source_type, title, source_uri, raw_object_key, status)
      VALUES (:id, :user_id, :stype, :title, :uri, :raw_key, 'PENDING')
    """), {"id": item_id, "user_id": DEMO_USER_ID, "stype": source_type, "title": title, "uri": source_uri, "raw_key": raw_object_key})
    db.commit()
    return item_id

@app.post("/ingest/web")
def ingest_web_endpoint(req: WebIngestReq, db: Session = Depends(get_db)):
    item_id = _create_item(db, "WEB", None, req.url, None)
    ingest_web.delay(item_id=item_id, user_id=DEMO_USER_ID, url=req.url)
    return {"item_id": item_id, "status": "PENDING"}

@app.post("/ingest/audio")
async def ingest_audio_endpoint(file: UploadFile = File(...), db: Session = Depends(get_db)):
    item_id = str(uuid.uuid4())
    fname = f"{item_id}_{file.filename}"
    fpath = os.path.join(UPLOAD_DIR, fname)

    total = 0
    with open(fpath, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                out.close()
                os.remove(fpath)
                return {"error": "audio_too_large", "max_mb": MAX_AUDIO_BYTES // (1024 * 1024)}
            out.write(chunk)

    db.execute(text("""
      INSERT INTO knowledge_items (id, user_id, source_type, title, source_uri, raw_object_key, status)
      VALUES (:id, :user_id, 'AUDIO', :title, :uri, :raw_key, 'PENDING')
    """), {"id": item_id, "user_id": DEMO_USER_ID, "title": file.filename, "uri": file.filename, "raw_key": fpath})
    db.commit()

    ingest_audio.delay(item_id=item_id, user_id=DEMO_USER_ID, file_path=fpath)
    return {"item_id": item_id, "status": "PENDING"}

@app.post("/ingest/document")
async def ingest_document_endpoint(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ext = (file.filename or "").lower().split(".")[-1]
    if ext not in ("pdf", "md"):
        return {"error": "unsupported_file_type", "supported": ["pdf", "md"]}

    item_id = str(uuid.uuid4())
    fname = f"{item_id}_{file.filename}"
    fpath = os.path.join(UPLOAD_DIR, fname)

    total = 0
    with open(fpath, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOC_BYTES:
                out.close()
                os.remove(fpath)
                return {"error": "document_too_large", "max_mb": MAX_DOC_BYTES // (1024 * 1024)}
            out.write(chunk)

    # Insert item as PENDING
    stype = "PDF" if ext == "pdf" else "MARKDOWN"
    db.execute(text("""
      INSERT INTO knowledge_items (id, user_id, source_type, title, source_uri, raw_object_key, status)
      VALUES (:id, :user_id, :stype, :title, :uri, :raw_key, 'PENDING')
    """), {"id": item_id, "user_id": DEMO_USER_ID, "stype": stype, "title": file.filename, "uri": file.filename, "raw_key": fpath})
    db.commit()

    ingest_document.delay(item_id=item_id, user_id=DEMO_USER_ID, file_path=fpath, doc_type=ext)
    return {"item_id": item_id, "status": "PENDING"}

@app.post("/ingest/image")
async def ingest_image_endpoint(
    file: UploadFile = File(...),
    title: str = Form("Image"),
    tags: str = Form(""),
    description_text: str = Form(""),
    db: Session = Depends(get_db),
):
    ext = (file.filename or "").lower().split(".")[-1]
    if ext not in ("png", "jpg", "jpeg", "webp"):
        return {"error": "unsupported_image_type", "supported": ["png", "jpg", "jpeg", "webp"]}

    item_id = str(uuid.uuid4())
    fname = f"{item_id}_{file.filename}"
    fpath = os.path.join(UPLOAD_DIR, fname)

    with open(fpath, "wb") as out:
        out.write(await file.read())

    db.execute(text("""
      INSERT INTO knowledge_items (id, user_id, source_type, title, source_uri, raw_object_key, status)
      VALUES (:id, :user_id, 'IMAGE', :title, :uri, :raw_key, 'PENDING')
    """), {"id": item_id, "user_id": DEMO_USER_ID, "title": title, "uri": file.filename, "raw_key": fpath})
    db.commit()

    ingest_image_metadata.delay(
        item_id=item_id,
        user_id=DEMO_USER_ID,
        raw_object_key=fpath,
        description_text=description_text,
        tags_csv=tags,
    )
    return {"item_id": item_id, "status": "PENDING"}

@app.get("/items/{item_id}")
def get_item(item_id: str, db: Session = Depends(get_db)):
    row = db.execute(text("""
      SELECT id, source_type, title, source_uri, status, ingested_at, source_time, metadata, error_message
      FROM knowledge_items
      WHERE id=:id AND user_id=:user_id
    """), {"id": item_id, "user_id": DEMO_USER_ID}).mappings().first()
    if not row:
        return {"error": "not_found"}
    return dict(row)

@app.post("/chat")
def chat(req: ChatReq, db: Session = Depends(get_db)):
    item_id = req.item_id
    if not item_id:
        row = db.execute(text("""
          SELECT id
          FROM knowledge_items
          WHERE user_id=:user_id AND status='READY'
          ORDER BY ingested_at DESC
          LIMIT 1
        """), {"user_id": DEMO_USER_ID}).mappings().first()
        if row:
            item_id = str(row["id"])

    evidence = hybrid_retrieve(
        db=db,
        user_id=DEMO_USER_ID,
        query_text=req.query,
        item_id=item_id,
        time_start=req.time_start,
        time_end=req.time_end,
        top_k=8,
    )
    answer, model = answer_question(req.query, evidence)
    citations = [{"citation": e["citation"], "chunk_id": e["chunk_id"], "item_id": e["item_id"]} for e in evidence]
    return {"answer": answer, "citations": citations, "model": model}

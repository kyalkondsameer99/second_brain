# Second Brain (Multi-Modal + Time-Aware RAG)

## What it does
- Ingests: Audio (transcription), Web URLs, Documents (PDF/Markdown), Images (searchable via metadata text).
- Chunks and indexes content into Postgres:
  - Keyword search (Postgres Full Text Search)
  - Semantic search (pgvector embeddings)
  - Time support via item/chunk timestamps
- Answers questions via /chat using hybrid retrieval + LLM synthesis with citations.

## Run
1) Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
2) Start:
   ```bash
   docker compose up --build
   ```

3) Open Streamlit: http://localhost:8501
4) API health: http://localhost:8000/health

## Debugging
- Worker logs:

```
docker compose logs -f worker
```

- API logs:

```
docker compose logs -f api
```

If ingestion stays in PROCESSING, check worker logs and ensure OPENAI_API_KEY is set in .env.

---

# Backend

## `backend/Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## `backend/requirements.txt`
```
fastapi==0.115.6
uvicorn[standard]==0.30.6
pydantic==2.10.4
pydantic-settings==2.7.1
python-dotenv==1.0.1

SQLAlchemy==2.0.36
psycopg2-binary==2.9.9

celery==5.4.0
redis==5.1.1

openai==1.59.6
trafilatura==1.9.0
beautifulsoup4==4.12.3
pypdf==5.1.0
```

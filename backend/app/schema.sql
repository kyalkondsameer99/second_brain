-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Users
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One per artifact: audio/pdf/md/web/note/image
CREATE TABLE IF NOT EXISTS knowledge_items (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),

  source_type TEXT NOT NULL, -- AUDIO, PDF, MARKDOWN, WEB, NOTE, IMAGE
  title TEXT,
  source_uri TEXT,

  raw_object_key TEXT,          -- where the raw file is stored (local path / s3 key)
  derived_text_object_key TEXT, -- optional pointer to derived text (transcript/extracted text)

  checksum TEXT,                -- dedupe
  status TEXT NOT NULL,         -- PENDING, PROCESSING, READY, FAILED
  error_message TEXT,

  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Temporal support
  source_time TIMESTAMPTZ,       -- best-known event time for the item
  content_time_start TIMESTAMPTZ,
  content_time_end TIMESTAMPTZ,

  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Retrieval unit
CREATE TABLE IF NOT EXISTS chunks (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  item_id UUID NOT NULL REFERENCES knowledge_items(id),

  chunk_index INT NOT NULL,
  text TEXT NOT NULL,

  -- Keyword search
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,

  -- Semantic search
  embedding vector(1536),

  token_count INT,
  chunk_hash TEXT,

  -- Citations / pointers
  pointer_type TEXT,        -- PDF_PAGE, AUDIO_MS, URL, NOTE_RANGE, IMAGE_REF
  pointer_start TEXT,
  pointer_end TEXT,

  -- Temporal support at chunk level (best-effort)
  chunk_time_start TIMESTAMPTZ,
  chunk_time_end TIMESTAMPTZ,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE(item_id, chunk_index)
);

-- Indexes (user scoping is mandatory at query time)
CREATE INDEX IF NOT EXISTS idx_items_user ON knowledge_items(user_id);
CREATE INDEX IF NOT EXISTS idx_items_user_time ON knowledge_items(user_id, source_time);
CREATE INDEX IF NOT EXISTS idx_chunks_user_item ON chunks(user_id, item_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_time ON chunks(user_id, chunk_time_start);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);

-- Vector index (HNSW)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops);

-- Demo user (for a simple prototype)
INSERT INTO users (id, email)
VALUES ('00000000-0000-0000-0000-000000000001', 'demo@local')
ON CONFLICT DO NOTHING;

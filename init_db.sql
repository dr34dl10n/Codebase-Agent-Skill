-- Codebase Skill: Database initialization script
-- Run as superuser: sudo -u postgres psql -d codeindex -f init_db.sql

-- Enable pgvector extension (requires superuser)
CREATE EXTENSION IF NOT EXISTS vector;

-- Main table: code chunks
CREATE TABLE IF NOT EXISTS code_chunks (
    id BIGSERIAL PRIMARY KEY,
    file_path TEXT NOT NULL,
    language TEXT,
    symbol TEXT,                        -- function/class/method name
    content TEXT NOT NULL,
    summary TEXT,
    start_line INTEGER,
    end_line INTEGER,
    metadata JSONB DEFAULT '{}',        -- module, git info, etc.
    embedding VECTOR(768),              -- ModernBERT/nomic produce 768-dim vectors (1024 for modernbert-embed-large)
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_code_hnsw ON code_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- B-tree indexes for filtering
CREATE INDEX IF NOT EXISTS idx_file_path ON code_chunks(file_path);
CREATE INDEX IF NOT EXISTS idx_language ON code_chunks(language);
CREATE INDEX IF NOT EXISTS idx_symbol ON code_chunks(symbol);

-- Timestamp for incremental reindexing
CREATE INDEX IF NOT EXISTS idx_updated_at ON code_chunks(updated_at);

-- Project metadata table
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    last_indexed TIMESTAMPTZ,
    total_chunks INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}'
);

-- Ground truth: raw source of every indexed file. This is the fallback used
-- to reconstruct a file when the original on disk is lost (see EMERGENCY.md
-- BUG #4). Chunks alone are not reliably reconstructable, but file_sources
-- stores the verbatim content.
CREATE TABLE IF NOT EXISTS file_sources (
    file_path TEXT NOT NULL,
    language TEXT,
    content TEXT NOT NULL,
    indexed_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (file_path)
);

CREATE INDEX IF NOT EXISTS idx_file_sources_path_prefix ON file_sources(file_path text_pattern_ops);
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
    embedding VECTOR(768),              -- nomic-embed-text produces 768-dim vectors
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

-- Upsert function for incremental updates
CREATE OR REPLACE FUNCTION upsert_code_chunk(
    p_file_path TEXT,
    p_language TEXT,
    p_symbol TEXT,
    p_content TEXT,
    p_summary TEXT,
    p_start_line INTEGER,
    p_end_line INTEGER,
    p_metadata JSONB,
    p_embedding VECTOR(768)
) RETURNS BIGINT AS $$
DECLARE
    chunk_id BIGINT;
BEGIN
    INSERT INTO code_chunks (file_path, language, symbol, content, summary, start_line, end_line, metadata, embedding, updated_at)
    VALUES (p_file_path, p_language, p_symbol, p_content, p_summary, p_start_line, p_end_line, p_metadata, p_embedding, now())
    ON CONFLICT DO NOTHING
    RETURNING id INTO chunk_id;
    
    IF chunk_id IS NULL THEN
        -- Try to find existing chunk by file_path + symbol + start_line
        UPDATE code_chunks SET
            content = p_content,
            summary = p_summary,
            end_line = p_end_line,
            metadata = p_metadata,
            embedding = p_embedding,
            updated_at = now()
        WHERE file_path = p_file_path AND symbol = p_symbol AND start_line = p_start_line
        RETURNING id INTO chunk_id;
    END IF;
    
    RETURN chunk_id;
END;
$$ LANGUAGE plpgsql;
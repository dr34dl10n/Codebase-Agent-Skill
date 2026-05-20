"""Semantic search over indexed code chunks using pgvector.

Supports:
- Cosine similarity search with metadata filters
- Hybrid search (vector + full-text)
- File context retrieval (full file + related chunks)
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

import psycopg
from pgvector.psycopg import register_vector

from config import AppConfig
from embedder import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result."""
    id: int
    file_path: str
    language: str
    symbol: str
    content: str
    summary: Optional[str]
    start_line: int
    end_line: int
    score: float
    metadata: dict


class CodeSearcher:
    """Search code chunks using vector similarity."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self._conn = None
        self._embedder = EmbeddingProvider(self.config.embed)

    def _get_conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.config.db.dsn)
            register_vector(self._conn)
        return self._conn

    def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        language: Optional[str] = None,
        file_pattern: Optional[str] = None,
        repo_path: Optional[str] = None,
        min_score: float = 0.3,
    ) -> list[SearchResult]:
        """Search code chunks by semantic similarity.

        Args:
            query: Natural language or code query.
            top_k: Maximum number of results.
            language: Filter by programming language.
            file_pattern: SQL LIKE pattern for file_path (e.g. '%/auth%').
            repo_path: Filter to a specific repository.
            min_score: Minimum cosine similarity (0-1).

        Returns:
            List of SearchResult sorted by relevance (best first).
        """
        conn = self._get_conn()
        
        # Embed the query
        query_emb = self._embedder.embed_single(query)
        if not query_emb or all(v == 0.0 for v in query_emb):
            logger.error("Failed to embed query")
            return []

        # Build query with optional filters
        sql = """
            SELECT id, file_path, language, symbol, content, summary,
                   start_line, end_line, metadata,
                   1 - (embedding <=> %s::vector) AS score
            FROM code_chunks
            WHERE embedding IS NOT NULL
        """
        params: list = [query_emb]

        if language:
            sql += " AND language = %s"
            params.append(language)
        if file_pattern:
            sql += " AND file_path LIKE %s"
            params.append(file_pattern)
        if repo_path:
            sql += " AND file_path LIKE %s"
            params.append(repo_path.rstrip("/") + "/%")

        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([query_emb, top_k])

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        results = []
        for row in rows:
            score = row[9]
            if score >= min_score:
                meta = row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}")
                results.append(SearchResult(
                    id=row[0],
                    file_path=row[1],
                    language=row[2],
                    symbol=row[3],
                    content=row[4],
                    summary=row[5],
                    start_line=row[6],
                    end_line=row[7],
                    score=round(score, 4),
                    metadata=meta,
                ))

        return results

    def get_file_context(
        self,
        file_path: str,
        focus: Optional[str] = None,
        top_k: int = 5,
    ) -> dict:
        """Get a file's content plus semantically related chunks.

        Args:
            file_path: Path to the file.
            focus: Optional query to focus the related chunk search.
            top_k: Number of related chunks to include.

        Returns:
            Dict with file_content, related_chunks, file_chunks.
        """
        from pathlib import Path

        conn = self._get_conn()
        
        # Get all chunks for this file
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, symbol, content, start_line, end_line, language "
                "FROM code_chunks WHERE file_path = %s "
                "ORDER BY start_line",
                (file_path,),
            )
            file_rows = cur.fetchall()

        file_chunks = [
            {"id": r[0], "symbol": r[1], "content": r[2],
             "start_line": r[3], "end_line": r[4], "language": r[5]}
            for r in file_rows
        ]

        # Read the actual file if it exists
        file_content = None
        try:
            file_content = Path(file_path).read_text(errors="replace")
        except OSError:
            pass

        # Find related chunks from other files
        related = []
        if focus and file_chunks:
            focus_emb = self._embedder.embed_single(focus)
            if focus_emb:
                # Exclude chunks from the same file
                sql = """
                    SELECT id, file_path, language, symbol, content,
                           start_line, end_line,
                           1 - (embedding <=> %s::vector) AS score
                    FROM code_chunks
                    WHERE embedding IS NOT NULL
                      AND file_path != %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """
                with conn.cursor() as cur:
                    cur.execute(sql, [focus_emb, file_path, focus_emb, top_k])
                    rel_rows = cur.fetchall()

                related = [
                    {"id": r[0], "file_path": r[1], "language": r[2],
                     "symbol": r[3], "content": r[4],
                     "start_line": r[5], "end_line": r[6], "score": round(r[7], 4)}
                    for r in rel_rows
                ]

        return {
            "file_path": file_path,
            "file_content": file_content,
            "file_chunks": file_chunks,
            "related_chunks": related,
        }

    def get_stats(self, repo_path: Optional[str] = None) -> dict:
        """Get indexing statistics."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            if repo_path:
                cur.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT file_path), "
                    "COUNT(DISTINCT language) FROM code_chunks "
                    "WHERE file_path LIKE %s",
                    (repo_path.rstrip("/") + "/%",),
                )
            else:
                cur.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT file_path), "
                    "COUNT(DISTINCT language) FROM code_chunks"
                )
            row = cur.fetchone()

        return {
            "total_chunks": row[0],
            "total_files": row[1],
            "total_languages": row[2],
        }

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
        self._embedder.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
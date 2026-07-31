"""Indexer: walks repos, parses files, embeds chunks, stores in pgvector.

Supports:
- Full repository indexing
- Incremental reindexing (only changed files)
- File-level deletion of stale chunks
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import psycopg
from pgvector.psycopg import register_vector

from config import AppConfig
from embedder import create_provider, EmbeddingProvider
from parser import parse_file, walk_repository, CodeChunk

logger = logging.getLogger(__name__)


class CodeIndexer:
    """Index code repositories into pgvector for semantic search."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self._conn = None
        self._embedder = create_provider(self.config.embed)

    def _get_conn(self) -> psycopg.Connection:
        """Get or create a database connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.config.db.dsn)
            register_vector(self._conn)
        return self._conn

    def index_repository(
        self,
        repo_path: str,
        force_reindex: bool = False,
        project_name: Optional[str] = None,
    ) -> dict:
        """Index a repository into pgvector.

        Args:
            repo_path: Absolute path to the repository root.
            force_reindex: If True, re-index all files even if unchanged.
            project_name: Optional project name for the projects table.

        Returns:
            Stats dict with counts and timing.
        """
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            raise ValueError(f"Not a directory: {repo_path}")

        name = project_name or repo.name
        start_time = time.time()
        conn = self._get_conn()

        # Get or create project record
        proj = self._upsert_project(conn, str(repo), name)

        # Find all parseable files on disk
        t0 = time.time()
        files_on_disk = set(walk_repository(str(repo), self.config.parse))
        t_walk = time.time() - t0
        logger.info(f"Found {len(files_on_disk)} parseable files in {repo} (walk {t_walk:.2f}s)")

        # Purge chunks for files that no longer exist on disk
        deleted_orphans = self._purge_deleted_files(conn, str(repo), files_on_disk)
        if deleted_orphans:
            logger.info(f"Purged {deleted_orphans} orphan chunks (deleted files)")

        if not force_reindex:
            # Only index files modified since last index
            last_indexed = proj.get("last_indexed")
            if last_indexed:
                files_on_disk = set(self._filter_changed(list(files_on_disk), last_indexed))
                logger.info(f"{len(files_on_disk)} files changed since last index")

        # Parse all files into chunks
        t0 = time.time()
        all_chunks: list[CodeChunk] = []
        for fpath in files_on_disk:
            try:
                chunks = parse_file(fpath, self.config.parse)
                all_chunks.extend(chunks)
                # Store verbatim source as ground truth (EMERGENCY.md BUG #4):
                # enables file reconstruction when the file is lost on disk.
                self._upsert_file_source(conn, fpath, self.config.parse)
            except Exception as e:
                logger.warning(f"Parse error on {fpath}: {e}")
        t_parse = time.time() - t0

        logger.info(f"Parsed {len(all_chunks)} chunks from {len(files_on_disk)} files (parse {t_parse:.2f}s)")

        # Delete stale chunks for changed files (old chunks from these files)
        if files_on_disk:
            self._delete_file_chunks(conn, [str(Path(f)) for f in files_on_disk])

        # Embed and store chunks in batches
        stored, t_embed, t_store = self._embed_and_store(conn, all_chunks)

        # Update project metadata
        total = self._count_chunks(conn, str(repo))
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET last_indexed = now(), total_chunks = %s "
                "WHERE path = %s",
                (total, str(repo)),
            )
        conn.commit()

        elapsed = time.time() - start_time
        stats = {
            "project": name,
            "files_processed": len(files_on_disk),
            "chunks_parsed": len(all_chunks),
            "chunks_stored": stored,
            "orphan_chunks_purged": deleted_orphans,
            "total_chunks": total,
            "elapsed_seconds": round(elapsed, 2),
            "phase_seconds": {
                "walk": round(t_walk, 2),
                "parse": round(t_parse, 2),
                "embed": round(t_embed, 2),
                "store": round(t_store, 2),
            },
        }
        logger.info(f"Indexing complete: {stats}")
        return stats

    def _upsert_project(self, conn, repo_path: str, name: str) -> dict:
        """Insert or update project record, return current state."""
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO projects (path, metadata) VALUES (%s, %s) "
                "ON CONFLICT (path) DO UPDATE SET metadata = EXCLUDED.metadata "
                "RETURNING last_indexed, total_chunks",
                (repo_path, json.dumps({"name": name})),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                return {"last_indexed": row[0], "total_chunks": row[1]}
            return {"last_indexed": None, "total_chunks": 0}

    def _filter_changed(self, files: list[str], since) -> list[str]:
        """Filter to only files modified since a timestamp."""
        import datetime
        if isinstance(since, str):
            since = datetime.datetime.fromisoformat(since)
        
        changed = []
        for f in files:
            try:
                mtime = Path(f).stat().st_mtime
                mt = datetime.datetime.fromtimestamp(mtime, tz=since.tzinfo if hasattr(since, 'tzinfo') else None)
                if mt > since:
                    changed.append(f)
            except OSError:
                changed.append(f)  # Include if we can't stat
        return changed

    def _upsert_file_source(self, conn, file_path: str, parse_config) -> None:
        """Store the verbatim source of a file in file_sources (ground truth)."""
        try:
            from pathlib import Path as _P
            p = _P(file_path)
            language = parse_config.supported_extensions.get(p.suffix)
            content = p.read_bytes().decode("utf-8", errors="replace")
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO file_sources (file_path, language, content, indexed_at) "
                    "VALUES (%s, %s, %s, now()) "
                    "ON CONFLICT (file_path) DO UPDATE SET "
                    "language = EXCLUDED.language, content = EXCLUDED.content, indexed_at = now()",
                    (file_path, language, content),
                )
        except OSError:
            pass

    def _delete_file_chunks(self, conn, file_paths: list[str]) -> None:
        """Remove chunks belonging to given file paths.

        Note: file_sources (ground truth) is intentionally NOT deleted here.
        This method is called both when re-indexing changed files (where the
        upsert already refreshed the source) and when purging orphan files
        (where the stored source is the recovery ground truth — see
        EMERGENCY.md BUG #4). file_sources is only purged on explicit
        repo removal (`remove`).
        """
        if not file_paths:
            return
        with conn.cursor() as cur:
            # Use ANY for efficient batch delete
            cur.execute(
                "DELETE FROM code_chunks WHERE file_path = ANY(%s)",
                (file_paths,),
            )
        conn.commit()
        logger.debug(f"Deleted stale chunks for {len(file_paths)} files")

    def _embed_and_store(self, conn, chunks: list[CodeChunk]) -> tuple[int, float, float]:
        """Embed chunks and insert into database. Returns (count, embed_time, store_time)."""
        if not chunks:
            return 0, 0.0, 0.0

        stored = 0
        batch_size = self.config.embed.batch_size
        t_embed_total = 0.0
        t_store_total = 0.0
        n_batches = (len(chunks) - 1) // batch_size + 1

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            texts = [c.content for c in batch]

            # Generate embeddings
            print(f"  Embedding batch {i//batch_size + 1}/{n_batches} ({len(batch)} chunks)...")
            t0 = time.time()
            embeddings = self._embedder.embed(texts)
            t_embed_total += time.time() - t0

            # Insert into DB
            t0 = time.time()
            with conn.cursor() as cur:
                for chunk, emb in zip(batch, embeddings):
                    try:
                        cur.execute(
                            """INSERT INTO code_chunks
                               (file_path, language, symbol, content, summary,
                                start_line, end_line, metadata, embedding)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (
                                chunk.file_path,
                                chunk.language,
                                chunk.symbol,
                                chunk.content,
                                chunk.metadata.get("summary"),
                                chunk.start_line,
                                chunk.end_line,
                                json.dumps(chunk.metadata),
                                [float(v) for v in emb],
                            ),
                        )
                        stored += 1
                    except Exception as e:
                        logger.warning(f"Insert failed for {chunk.file_path}:{chunk.symbol}: {e}")

            conn.commit()
            t_store_total += time.time() - t0
            logger.debug(f"Stored batch {i//batch_size + 1}: {len(batch)} chunks")

        return stored, t_embed_total, t_store_total

    def _count_chunks(self, conn, repo_path: str) -> int:
        """Count total chunks for a repository."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM code_chunks WHERE file_path LIKE %s",
                (repo_path + "/%",),
            )
            return cur.fetchone()[0]

    def _purge_deleted_files(self, conn, repo_path: str, files_on_disk: set[str]) -> int:
        """Purge chunks for files that no longer exist on disk.

        Compares file paths in the DB for this repo against the actual
        filesystem. Deletes chunks for any files that have been removed.

        Returns:
            Number of orphan chunks deleted.
        """
        # Get all distinct file paths in DB for this repo
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT file_path FROM code_chunks WHERE file_path LIKE %s",
                (repo_path + "/%",),
            )
            db_files = {row[0] for row in cur.fetchall()}

        # Find files in DB that don't exist on disk anymore
        orphans = [f for f in db_files if f not in files_on_disk]
        if not orphans:
            return 0

        self._delete_file_chunks(conn, orphans)
        return len(orphans)

    def list_projects(self) -> list[dict]:
        """List all indexed projects with their metadata."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, last_indexed, total_chunks, metadata FROM projects ORDER BY last_indexed DESC NULLS LAST"
            )
            rows = cur.fetchall()
        return [
            {"path": r[0], "last_indexed": r[1].isoformat() if r[1] else None,
             "total_chunks": r[2], "metadata": r[3]}
            for r in rows
        ]

    def remove_repository(self, repo_path: str) -> int:
        """Remove all indexed data for a repository."""
        conn = self._get_conn()
        repo = str(Path(repo_path).resolve())
        
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM code_chunks WHERE file_path LIKE %s",
                (repo + "/%",),
            )
            deleted = cur.rowcount
            cur.execute("DELETE FROM file_sources WHERE file_path LIKE %s", (repo + "/%",))
            cur.execute("DELETE FROM projects WHERE path = %s", (repo,))
        conn.commit()
        logger.info(f"Removed {deleted} chunks for {repo}")
        return deleted

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
        self._embedder.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()



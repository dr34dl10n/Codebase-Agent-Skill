"""FastAPI server for codebase-skill.

Provides HTTP endpoints for indexing and searching code repositories.
Also MCP-compatible tool definitions.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from config import AppConfig
from indexer import CodeIndexer
from search import CodeSearcher, SearchResult

logger = logging.getLogger(__name__)

# Global state
_config: AppConfig = AppConfig()
_indexer: Optional[CodeIndexer] = None
_searcher: Optional[CodeSearcher] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _indexer, _searcher
    _indexer = CodeIndexer(_config)
    _searcher = CodeSearcher(_config)
    yield
    if _indexer:
        _indexer.close()
    if _searcher:
        _searcher.close()


app = FastAPI(
    title="codebase-skill",
    description="Semantic code indexing and search via pgvector",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Request/Response models ---

class IndexRequest(BaseModel):
    repo_path: str = Field(..., description="Absolute path to repository root")
    force_reindex: bool = Field(False, description="Re-index all files")
    project_name: Optional[str] = Field(None, description="Project name")


class IndexResponse(BaseModel):
    project: str
    files_processed: int
    chunks_parsed: int
    chunks_stored: int
    total_chunks: int
    elapsed_seconds: float


class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language or code query")
    top_k: int = Field(10, ge=1, le=50, description="Max results")
    language: Optional[str] = Field(None, description="Filter by language")
    file_pattern: Optional[str] = Field(None, description="SQL LIKE pattern for file_path")
    repo_path: Optional[str] = Field(None, description="Filter by repo path")
    min_score: float = Field(0.3, ge=0, le=1, description="Min similarity score")


class SearchHit(BaseModel):
    id: int
    file_path: str
    language: str
    symbol: str
    content: str
    summary: Optional[str]
    start_line: int
    end_line: int
    score: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]
    total: int


class FileContextRequest(BaseModel):
    file_path: str = Field(..., description="Path to the file")
    focus: Optional[str] = Field(None, description="Query to focus related search")
    top_k: int = Field(5, ge=1, le=20, description="Related chunks count")


class FileContextResponse(BaseModel):
    file_path: str
    file_content: Optional[str]
    file_chunks: list[dict]
    related_chunks: list[dict]


class StatsResponse(BaseModel):
    total_chunks: int
    total_files: int
    total_languages: int


class RemoveResponse(BaseModel):
    repo_path: str
    chunks_deleted: int


# --- Endpoints ---

@app.post("/index", response_model=IndexResponse)
async def index_repository(req: IndexRequest):
    """Index a repository into pgvector."""
    try:
        stats = _indexer.index_repository(
            repo_path=req.repo_path,
            force_reindex=req.force_reindex,
            project_name=req.project_name,
        )
        return IndexResponse(**stats)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Indexing failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", response_model=SearchResponse)
async def semantic_search(req: SearchRequest):
    """Search code chunks by semantic similarity."""
    results = _searcher.semantic_search(
        query=req.query,
        top_k=req.top_k,
        language=req.language,
        file_pattern=req.file_pattern,
        repo_path=req.repo_path,
        min_score=req.min_score,
    )
    hits = [
        SearchHit(
            id=r.id, file_path=r.file_path, language=r.language,
            symbol=r.symbol, content=r.content, summary=r.summary,
            start_line=r.start_line, end_line=r.end_line, score=r.score,
        )
        for r in results
    ]
    return SearchResponse(query=req.query, results=hits, total=len(hits))


@app.post("/file-context", response_model=FileContextResponse)
async def get_file_context(req: FileContextRequest):
    """Get a file's content plus semantically related chunks."""
    ctx = _searcher.get_file_context(
        file_path=req.file_path,
        focus=req.focus,
        top_k=req.top_k,
    )
    return FileContextResponse(**ctx)


@app.get("/stats", response_model=StatsResponse)
async def get_stats(repo_path: Optional[str] = Query(None)):
    """Get indexing statistics."""
    return StatsResponse(**_searcher.get_stats(repo_path))


@app.delete("/repository", response_model=RemoveResponse)
async def remove_repository(repo_path: str = Query(...)):
    """Remove all indexed data for a repository."""
    deleted = _indexer.remove_repository(repo_path)
    return RemoveResponse(repo_path=repo_path, chunks_deleted=deleted)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# --- MCP tool definitions (for tool registration) ---

MCP_TOOLS = [
    {
        "name": "index_repository",
        "description": "Index a code repository for semantic search. Parses files with tree-sitter, generates embeddings, stores in pgvector.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Absolute path to repository root"},
                "force_reindex": {"type": "boolean", "default": False, "description": "Re-index all files"},
                "project_name": {"type": "string", "description": "Optional project name"},
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "semantic_search",
        "description": "Search indexed code by natural language or code query. Returns relevant chunks with file paths, symbol names, and similarity scores.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "default": 10, "description": "Max results (1-50)"},
                "language": {"type": "string", "description": "Filter by programming language"},
                "file_pattern": {"type": "string", "description": "SQL LIKE pattern for file path"},
                "repo_path": {"type": "string", "description": "Filter by repo path"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_file_context",
        "description": "Get a file's full content plus semantically related chunks from other files. Useful for understanding context before editing.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "focus": {"type": "string", "description": "Query to focus related chunk search"},
                "top_k": {"type": "integer", "default": 5, "description": "Related chunks count"},
            },
            "required": ["file_path"],
        },
    },
]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host=_config.api_host,
        port=_config.api_port,
        log_level="info",
    )
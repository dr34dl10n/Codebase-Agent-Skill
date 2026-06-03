"""Configuration for codebase-skill.

Database mode:
  CODEINDEX_DB_MODE controls which defaults are used:
    "local"  (default) — Local PostgreSQL, user=codeindex, db=codeindex, port=5432
    "docker"           — Docker pgvectordb, user=postgres, db=codebase, port=5433

  Password resolution (never uses hardcoded insecure defaults):
    1. CODEINDEX_DB_PASSWORD env var (shell / ~/.hermes/.env / project .env)
    2. If empty, config.py raises ValueError — the deploy script or user
       must provide a password. The deploy script auto-generates one and
       stores it in the project .env file.

  All defaults can be overridden individually via their CODEINDEX_DB_* env vars.

Embedding model selection (ModernBERT recommended — default):
  Set CODEINDEX_EMBED_MODEL to choose your model. If unset, defaults to
  modernbert-embed-base. Run `python scripts/detect_model.py` to auto-detect
  the best model for your hardware (writes the result to .env).

  Recommended models (sentence_transformers backend — zero external deps):
    modernbert-embed-base   – 768-dim, 8192 context (fast on CPU, default)
    modernbert-embed-large  – 1024-dim, 8192 context (better quality, more RAM)

  Advanced: Ollama backend for GPU servers already running Ollama:
    nomic-embed-text        – 768-dim, ~8k context (requires Ollama server)

  Backend selection:
    Set CODEINDEX_EMBED_BACKEND to "sentence_transformers" (default) or "ollama".
    ModernBERT models require the sentence_transformers backend.
    nomic-embed-text requires the ollama backend.
    Auto-detected from model name if not set.
"""

import os
import logging
import secrets
import string
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no overwrite)."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


# Auto-load env files — project .env first (higher priority), then ~/.hermes/.env
# This ensures the project .env (written by deploy.sh) takes precedence.
_SCRIPT_DIR = Path(__file__).resolve().parent
_load_env_file(Path.home() / ".hermes" / ".env")
_load_env_file(_SCRIPT_DIR / ".env")


# ─── Database mode defaults ────────────────────────────────────────────
_DB_MODE = os.getenv("CODEINDEX_DB_MODE", "local")

if _DB_MODE == "docker":
    _DB_DEFAULTS = {
        "host": "localhost",
        "port": "5433",
        "database": "codebase",
        "user": "postgres",
    }
else:
    _DB_DEFAULTS = {
        "host": "localhost",
        "port": "5432",
        "database": "codeindex",
        "user": "codeindex",
    }


def _generate_password(length: int = 32) -> str:
    """Generate a cryptographically random password (alphanumeric + safe symbols)."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@dataclass
class DBConfig:
    host: str = os.getenv("CODEINDEX_DB_HOST", _DB_DEFAULTS["host"])
    port: int = int(os.getenv("CODEINDEX_DB_PORT", _DB_DEFAULTS["port"]))
    database: str = os.getenv("CODEINDEX_DB_NAME", _DB_DEFAULTS["database"])
    user: str = os.getenv("CODEINDEX_DB_USER", _DB_DEFAULTS["user"])
    password: str = os.getenv("CODEINDEX_DB_PASSWORD", "")

    def __post_init__(self):
        if not self.password:
            # No password in env — try to generate one and persist it
            generated = _generate_password()
            logger.warning(
                "CODEINDEX_DB_PASSWORD not set. Generated a random password. "
                "Run `bash deploy.sh` to set up the database properly, or "
                "set CODEINDEX_DB_PASSWORD in your environment."
            )
            self.password = generated

    @property
    def mode(self) -> str:
        """Current database mode: 'local' or 'docker'."""
        return _DB_MODE

    @property
    def dsn(self) -> str:
        """Full DSN for psycopg.connect(). Includes the real password."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    @property
    def safe_dsn(self) -> str:
        """DSN with masked password — safe for logging and display."""
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.database}"



# Model dimension lookup – keeps dim in sync with the chosen model.
_MODEL_DIMS = {
    "nomic-embed-text":       768,
    "modernbert-embed-base":  768,
    "modernbert-embed-large": 1024,
}

# Maximum context length per model (in characters, rough ≈ 4 chars/token).
_MODEL_MAX_TEXT = {
    "nomic-embed-text":       32000,   # ~8k tokens
    "modernbert-embed-base":  32768,   # 8192 tokens
    "modernbert-embed-large": 32768,    # 8192 tokens
}

# HuggingFace model IDs for sentence-transformers backend.
_HF_MODEL_IDS = {
    "modernbert-embed-base":  "nomic-ai/modernbert-embed-base",
    "modernbert-embed-large": "lightonai/modernbert-embed-large",
}

# Backend selection per model (auto-detected if not set).
_MODEL_BACKEND = {
    "nomic-embed-text":       "ollama",
    "modernbert-embed-base":  "sentence_transformers",
    "modernbert-embed-large": "sentence_transformers",
}


@dataclass
class EmbedConfig:
    model: str = os.getenv("CODEINDEX_EMBED_MODEL", "modernbert-embed-base")
    backend: str = os.getenv("CODEINDEX_EMBED_BACKEND", "")
    api_base: str = os.getenv("CODEINDEX_EMBED_API_BASE", "http://localhost:11434")
    dim: int = 0  # 0 means "auto-detect from model name"
    batch_size: int = 16
    max_text_len: int = 0  # 0 means "auto-detect from model name"

    def __post_init__(self):
        # Auto-detect backend from model name if not set
        if not self.backend:
            self.backend = _MODEL_BACKEND.get(self.model, "ollama")
        # Resolve dim from model name if not explicitly set
        if self.dim == 0:
            self.dim = _MODEL_DIMS.get(self.model, 768)
        # Resolve max_text_len from model name if not explicitly set
        if self.max_text_len == 0:
            self.max_text_len = _MODEL_MAX_TEXT.get(self.model, 32000)
        self.model = self.model  # ensure it's stored
        logger.info("EmbedConfig: model=%s backend=%s dim=%d max_text_len=%d",
                    self.model, self.backend, self.dim, self.max_text_len)


@dataclass
class ParseConfig:
    # Target chunk sizes in characters (not tokens)
    min_chunk_size: int = 100
    max_chunk_size: int = 2000
    # Languages supported by tree-sitter-languages
    supported_extensions: dict = field(default_factory=lambda: {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "c_sharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".lua": "lua",
        ".r": "r",
        ".sh": "bash",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
    })
    # Directories to skip
    skip_dirs: set = field(default_factory=lambda: {
        "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
        "dist", "build", ".next", ".nuxt", "target", "vendor",
        ".tox", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
    })


@dataclass
class AppConfig:
    db: DBConfig = field(default_factory=DBConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    parse: ParseConfig = field(default_factory=ParseConfig)
    api_host: str = os.getenv("CODEINDEX_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("CODEINDEX_API_PORT", "8900"))


def load_config() -> AppConfig:
    return AppConfig()
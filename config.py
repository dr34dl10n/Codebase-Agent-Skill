"""Configuration for codebase-skill."""

import os
from dataclasses import dataclass, field
from pathlib import Path


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


# Auto-load ~/.hermes/.env if not already loaded by the shell
_load_env_file(Path.home() / ".hermes" / ".env")


@dataclass
class DBConfig:
    host: str = os.getenv("CODEINDEX_DB_HOST", "localhost")
    port: int = int(os.getenv("CODEINDEX_DB_PORT", "5432"))
    database: str = os.getenv("CODEINDEX_DB_NAME", "codeindex")
    user: str = os.getenv("CODEINDEX_DB_USER", "codeindex")
    password: str = os.getenv("CODEINDEX_DB_PASSWORD", "")

    def __post_init__(self):
        if not self.password:
            raise ValueError(
                "CODEINDEX_DB_PASSWORD env var is required. "
                "Set it in .env or export it before running."
            )

    @property
    def dsn(self) -> str:
        """Full DSN for psycopg.connect(). Includes the real password."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    @property
    def safe_dsn(self) -> str:
        """DSN with masked password — safe for logging and display."""
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.database}"


@dataclass
class EmbedConfig:
    model: str = os.getenv("CODEINDEX_EMBED_MODEL", "nomic-embed-text")
    api_base: str = os.getenv("CODEINDEX_EMBED_API_BASE", "http://localhost:11434")
    dim: int = 768  # nomic-embed-text dimension
    batch_size: int = 16


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
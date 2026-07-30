"""Tree-sitter based code parser for semantic chunking.

Chunks code by functions, classes, methods — not by naive text splitting.
Each chunk preserves file path, symbol name, and line numbers as metadata.
"""

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tree_sitter_languages  # type: ignore
from tree_sitter import Node, Parser, Tree

from config import ParseConfig


@dataclass
class CodeChunk:
    """A semantically meaningful chunk of code."""
    file_path: str
    language: str
    symbol: str          # function/class/method name
    content: str
    start_line: int
    end_line: int
    metadata: dict = field(default_factory=dict)


# Node types that represent semantic boundaries per language
DEF_NODE_TYPES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition",
                    "arrow_function"},
    "typescript": {"function_declaration", "class_declaration", "method_definition",
                   "arrow_function", "interface_declaration", "type_alias_declaration",
                   "enum_declaration"},
    "tsx": {"function_declaration", "class_declaration", "method_definition",
            "arrow_function", "interface_declaration", "type_alias_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {"function_item", "impl_item", "struct_item", "enum_item",
             "trait_item", "type_item"},
    "java": {"class_declaration", "method_declaration", "interface_declaration",
             "enum_declaration", "constructor_declaration"},
    "c": {"function_definition", "struct_specifier", "enum_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier",
            "enum_specifier", "namespace_definition"},
    "ruby": {"method", "singleton_method", "class", "module"},
    "php": {"function_definition", "class_declaration", "method_declaration"},
    "swift": {"function_declaration", "class_declaration", "struct_declaration",
              "enum_declaration", "protocol_declaration"},
    "kotlin": {"function_declaration", "class_declaration", "object_declaration",
               "interface_declaration"},
    "scala": {"function_definition", "class_definition", "object_definition",
              "trait_definition"},
    "c_sharp": {"class_declaration", "method_declaration", "interface_declaration",
                "struct_declaration", "enum_declaration"},
    "lua": {"function_declaration", "function_definition"},
}


def _get_parser(language: str) -> Parser:
    """Get a tree-sitter parser for the given language."""
    try:
        return tree_sitter_languages.get_parser(language)
    except Exception:
        from tree_sitter_languages import get_language
        lang = get_language(language)
        parser = Parser()
        parser.set_language(lang)
        return parser


def _extract_symbol_name(node: Node) -> str:
    """Extract the name of a function/class/method from its definition node."""
    # Walk named children for identifier-like nodes
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier",
                          "type_identifier"):
            return child.text.decode("utf-8", errors="replace")
    return "<anonymous>"


def _find_def_node(node: Node) -> Optional[Node]:
    """Return the underlying function/class node, unwrapping decorators."""
    if node.type == "decorated_definition":
        for c in node.children:
            if c.type in ("function_definition", "class_definition"):
                return c
        return None
    return node


def _extract_python_docstring(def_node: Node) -> Optional[str]:
    """Best-effort extraction of a Python docstring from a def/class node.

    Returns the docstring text (quotes stripped) or None. Defensive: any
    parse-layout mismatch yields None rather than raising.
    """
    try:
        real = _find_def_node(def_node)
        if real is None:
            return None
        body = None
        for c in real.children:
            if c.type == "block":
                body = c
                break
        if body is None or not body.children:
            return None
        first = body.children[0]
        if first is None or first.type != "expression_statement":
            return None
        for sub in first.children:
            if sub.type == "string":
                raw = sub.text.decode("utf-8", errors="replace").strip()
                for q in ('"""', "'''", '"', "'"):
                    if len(raw) >= 2 * len(q) and raw.startswith(q) and raw.endswith(q):
                        raw = raw[len(q):-len(q)]
                        break
                return raw.strip() or None
        return None
    except Exception:
        return None


def _collect_top_level_defs(node: Node, language: str) -> list[tuple[str, Node]]:
    """Collect ONLY top-level definition nodes (functions, classes, etc).
    
    For Python, this means children of module_node only — not nested methods.
    The full class body (including methods) becomes one chunk.
    """
    def_types = DEF_NODE_TYPES.get(language, set())
    results = []

    for child in node.children:
        # Handle decorated definitions (Python: @decorator\ndef foo...)
        if child.type == "decorated_definition":
            for grandchild in child.children:
                if grandchild.type in def_types:
                    name = _extract_symbol_name(grandchild)
                    results.append((name, child))  # use decorated node for full range
                    break
        elif child.type in def_types:
            name = _extract_symbol_name(child)
            results.append((name, child))

    return results


def parse_file(file_path: str, config: ParseConfig) -> list[CodeChunk]:
    """Parse a single file into semantic chunks using tree-sitter.
    
    Strategy:
    - Extract top-level definitions (functions, classes) as individual chunks
    - Capture module-level code (imports, globals) as a <module> chunk
    - If no definitions found, fall back to line-based chunking
    """
    path = Path(file_path)
    ext = path.suffix
    language = config.supported_extensions.get(ext)
    
    if not language:
        return []
    
    try:
        source_bytes = path.read_bytes()
    except OSError:
        return []
    source = source_bytes.decode("utf-8", errors="replace")
    
    if not source.strip():
        return []
    
    chunks: list[CodeChunk] = []
    
    # Markdown parser has a known C++ assertion bug that calls abort()
    # (vendor/tree-sitter-markdown scanner.cc:56 assertion `i <= 1024`).
    # This crashes the entire process — cannot be caught with try/except.
    # For markdown, line-based chunking is sufficient (no function definitions
    # to extract) and avoids the crash entirely.
    if language == "markdown":
        return _chunk_by_lines(source, file_path, language, config)

    try:
        parser = _get_parser(language)
        # tree-sitter works on BYTES — parse the raw bytes so that
        # node.start_byte / node.end_byte are valid offsets into source_bytes.
        tree = parser.parse(source_bytes)
        root = tree.root_node
        
        # Collect ONLY top-level definitions
        defs = _collect_top_level_defs(root, language)
        
        if defs:
            # Track which root-level children are fully covered by a definition
            def_ranges = [(node.start_byte, node.end_byte) for _, node in defs]
            
            def is_covered(child: Node) -> bool:
                """Check if a root-level child is entirely inside a definition."""
                for start, end in def_ranges:
                    if child.start_byte >= start and child.end_byte <= end:
                        return True
                return False
            
            # 1. Capture module-level code as CONTIGUOUS runs of non-covered
            # root children. Each run spans the exact byte range from the first
            # to the last child in the run (including any whitespace between
            # them), so the chunk content matches the source verbatim and can
            # be reassembled by line number. We deliberately do NOT strip the
            # content and do NOT apply min_chunk_size (those caused lost
            # imports and broken line reconstruction — see EMERGENCY.md #2/#3/#5).
            runs: list[tuple[int, int, int, int]] = []
            run_first: Optional[Node] = None
            run_last: Optional[Node] = None
            
            def flush_run() -> None:
                nonlocal run_first, run_last
                if run_first is not None:
                    runs.append((
                        run_first.start_byte,
                        run_last.end_byte,
                        run_first.start_point[0] + 1,
                        run_last.end_point[0] + 1,
                    ))
                    run_first = run_last = None
            
            for child in root.children:
                if is_covered(child):
                    flush_run()
                    continue
                if run_first is None:
                    run_first = child
                run_last = child
            flush_run()
            
            for start_byte, end_byte, start_line, end_line in runs:
                mod_content = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")
                # Skip purely-whitespace runs but keep everything else
                # (even single short imports) so nothing is lost.
                if not mod_content.strip():
                    continue
                chunks.append(CodeChunk(
                    file_path=file_path,
                    language=language,
                    symbol="<module>",
                    content=mod_content,
                    start_line=start_line,
                    end_line=end_line,
                    metadata={"type": "module_level"},
                ))
            
            # 2. Capture each top-level definition as a chunk
            for symbol_name, node in defs:
                # Slice the BYTES (not the str) — node offsets are byte offsets.
                # See EMERGENCY.md BUG #1: slicing a str with byte offsets
                # corrupts content whenever the file has multi-byte chars.
                content = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                if len(content) < config.min_chunk_size:
                    continue
                meta = {"type": "definition"}
                if language == "python":
                    doc = _extract_python_docstring(node)
                    if doc:
                        meta["summary"] = doc
                chunks.append(CodeChunk(
                    file_path=file_path,
                    language=language,
                    symbol=symbol_name,
                    content=content,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    metadata=meta,
                ))
        else:
            # No definitions found — fall back to line-based chunking
            chunks.extend(_chunk_by_lines(source, file_path, language, config))
    
    except Exception:
        chunks.extend(_chunk_by_lines(source, file_path, language, config))
    
    return chunks


def _chunk_by_lines(
    source: str, file_path: str, language: str, config: ParseConfig
) -> list[CodeChunk]:
    """Fallback chunking by lines for files without definitions."""
    lines = source.split("\n")
    chunks = []
    current_lines: list[str] = []
    start_line = 1
    
    for i, line in enumerate(lines, 1):
        current_lines.append(line)
        content = "\n".join(current_lines)
        
        if len(content) >= config.max_chunk_size:
            chunks.append(CodeChunk(
                file_path=file_path,
                language=language,
                symbol=f"<block_{start_line}>",
                content=content,
                start_line=start_line,
                end_line=i,
                metadata={"type": "line_based"},
            ))
            current_lines = []
            start_line = i + 1
    
    if current_lines:
        content = "\n".join(current_lines)
        if len(content) >= config.min_chunk_size:
            chunks.append(CodeChunk(
                file_path=file_path,
                language=language,
                symbol=f"<block_{start_line}>",
                content=content,
                start_line=start_line,
                end_line=len(lines),
                metadata={"type": "line_based"},
            ))
    
    return chunks


def _parse_gitignore(repo_path: Path) -> list[str]:
    """Read .gitignore patterns from the repo root and return them as a list."""
    patterns: list[str] = []
    gitignore = repo_path / ".gitignore"
    if gitignore.is_file():
        for line in gitignore.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def _is_ignored(path: Path, repo: Path, patterns: list[str]) -> bool:
    """Check if a path matches any .gitignore pattern (simplified gitignore semantics)."""
    try:
        rel = path.relative_to(repo)
    except ValueError:
        return False
    parts = rel.parts
    for pat in patterns:
        # Directory patterns (trailing slash)
        if pat.endswith("/"):
            dirname = pat[:-1]
            if dirname in parts[:-1] or parts and parts[-1] == dirname:
                return True
            # also match any ancestor
            for i in range(len(parts) - 1):
                if parts[i] == dirname:
                    return True
        # Negation patterns — not fully supported, skip
        elif pat.startswith("!"):
            continue
        # Simple patterns: match against any path segment or full relative path
        else:
            if fnmatch.fnmatch(parts[-1], pat):
                return True
            if fnmatch.fnmatch(str(rel), pat):
                return True
            # match any ancestor dir against pattern
            for part in parts[:-1]:
                if fnmatch.fnmatch(part, pat):
                    return True
    return False


def _matches_skip_segment(rel_path: str, skip_segments: set[str]) -> bool:
    """Check if a relative path contains any skip segment (e.g. 'assets/data')."""
    if not skip_segments:
        return False
    normalized = rel_path.replace("\\", "/")
    for seg in skip_segments:
        if seg in normalized:
            return True
    return False


def _git_ls_files(repo: Path, config: ParseConfig) -> Optional[list[str]]:
    """Use `git ls-files` to get tracked files (respects .gitignore natively).

    Returns absolute paths, filtered by supported extensions.
    Returns None if not a git repo or git command fails.
    """
    if not (repo / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
    except (subprocess.SubprocessError, OSError):
        return None

    files: list[str] = []
    for line in result.stdout.splitlines():
        fpath = str(repo / line)
        if not os.path.isfile(fpath):
            continue
        ext = Path(line).suffix
        if ext not in config.supported_extensions:
            continue
        if _matches_skip_segment(line, config.skip_path_segments):
            continue
        files.append(fpath)
    return files


def walk_repository(repo_path: str, config: ParseConfig) -> list[str]:
    """Walk a repository and return list of parseable file paths.

    If the repo is a git repository, uses `git ls-files` which respects
    .gitignore natively. Otherwise, falls back to os.walk with skip_dirs
    and manual .gitignore parsing.
    """
    repo = Path(repo_path).resolve()

    # Primary path: git ls-files (respects .gitignore perfectly)
    git_files = _git_ls_files(repo, config)
    if git_files is not None:
        return sorted(git_files)

    # Fallback: os.walk + .gitignore + skip_dirs
    files: list[str] = []
    ignore_patterns = _parse_gitignore(repo)

    for root, dirs, filenames in os.walk(repo):
        # Prune skip_dirs and hidden dirs in-place
        dirs[:] = [
            d for d in sorted(dirs)
            if d not in config.skip_dirs and not d.startswith(".")
        ]

        # Prune dirs matching .gitignore patterns
        if ignore_patterns:
            dirs[:] = [
                d for d in dirs
                if not _is_ignored(Path(root) / d, repo, ignore_patterns)
            ]

        for fname in sorted(filenames):
            fpath = os.path.join(root, fname)
            ext = Path(fname).suffix
            if ext not in config.supported_extensions:
                continue
            if ignore_patterns and _is_ignored(Path(fpath), repo, ignore_patterns):
                continue
            files.append(fpath)

    return files
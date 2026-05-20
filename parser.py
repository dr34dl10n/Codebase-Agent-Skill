"""Tree-sitter based code parser for semantic chunking.

Chunks code by functions, classes, methods — not by naive text splitting.
Each chunk preserves file path, symbol name, and line numbers as metadata.
"""

import os
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
        source = path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    
    if not source.strip():
        return []
    
    chunks: list[CodeChunk] = []
    
    try:
        parser = _get_parser(language)
        tree = parser.parse(source.encode("utf-8"))
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
            
            # 1. Capture module-level code (NOT covered by any definition)
            module_parts = []
            module_start_line = None
            for child in root.children:
                if is_covered(child):
                    continue
                text = source[child.start_byte:child.end_byte].strip()
                if text and len(text) >= config.min_chunk_size:
                    if module_start_line is None:
                        module_start_line = child.start_point[0] + 1
                    module_parts.append(text)
            
            if module_parts:
                mod_content = "\n\n".join(module_parts)
                if len(mod_content) >= config.min_chunk_size:
                    chunks.append(CodeChunk(
                        file_path=file_path,
                        language=language,
                        symbol="<module>",
                        content=mod_content,
                        start_line=module_start_line or 1,
                        end_line=root.child_count and root.children[-1].end_point[0] + 1 or 1,
                        metadata={"type": "module_level"},
                    ))
            
            # 2. Capture each top-level definition as a chunk
            for symbol_name, node in defs:
                content = source[node.start_byte:node.end_byte]
                if len(content) < config.min_chunk_size:
                    continue
                chunks.append(CodeChunk(
                    file_path=file_path,
                    language=language,
                    symbol=symbol_name,
                    content=content,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    metadata={"type": "definition"},
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


def walk_repository(repo_path: str, config: ParseConfig) -> list[str]:
    """Walk a repository and return list of parseable file paths."""
    files = []
    repo = Path(repo_path).resolve()
    
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in sorted(dirs) if d not in config.skip_dirs 
                   and not d.startswith(".")]
        
        for fname in sorted(filenames):
            fpath = os.path.join(root, fname)
            ext = Path(fname).suffix
            if ext in config.supported_extensions:
                files.append(fpath)
    
    return files
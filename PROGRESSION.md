# Codebase-Skill — Progression

## Status Legend: [ ] Todo | [~] In Progress | [x] Done | [!] Blocked

## Phase 1: Infrastructure
- [x] Python venv + dépendances (tree-sitter 0.21.3, psycopg, pgvector, fastapi, httpx)
- [x] PostgreSQL DB `codeindex` avec user `codeindex`
- [x] Extension pgvector 0.6.0 activée
- [x] Tables créées (code_chunks, projects + 8 index HNSW)

## Phase 2: Core Modules
- [x] config.py — Configuration (DB, embed, parse, API) via env vars
- [x] parser.py — Tree-sitter chunking par symbole, 25 langues
- [x] embedder.py — Ollama nomic-embed-text 768-dim, retry 3x, truncation 32k, sleep 0.5s
- [x] indexer.py — Indexation repo + incrémental, insert pgvector
- [x] search.py — Recherche cosine + filtres (lang, file_pattern, repo_path)

## Phase 3: Interface
- [x] api.py — FastAPI server + MCP tool defs
- [x] cli.py — CLI terminal (index, search, file-context, stats, remove, serve)
- [x] init_db.sql + setup_db.sh — Schéma DB + script init
- [x] SKILL.md — Skill Hermes Agent
- [x] .gitignore

## Phase 4: Validation
- [x] Test embedding Ollama — nomic-embed-text 768-dim, similarités cohérentes
- [x] Test indexation codebase-skill — 49 chunks, embeddings valides
- [x] Test recherche sémantique — "FastAPI endpoint" -> api.py (0.697)
- [x] Test API FastAPI — /health OK, /stats OK, /search OK
- [x] Test indexation AIssistant — 83 fichiers, 502 chunks, 5 langues (14.5 min, erreurs Ollama 500 retryées)
- [x] Test get_file_context — config.yaml + focus TTS => chunks pertinents (0.566)
- [x] Test indexation incrémentale — 3 fichiers modifiés => 5 chunks retraités

## Phase 5: Polish
- [x] Fix: import json manquant dans indexer.py
- [x] Embedder: retry 3x + backoff exponentiel + sleep 0.5s + timeout 30s
- [x] Embedder: truncation textes > 32k chars
- [x] Fichiers test temporaires supprimés
- [x] Indexer: progress print par batch

## Problèmes connus
- Ollama 500 intermittents sur /api/embeddings — ralentit l'indexation gros repos
- tree-sitter 0.21.x émet FutureWarning (sans impact, cf. tree-sitter-languages compat)
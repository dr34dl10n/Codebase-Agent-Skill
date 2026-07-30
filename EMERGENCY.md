# EMERGENCY.md — Défauts critiques constatés sur le codebase-skill (pgvector)

> Contexte : lors de la reconstruction du projet `ai-recruiter` (dossier perdu),
> la base pgvector `codeindex` a été utilisée comme source de récupération.
> Plusieurs défauts majeurs ont été identifiés dans l'indexeur/parser, qui
> rendent la reconstruction de fichiers depuis les chunks **non fiable**.
>
> Date des constats : 2026-07-30. Données indexées : 2026-07-19.

---

## BUG #1 — CRITIQUE : Byte offsets vs character offsets (root cause)

**Fichier :** `parser.py`, fonction `parse_file()`

**Problème :**
```python
source = path.read_text(errors="replace")          # → str (caractères)
tree = parser.parse(source.encode("utf-8"))         # tree-sitter parse les BYTES
content = source[node.start_byte:node.end_byte]     # ❌ byte offsets sur str
```

`node.start_byte` et `node.end_byte` sont des **byte offsets** (tree-sitter
travaille sur la représentation UTF-8). Mais `source` est un `str` Python
(indexé par **caractères**). Quand le fichier contient des caractères
multi-octets (`é`, `—`, `→`, `═`, `«`, `»`, emojis…), les byte offsets
divergent des character offsets.

**Conséquence :** Le slice `source[byte_start:byte_end]` coupe le contenu au
mauvais endroit. Plus il y a de caractères multi-octets avant la définition,
plus le début du chunk est tronqué. Le contenu est **décalé** : il manque des
caractères en début de chunk ET il inclut du code qui vient APRÈS la fin réelle
du nœud tree-sitter.

**Exemples constatés dans `src/models.py` :**
- `class PositionInput(BaseModel):` → chunk commence par `"Full job description…`
- `def init_db(db_url: str…)` → chunk commence par `db_url: str…`
- Chunk `CandidateProfile` (lines 135-186) inclut le contenu de `ConsentLog`,
  `AccessLog` et le début de `MatchRequest` — code qui est en réalité APRÈS
  la classe CandidateProfile.

**Fix :**
```python
source_bytes = path.read_bytes()
source = source_bytes.decode("utf-8", errors="replace")
# ...
content = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
```

Ou de façon équivalente :
```python
content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
```

**Impact :** Tous les chunks de définition (function/class) pour les fichiers
contenant des caractères multi-octets sont corrompus. C'est le défaut le plus
grave — il affecte la quasi-totalité des fichiers Python du projet
(docstrings avec `—`, `é`, `→`, commentaires avec `═`, etc.).

**Note :** Les `start_line` et `end_line` (de `node.start_point[0]` et
`node.end_point[0]`) sont **corrects** car ce sont des numéros de ligne, pas
des offsets. Le `symbol` est aussi correct (extrait du nœud tree-sitter).
Seul le `content` est corrompu.

---

## BUG #2 — `<module>` chunk non-contigu (impossible à reconstruire)

**Fichier :** `parser.py`, fonction `parse_file()`, bloc "Capture module-level code"

**Problème :**
```python
module_parts = []
for child in root.children:
    if is_covered(child):
        continue
    text = source[child.start_byte:child.end_byte].strip()  # bug #1 + .strip()
    if text and len(text) >= config.min_chunk_size:
        module_parts.append(text)
mod_content = "\n\n".join(module_parts)  # ❌ joint des fragments non-contigus
```

Le chunk `<module>` est construit en joignant tous les fragments de code
module-level (imports, constantes, try/except, commentaires entre defs) avec
`"\n\n"`. Ces fragments ne sont **pas contigus** dans le fichier source — ils
sont séparés par les définitions (class/function) qui sont exclues.

**Conséquences :**
- Le contenu est un **collage** de fragments non-adjacents, sans relation avec
  l'ordre réel des lignes.
- `start_line` correspond au premier fragment, mais le reste du contenu vient
  de lignes arbitraires plus loin dans le fichier.
- Les fragments sont **intervertis** avec du code de définition (banners de
  commentaires qui précèdent une classe apparaissent accolés à des imports
  d'une autre section).
- `.strip()` supprime l'indentation et la structure de ligne de chaque
  fragment.
- Le `min_chunk_size` filter **dropping** les petits fragments (ex. `import os`
  seul sur une ligne) → imports manquants.

**Fix suggéré :**
- Stocker le code module-level **contigu** (du début du fichier jusqu'à la
  première définition, plus les gaps entre définitions) en préservant les
  numéros de ligne.
- Ou mieux : ajouter une colonne `raw_source` / une table `file_sources` qui
  stocke le contenu intégral du fichier (voir BUG #4).

---

## BUG #3 — `.strip()` sur les fragments module-level

**Fichier :** `parser.py`, ligne `text = source[child.start_byte:child.end_byte].strip()`

Le `.strip()` supprime whitespace de tête et de queue sur chaque fragment
module-level. Pour du code module-level (pas d'indentation), l'impact principal
est la perte des lignes vides et de la structure entre fragments. Combiné avec
le bug #2, cela rend les fragments impossibles à réassembler dans l'ordre.

**Fix :** Ne pas `.strip()` les fragments module-level, ou préserver les
numéros de ligne originaux pour chaque fragment.

---

## BUG #4 — Pas de stockage du source brut (no ground truth)

**Problème :** Il n'y a aucune table ou colonne stockant le contenu intégral
des fichiers indexés. Quand les chunks sont corrompus (bugs #1, #2), il n'y a
**aucun fallback** pour reconstruire le fichier original.

**Fix suggéré :** Ajouter une table `file_sources` :
```sql
CREATE TABLE IF NOT EXISTS file_sources (
    file_path TEXT NOT NULL,
    content TEXT NOT NULL,
    language TEXT,
    indexed_at TIMESTPTZ DEFAULT now(),
    PRIMARY KEY (file_path)
);
```
Ou ajouter une colonne `raw_content` au niveau du projet. Cela permettrait
de toujours retrouver le source exact, même si le chunking a des bugs.

---

## BUG #5 — `min_chunk_size` drope les petits fragments module-level

**Fichier :** `parser.py`, `if text and len(text) >= config.min_chunk_size:`

Les fragments module-level plus petits que `min_chunk_size` sont silencieusement
droppés. Pour un fichier avec plusieurs imports courts séparés par des defs,
cela peut perdre des imports entiers (ex. `import os`, `import sys`).

**Fix :** Pour les fragments module-level, ne pas appliquer `min_chunk_size`,
ou utiliser un seuil beaucoup plus bas (ex. 1 caractère).

---

## BUG #6 — `summary` toujours NULL

**Fichier :** `indexer.py`, `_embed_and_store()`

```python
chunk.metadata.get("summary")  # → toujours None car metadata ne contient jamais "summary"
```

Le champ `summary` dans la DB est toujours NULL. La colonne `metadata` contient
`{"type": "module_level"}` ou `{"type": "definition"}` mais jamais de `summary`.
Si l'embedding est supposé utiliser un summary pour améliorer la recherche
sémantique, ce n'est jamais fait.

**Fix :** Soit générer un summary (ex. docstring de la fonction/classe), soit
retirer la colonne `summary` si elle n'est pas utilisée.

---

## BUG #7 — `upsert_code_chunk` function existe mais n'est pas utilisée

**Fichier :** `init_db.sql` définit `upsert_code_chunk()`, mais `indexer.py`
utilise un `INSERT` direct sans `ON CONFLICT`. Conséquence : les
re-indexations font `DELETE` puis `INSERT` (via `_delete_file_chunks`), ce qui
change les IDs et est moins efficace. La function SQL est dead code.

**Fix :** Soit utiliser `upsert_code_chunk()` dans l'indexer, soit la supprimer
de `init_db.sql`.

---

## PRIORITÉ DE RÉPARATION

| # | Sévérité | Impact | Effort |
|---|----------|--------|--------|
| 1 | **CRITIQUE** | Tous les chunks de def sont corrompus si ≥1 char multi-octet | 1 ligne |
| 4 | **HAUTE** | Pas de fallback pour reconstruction | Moyen |
| 2 | **HAUTE** | Module chunk inutilisable pour reconstruction | Moyen |
| 5 | **MOYENNE** | Imports/constantes perdues | 1 ligne |
| 3 | **MOYENNE** | Structure perdue | 1 ligne |
| 6 | **BASSE** | summary inutilisé | Faible |
| 7 | **BASSE** | Dead code | Faible |

**Action immédiate recommandée :** Fixer le BUG #1 (1 ligne de code) et
re-indexer tous les projets. C'est le défaut qui rend les données actuellement
non fiables pour la reconstruction de fichiers.
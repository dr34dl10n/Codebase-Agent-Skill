# Accélération de l'indexation

Diagnostic des goulots d'étranglement dans le pipeline d'indexation
(`cli.py index` → `indexer.py` → `parser.py` + `embedder.py` → pgvector)
et leviers d'amélioration.

Contexte machine de référence : 4 cœurs CPU, **pas de GPU**, 23 GB RAM.
Backend réel : `sentence_transformers` in-process (ModernBERT), **pas Ollama**
(Ollama tourne mais sans modèle chargé). Sur CPU sans GPU, l'embedding est
le coût dominant.

---

## ⚠️ Diagnostic réel (mesuré, pas supposé)

Les phases walk/parse/store sont **négligeables** :
- Walk 27–202 fichiers : **0.01–0.5 s**
- Parse 144–1681 chunks : **0.06–0.35 s**
- Store (inserts 1-par-1) : **~0.5 %** du total

Le goulot est **l'embedding** (~99 % du temps). Mais la cause racine n'est
**pas** le backend Ollama (comme supposé dans la première version de ce
doc) — c'est la **longueur des chunks** :

| Taille chunk (chars) | Débit embedding |
|----------------------|-----------------|
| 483  | 5.65 chunks/s |
| 1321 (moy. réelle) | 2.07 chunks/s |
| 4000 | 0.59 chunks/s |
| 11791 | 0.13 chunks/s (~7.5 s pour 1 chunk) |
| 21375 | ~15–30 s pour 1 chunk |
| 46169 | ~60 s pour 1 chunk |

Le temps d'embedding de ModernBERT sur CPU croît **super-linéairement** avec
la longueur des séquences, ET le **padding intra-batch** est tiré par la plus
longue séquence du batch.

**Cause racine dans le parser** : `parse_file` extrayait les définitions
(class/function) **entières** sans borne de taille. `max_chunk_size` n'était
appliqué qu'au fallback `_chunk_by_lines`. Conséquence mesurée :
- sc-companion : top5 = 21375 / 16442 / 15889 / 14612 / 14032 chars, 467 chunks > 2000.
- ai-recruiter : chunks de **46169** et 30029 chars.

Avec un seul chunk de 21375 chars à ~20 s, et 467 chunks > 2000 chars, une
indexation de 1681 chunks prenait **des heures à des jours** — cohérent avec
le constat « 200 chunks en une nuit ».

---

## ✅ Corrections appliquées

### P0 — Borner la taille des chunks (LE levier dominant)
`parser.py::_enforce_max_size` + `_split_definition` : une passe finale
découpe **tout** chunk (définition, run module-level, line-based) dépassant
`max_chunk_size` en sous-chunks contigus verbatim, par lignes. Les numéros de
ligne sont préservés (reconstruction toujours possible — voir EMERGENCY.md).
Le premier sous-chunk garde le nom du symbole ; les suivants sont suffixés
`#part2`, `#part3`, ...

Résultat : max chunk 21375 → **3556** (sc-companion), 46169 → **2304**
(ai-recruiter). Plus aucun chunk > 8000.

### P1 — Trier par longueur avant embedding
`embedder.py::SentenceTransformerProvider.embed` : trie les textes par
longueur avant l'`encode()`, puis réordonne les vecteurs. Comme
sentence-transformers padde chaque batch à la longueur du plus long, grouper
les textes de taille similaire réduit drastiquement le padding. Mesuré
**~1.7×** sur l'échantillon dur.

### P2 — Cap du texte embeddé à 1200 chars
`config.py::_MODEL_MAX_TEXT` (modernbert) 32768 → **1200**. Le **content
stocké reste complet** (reconstruction intacte) ; seul le texte nourri à
l'embedder est tronqué au début (signature + docstring + début du corps = le
plus sémantiquement riche). Garde-fou contre les lignes uniques trop longues
(JSON minifié, longs paragraphes markdown) que le split par lignes ne coupe
pas. Mesuré ~2× sur les longs chunks.

### P3 — batch_size 16
Optimal **avec le tri** (16 > 32 mesuré), contrairement à l'intuition
initiale. `config.py::EmbedConfig.batch_size = 16`.

### P4 — Timers par phase
`indexer.py::_embed_and_store` et `index_repository` chronomêtrent
walk/parse/embed/store et les exposent dans `stats["phase_seconds"]`.
Indispensable pour ne plus optimiser à l'aveugle.

### Mesures de bout en bout (codebase-skill, 183 chunks, force)
| Phase | Avant | Après |
|-------|-------|-------|
| embed | > 300 s (timeout) | ~150 s |
| total | (jamais terminé sur sc-companion) | ~150 s |

Débit stable mesuré ~1.2–1.7 chunks/s. sc-companion (1681 chunks, anciennement
astronomique) → ~18 min estimé.

---

## Leviers écartés (mesurés inutiles ou instables)

- **P3 ACCELERATION initial (process pool parsing)** : le parsing prend 0.06–0.35 s.
  Paralléliser = overhead inutile.
- **P4 ACCELERATION initial (COPY/executemany DB)** : le store fait <1 % du temps.
  Gain négligeable. Reste à faire si un très gros repo le justifie.
- **ONNX Runtime** : testé (`sentence-transformers[onnx]` + `optimum[onnxruntime]`).
  L'export ONNX de ModernBERT échoue (`KeyError: 'last_hidden_state'`) avec la
  stack actuelle (ST 5.6 / transformers 5.14). Trop instable sur ce modèle
  récent. À retenter quand l'écosystème ONNX supportera proprement ModernBERT.
  `onnxruntime` est installé dans le venv mais inutilisé (inoffensif).
- **P1/P2 ACCELERATION initial (Ollama /api/embed + thread pool)** : le backend
  réel est sentence_transformers, pas Ollama. Non applicable ici.

## Leviers restants (si besoin de plus)

- **GPU** : seul vrai multiplicateur matériel (~10–50×). Pas applicable ici.
- **Modèle plus petit** : si la qualité de modernbert-embed-base n'est pas
  critique, un modèle plus léger augmenterait le débit. À évaluer.
- **ONNX** : à retenter plus tard (voir ci-dessus).
- **Réduire `max_text_len` à 800** : 2.36 chunks/s (vs 1.74 à 1200). Trades
  un peu de recall pour ~1.35× de débit. Ajustable via env
  `CODEINDEX_EMBED_MAX_TEXT_LEN` si ajouté (TODO : exposer).
- **Ne jamais lancer 2 index en parallèle** sur 4 cœurs (concurrence de
  process → oversubscription). Discipline opérationnelle.
"""Embedding providers — sentence-transformers (direct Python) or Ollama API.

Factory pattern: create_provider(config) returns the right backend.
  - SentenceTransformerProvider: loads model directly via HuggingFace (ModernBERT).
    This is the default and recommended backend — zero external dependencies.
  - OllamaProvider: HTTP client to /api/embeddings (nomic-embed-text, cloud endpoints).
    Advanced option for setups that already run Ollama with a GPU.

Swap models/providers by changing CODEINDEX_EMBED_MODEL and CODEINDEX_EMBED_BACKEND.
"""

import json
import logging
import time
from typing import Optional

from config import EmbedConfig, _HF_MODEL_IDS

logger = logging.getLogger(__name__)


class EmbeddingProvider:
    """Base class for embedding providers."""

    def __init__(self, config: Optional[EmbedConfig] = None):
        self.config = config or EmbedConfig()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of float vectors."""
        raise NotImplementedError

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text. Returns one float vector."""
        result = self.embed([text])
        return result[0] if result else []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class OllamaProvider(EmbeddingProvider):
    """Generate embeddings via Ollama-compatible /api/embeddings endpoint."""

    def __init__(self, config: Optional[EmbedConfig] = None):
        super().__init__(config)
        import httpx
        self._client = httpx.Client(
            base_url=self.config.api_base,
            timeout=30.0,
        )
        self._max_text_len = self.config.max_text_len

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        all_embeddings = []
        for i in range(0, len(texts), self.config.batch_size):
            batch = texts[i:i + self.config.batch_size]
            batch_embeddings = self._embed_batch(batch)
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        embeddings = []
        for text in texts:
            if len(text) > self._max_text_len:
                text = text[:self._max_text_len]
            for attempt in range(3):
                try:
                    resp = self._client.post("/api/embeddings", json={
                        "model": self.config.model,
                        "prompt": text,
                    })
                    resp.raise_for_status()
                    data = resp.json()
                    emb = data.get("embedding", [])
                    if emb and len(emb) == self.config.dim:
                        embeddings.append([float(v) for v in emb])
                    else:
                        logger.warning(f"Bad embedding dim={len(emb)}, expected {self.config.dim}")
                        embeddings.append([0.0] * self.config.dim)
                    break
                except Exception as e:
                    time.sleep(0.5)
                    if attempt < 2:
                        wait = 2 ** (attempt + 1)
                        logger.warning(f"Embedding attempt {attempt+1} failed: {e}, retrying in {wait}s")
                        time.sleep(wait)
                    else:
                        logger.error(f"Embedding failed after 3 attempts: {e}")
                        embeddings.append([0.0] * self.config.dim)
        return embeddings

    def close(self):
        self._client.close()


class SentenceTransformerProvider(EmbeddingProvider):
    """Generate embeddings directly via sentence-transformers (HuggingFace models).

    No API server needed — model downloads and runs in-process.
    Best for ModernBERT models (modernbert-embed-base, modernbert-embed-large).
    """

    def __init__(self, config: Optional[EmbedConfig] = None):
        super().__init__(config)
        hf_id = _HF_MODEL_IDS.get(self.config.model)
        if not hf_id:
            # Allow passing full HuggingFace model IDs directly
            if "/" in self.config.model:
                hf_id = self.config.model
            else:
                raise ValueError(
                    f"Unknown model '{self.config.model}' for sentence_transformers backend. "
                    f"Known models: {list(_HF_MODEL_IDS.keys())}. "
                    f"Or pass a full HuggingFace model ID (e.g. 'org/model-name')."
                )

        logger.info(f"Loading sentence-transformers model: {hf_id}")
        t0 = time.time()
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(hf_id)
        self._max_text_len = self.config.max_text_len
        elapsed = time.time() - t0
        logger.info(f"Model loaded in {elapsed:.1f}s — dim={self.config.dim}, "
                    f"max_seq_length={self._model.max_seq_length}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Truncate texts that exceed model context
        truncated = []
        for t in texts:
            if len(t) > self._max_text_len:
                t = t[:self._max_text_len]
            truncated.append(t)

        # Encode in batches (sentence-transformers handles batching internally)
        results = []
        for i in range(0, len(truncated), self.config.batch_size):
            batch = truncated[i:i + self.config.batch_size]
            embeddings = self._model.encode(
                batch,
                batch_size=len(batch),
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            results.extend(embeddings.tolist())

        return results

    def close(self):
        # Nothing to close — model is in-process
        pass


def create_provider(config: Optional[EmbedConfig] = None) -> EmbeddingProvider:
    """Factory: return the right embedding provider based on config.backend."""
    config = config or EmbedConfig()

    if config.backend == "sentence_transformers":
        return SentenceTransformerProvider(config)
    elif config.backend == "ollama":
        return OllamaProvider(config)
    else:
        raise ValueError(
            f"Unknown embedding backend '{config.backend}'. "
            f"Use 'ollama' or 'sentence_transformers'."
        )
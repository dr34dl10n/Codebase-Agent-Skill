"""Embedding provider — Ollama-compatible API (works with any local or cloud endpoint).

Batch-aware: embeds multiple texts per API call for efficiency.
Swap models/providers by changing CODEINDEX_EMBED_MODEL and CODEINDEX_EMBED_API_BASE.
"""

import json
import logging
from typing import Optional

import httpx

from config import EmbedConfig

logger = logging.getLogger(__name__)


class EmbeddingProvider:
    """Generate embeddings via Ollama-compatible /api/embeddings endpoint."""

    def __init__(self, config: Optional[EmbedConfig] = None):
        self.config = config or EmbedConfig()
        self._client = httpx.Client(
            base_url=self.config.api_base,
            timeout=30.0,
        )
        self._max_text_len = 32000  # ~8k tokens for nomic-embed-text

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of float vectors."""
        if not texts:
            return []

        all_embeddings = []
        # Process in batches
        for i in range(0, len(texts), self.config.batch_size):
            batch = texts[i:i + self.config.batch_size]
            batch_embeddings = self._embed_batch(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text. Returns one float vector."""
        result = self.embed([text])
        return result[0] if result else []

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a single batch via /api/embeddings endpoint."""
        import time
        embeddings = []
        for text in texts:
            # Truncate text if too long for the model
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
                        # Ensure all values are float — some models return ints
                        embeddings.append([float(v) for v in emb])
                    else:
                        logger.warning(f"Bad embedding dim={len(emb)}, expected {self.config.dim}")
                        embeddings.append([0.0] * self.config.dim)
                    break  # success
                except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.HTTPError, json.JSONDecodeError) as e:
                    # On 500, the embedding service might be overloaded — wait longer before retry
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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
"""Sentence embeddings for the dense half of hybrid search.

Default: sentence-transformers all-MiniLM-L6-v2 (local, ~90 MB, downloaded on first run).
If the model cannot be loaded (no internet on first run), a hashing embedder is used so
the app still works; /health reports which backend is active.
"""

import logging

import numpy as np

from app import config

log = logging.getLogger(__name__)


class HashingEmbedder:
    """Offline fallback: word 1-2 grams + character 3-5 grams hashed into 1024 dims."""

    name = "hashing-1024"

    def __init__(self):
        from sklearn.feature_extraction.text import HashingVectorizer

        self.words = HashingVectorizer(n_features=512, ngram_range=(1, 2), alternate_sign=False, norm=None,
                                       stop_words="english")
        self.chars = HashingVectorizer(n_features=512, analyzer="char_wb", ngram_range=(3, 5),
                                       alternate_sign=False, norm=None)

    def encode(self, texts):
        mat = np.hstack([self.words.transform(texts).toarray(), 0.5 * self.chars.transform(texts).toarray()])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return (mat / np.where(norms == 0, 1, norms)).astype("float32")


class MiniLMEmbedder:
    def __init__(self, model_name):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.name = model_name.split("/")[-1]

    def encode(self, texts):
        vecs = self.model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype="float32")


_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        if config.EMBED_BACKEND == "hashing":
            _embedder = HashingEmbedder()
        else:
            try:
                _embedder = MiniLMEmbedder(config.EMBED_MODEL)
            except Exception as exc:  # model download blocked / not installed
                if config.EMBED_BACKEND == "minilm":
                    raise
                log.warning("MiniLM unavailable (%s); using the hashing embedder.", exc)
                _embedder = HashingEmbedder()
    return _embedder

"""
Unit tests for semantic_relevance_service (opt-in embedding news relevance).

Network-free: the embedding client is mocked. Covers the cosine helper, the
opt-in no-op (disabled / no articles), in-place annotation with negative-cosine
clamping, and best-effort fallback on embedding failure.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import semantic_relevance_service as srs


def _embed_response(vectors):
    return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])


class CosineTests(unittest.TestCase):
    def test_basic_values(self):
        self.assertAlmostEqual(srs._cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(srs._cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertAlmostEqual(srs._cosine([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_zero_or_mismatched_vectors(self):
        self.assertEqual(srs._cosine([0.0, 0.0], [1.0, 1.0]), 0.0)
        self.assertEqual(srs._cosine([], [1.0]), 0.0)
        self.assertEqual(srs._cosine([1.0], [1.0, 0.0]), 0.0)


class AnnotateTests(unittest.TestCase):
    def test_noop_when_disabled(self):
        articles = [{"title": "x", "description": "y"}]
        with patch.object(srs.settings, "EMBEDDING_MODEL", ""):
            out = asyncio.run(srs.annotate_semantic_relevance("q", articles))
        self.assertNotIn("semantic_relevance", out[0])

    def test_noop_when_no_articles(self):
        with patch.object(srs.settings, "EMBEDDING_MODEL", "m"):
            self.assertEqual(
                asyncio.run(srs.annotate_semantic_relevance("q", [])), []
            )

    def test_annotates_and_clamps_negative(self):
        # query=[1,0]; article0=[1,0] -> cos 1.0; article1=[-1,0] -> cos -1 -> 0.
        articles = [
            {"title": "relevant", "description": ""},
            {"title": "opposite", "description": ""},
        ]
        client = SimpleNamespace(
            embeddings=SimpleNamespace(
                create=AsyncMock(
                    return_value=_embed_response([[1.0, 0.0], [1.0, 0.0], [-1.0, 0.0]])
                )
            )
        )
        with patch.object(srs.settings, "EMBEDDING_MODEL", "m"), \
                patch.object(srs, "_embed_client", return_value=client):
            out = asyncio.run(srs.annotate_semantic_relevance("q", articles))
        self.assertEqual(out[0]["semantic_relevance"], 1.0)
        self.assertEqual(out[1]["semantic_relevance"], 0.0)

    def test_failure_is_noop(self):
        articles = [{"title": "x", "description": ""}]
        client = SimpleNamespace(
            embeddings=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("boom"))
            )
        )
        with patch.object(srs.settings, "EMBEDDING_MODEL", "m"), \
                patch.object(srs, "_embed_client", return_value=client):
            out = asyncio.run(srs.annotate_semantic_relevance("q", articles))
        self.assertNotIn("semantic_relevance", out[0])


if __name__ == "__main__":
    unittest.main()

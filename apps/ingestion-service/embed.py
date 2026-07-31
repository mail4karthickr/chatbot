# embed.py
import logging
import time
from functools import lru_cache

import numpy as np
from fastembed import SparseTextEmbedding
from openai import OpenAI

from config import get_settings

log = logging.getLogger("embed")

# text-embedding-3-small native dimension. Must match vectordb.DENSE_DIM;
# changing either invalidates every stored vector (reset + re-ingest).
DIM = 1536
_sparse = SparseTextEmbedding("Qdrant/bm25")


@lru_cache
def _client() -> OpenAI:
    # The SDK retries connection errors and 5xx twice with backoff by itself,
    # so no tenacity wrapper here (the old Jina path needed one).
    return OpenAI(api_key=get_settings().openai_api_key)


def _embed(texts: list[str]) -> list[list[float]]:
    """One batched embeddings call. OpenAI has no passage/query task split
    (symmetric encoding), so passages and queries share this path."""
    t0 = time.perf_counter()
    resp = _client().embeddings.create(model=get_settings().embed_model, input=texts)
    # Response order matches input order, but sort by index anyway — it's free
    # insurance against a silent corpus-wide scramble.
    data = sorted(resp.data, key=lambda d: d.index)
    log.info("embed model=%s inputs=%d took=%.2fs",
             get_settings().embed_model, len(texts), time.perf_counter() - t0)
    return [d.embedding for d in data]


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _embed(texts)


def embed_query(query: str) -> list[float]:
    return _embed([query])[0]


def embed_queries(queries: list[str]) -> list[list[float]]:
    """All query variants in ONE embeddings call — same latency as a single query."""
    return _embed(queries)


# NOTE: image-pixel embedding was removed on 2026-07-23 (plan step 4a).
# Image chunks are embedded by their pixels-only caption via embed_texts —
# the A/B in parsing_test_files/test_4a_embedding_ab.py showed the
# image+caption blend added nothing the reranker didn't erase. Pixels live
# in S3 only. That made the multimodal Jina embedder pointless, so on
# 2026-07-24 it was replaced with OpenAI text-embedding-3-small (one vendor,
# ~50× faster batches than Jina's shared endpoint). Revisit a multimodal
# embedder (named-vectors strategy) if chart-dense documents arrive whose
# captions can't carry the visual content.

def embed_sparse(texts: list[str]):
    return list(_sparse.embed(texts))                       # BM25 SparseEmbedding objects


if __name__ == "__main__":
    def _summary(v: list[float]) -> str:
        arr = np.array(v)
        return f"dim={len(v)}, norm={np.linalg.norm(arr):.4f}, head={[round(x, 4) for x in v[:3]]}"

    results: dict[str, str] = {}

    def _run(name: str, fn):
        print(f"→ {name}")
        try:
            fn()
            results[name] = "PASS"
        except Exception as e:
            results[name] = f"FAIL: {type(e).__name__}: {e}"
            print(f"   ✗ {results[name]}")

    def _test_embed_texts():
        v = embed_texts(["hello world", "multimodal rag"])
        print(f"   got {len(v)} vectors; [0] {_summary(v[0])}")
        assert len(v) == 2 and len(v[0]) == DIM, f"expected 2×{DIM}"

    def _test_embed_query():
        v = embed_query("what is retrieval augmented generation?")
        print(f"   {_summary(v)}")
        assert len(v) == DIM

    def _test_embed_queries():
        v = embed_queries(["what is the premium?", "premium amount payable"])
        print(f"   got {len(v)} vectors; [0] {_summary(v[0])}")
        assert len(v) == 2 and len(v[0]) == DIM

    def _test_embed_semantics():
        """Related texts must score closer than unrelated ones — catches a
        scrambled batch or a broken model, not just a 200 OK."""
        a, b, c = embed_texts([
            "The mitochondrion produces ATP in the cell.",
            "Cellular respiration generates ATP inside mitochondria.",
            "Football is played with a spherical ball.",
        ])
        rel   = float(np.dot(a, b))
        unrel = float(np.dot(a, c))
        print(f"   related={rel:.4f} unrelated={unrel:.4f}")
        assert rel > unrel, "related pair scored below unrelated pair"

    def _test_embed_sparse():
        v = embed_sparse(["hello world", "multimodal rag"])
        for i, s in enumerate(v):
            print(f"   [{i}] nnz={len(s.indices)}, head_idx={list(s.indices[:3])}, head_val={[round(float(x), 4) for x in s.values[:3]]}")

    _run("embed_texts",                            _test_embed_texts)
    _run("embed_query",                            _test_embed_query)
    _run("embed_queries (batch)",                  _test_embed_queries)
    _run("embed semantics (sanity)",               _test_embed_semantics)
    _run("embed_sparse",                           _test_embed_sparse)

    print()
    print("== summary ==")
    for name, status in results.items():
        print(f"  {name:45s} {status}")
    print()
    if all(v == "PASS" for v in results.values()):
        print("OK — all embed functions returned successfully")
    else:
        print("Some functions failed — see summary above")

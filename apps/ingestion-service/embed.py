# embed.py
import logging
import time
from functools import lru_cache
import requests
import numpy as np
from fastembed import SparseTextEmbedding
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from config import get_settings

log = logging.getLogger("embed")

_JINA_URL = "https://api.jina.ai/v1/embeddings"
DIM = 1024
_sparse = SparseTextEmbedding("Qdrant/bm25")


@lru_cache
def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().jina_api_key}",
        "Content-Type": "application/json",
    }


def _is_transient(exc: BaseException) -> bool:
    """Retry on network hiccups and 5xx from Jina; fail fast on 4xx (auth,
    bad request — those need a code fix, not more attempts)."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code >= 500
    return False


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(min=2, max=30),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def _jina(inputs: list[dict], task: str) -> list[list[float]]:
    """inputs: list of {"text": ...} and/or {"image": <base64>} items."""
    t0 = time.perf_counter()
    r = requests.post(
        _JINA_URL,
        headers=_headers(),
        # 180s: text batches of ~50 chunks routinely take 30-90s on Jina's
        # shared endpoint. Was 60s and timing out mid-batch.
        timeout=180,
        json={
            "model": "jina-embeddings-v4",
            "dimensions": DIM,
            "task": task,
            "normalized": True,
            "embedding_type": "float",
            "input": inputs
        }
    )
    r.raise_for_status()
    log.info("jina task=%s inputs=%d took=%.2fs",
             task, len(inputs), time.perf_counter() - t0)
    return [d["embedding"] for d in r.json()["data"]]

def embed_texts(texts: list[str]) -> list[list[float]]:
    return _jina([{"text": t} for t in texts], task="retrieval.passage")

def embed_query(query: str) -> list[float]:
    return _jina([{"text": query}], task="retrieval.query")[0]

def embed_queries(queries: list[str]) -> list[list[float]]:
    """All query variants in ONE Jina call — same latency as a single query."""
    return _jina([{"text": q} for q in queries], task="retrieval.query")

# NOTE: image-pixel embedding was removed on 2026-07-23 (plan step 4a).
# Image chunks are embedded by their pixels-only caption via embed_texts —
# the A/B in parsing_test_files/test_4a_embedding_ab.py showed the
# image+caption blend added nothing the reranker didn't erase. Pixels live
# in S3 only. Revisit (named-vectors strategy) if chart-dense documents
# arrive whose captions can't carry the visual content.

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

    def _test_embed_query():
        v = embed_query("what is retrieval augmented generation?")
        print(f"   {_summary(v)}")

    def _test_embed_queries():
        v = embed_queries(["what is the premium?", "premium amount payable"])
        print(f"   got {len(v)} vectors; [0] {_summary(v[0])}")

    def _test_embed_sparse():
        v = embed_sparse(["hello world", "multimodal rag"])
        for i, s in enumerate(v):
            print(f"   [{i}] nnz={len(s.indices)}, head_idx={list(s.indices[:3])}, head_val={[round(float(x), 4) for x in s.values[:3]]}")

    _run("embed_texts",                            _test_embed_texts)
    _run("embed_query",                            _test_embed_query)
    _run("embed_queries (batch)",                  _test_embed_queries)
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

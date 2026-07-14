# rag.py
"""Retrieval-only: return reranked text chunks + relevant image references.

Generation lives in the agent-service; this module is deliberately LLM-free.
"""
import logging
import time
from functools import lru_cache

import torch
from sentence_transformers import CrossEncoder

from storage import presigned_url
from vectordb import hybrid_search

log = logging.getLogger("rag")
user_log = logging.getLogger("user")  # friendly per-stage progress for the UI


IMG_SCORE_THRESHOLD = 0.30   # tune on your golden set
# Candidate pool passed to the reranker. Rerank cost is roughly linear in this
# number; 25 keeps quality (top-8 stays stable in practice) while dropping
# CrossEncoder work by ~50% vs the previous 50.
RERANK_CANDIDATE_POOL = 25


def _pick_device() -> str:
    """Prefer GPU when available. On Apple Silicon this is a 3–5× speedup for
    the CrossEncoder vs CPU."""
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache
def _reranker() -> CrossEncoder:
    device = _pick_device()
    log.info("loading reranker device=%s", device)
    return CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512, device=device)


def warmup() -> None:
    """Force the reranker to load + JIT compile at import/startup time so the
    first user query doesn't pay the model-load penalty (multi-second first hit)."""
    t0 = time.perf_counter()
    _reranker().predict([("warmup", "warmup")])
    log.info("reranker warmup took=%.2fs", time.perf_counter() - t0)


def _rerank_all(query: str, points, top_n: int = 8):
    """Score text chunks and image candidates in a single CrossEncoder batch.

    Image candidates are harvested from ALL Qdrant candidates, not just the
    text top-N. This widens image recall AND removes the ordering dependency
    that used to force two .predict() calls. Image chunks that are direct hits
    reuse their text-pair score instead of being scored twice.
    """
    if not points:
        return [], []

    image_candidates: dict[str, dict] = {}   # image_key -> {"caption", "via"}
    direct_score_idx: dict[str, int] = {}    # image_key -> index into points[]
    for i, point in enumerate(points):
        p = point.payload
        if p["kind"] == "image" and p.get("image_key"):
            image_candidates.setdefault(p["image_key"], {"caption": p["text"], "via": "direct"})
            direct_score_idx.setdefault(p["image_key"], i)
        for k in p.get("linked_image_keys", []) or []:
            image_candidates.setdefault(k, {"caption": "", "via": "linked"})

    text_pairs = [(query, p.payload["text"]) for p in points]
    linked_only_keys = [k for k in image_candidates if k not in direct_score_idx]
    image_pairs = [(query, image_candidates[k]["caption"] or k) for k in linked_only_keys]

    user_log.info(
        "Reranking %d text + %d image pair%s on %s",
        len(text_pairs), len(image_pairs), "" if len(image_pairs) == 1 else "s",
        _pick_device(),
    )
    all_scores = _reranker().predict(text_pairs + image_pairs)
    text_scores = all_scores[: len(text_pairs)]
    linked_scores = all_scores[len(text_pairs):]

    reranked = sorted(zip(points, text_scores), key=lambda x: x[1], reverse=True)
    reranked = [(p, float(s)) for p, s in reranked[:top_n]]

    image_scores: dict[str, float] = {
        k: float(text_scores[i]) for k, i in direct_score_idx.items()
    }
    image_scores.update({k: float(s) for k, s in zip(linked_only_keys, linked_scores)})

    ranked_images = sorted(image_scores.items(), key=lambda x: x[1], reverse=True)
    image_hits = [
        (k, s, image_candidates[k]["caption"])
        for k, s in ranked_images
        if s >= IMG_SCORE_THRESHOLD
    ][:3]

    return reranked, image_hits


def retrieve(query: str, doc_ids=None, top_n: int = 8) -> dict:
    """Retrieve top passages + relevant image references for `query`.

    Returns:
        {
          "chunks": [{chunk_id, text, page, kind, score}, ...],
          "images": [{image_key, url, caption, score}, ...],
        }
    """
    t_total = time.perf_counter()
    short_q = (query[:80] + "…") if len(query) > 80 else query
    user_log.info("Searching for: %s", short_q)

    t0 = time.perf_counter()
    points   = hybrid_search(query, doc_ids=doc_ids, top_k=RERANK_CANDIDATE_POOL)
    t_search = time.perf_counter() - t0

    n_text  = sum(1 for p in points if (p.payload or {}).get("kind") == "text")
    n_image = sum(1 for p in points if (p.payload or {}).get("kind") == "image")
    user_log.info(
        "Vector search found %d candidate%s in %.2fs (%d text, %d image)",
        len(points), "" if len(points) == 1 else "s", t_search, n_text, n_image,
    )

    t0 = time.perf_counter()
    reranked, image_hits = _rerank_all(query, points, top_n=top_n)
    t_rerank = time.perf_counter() - t0

    total_s = time.perf_counter() - t_total
    user_log.info(
        "Retrieval complete in %.2fs — %d passage%s, %d image%s",
        total_s, len(reranked), "" if len(reranked) == 1 else "s",
        len(image_hits), "" if len(image_hits) == 1 else "s",
    )

    log.info("retrieve timing search=%.2fs rerank=%.2fs total=%.2fs points=%d reranked=%d images=%d",
             t_search, t_rerank, total_s, len(points), len(reranked), len(image_hits))

    chunks = [
        {
            "chunk_id": p.payload["chunk_id"],
            "text":     p.payload["text"],
            "page":     p.payload["page"],
            "kind":     p.payload["kind"],
            "score":    score,
        }
        for p, score in reranked
    ]
    images = [
        {"image_key": k, "url": presigned_url(k), "caption": caption, "score": score}
        for k, score, caption in image_hits
    ]
    return {
        "chunks": chunks,
        "images": images,
        "timing": {
            "search_ms": int(t_search * 1000),
            "rerank_ms": int(t_rerank * 1000),
            "total_ms":  int(total_s * 1000),
            "candidates": len(points),
            "chunks":     len(reranked),
            "images":     len(image_hits),
            "device":     _pick_device(),
        },
    }


if __name__ == "__main__":
    import uuid
    from types import SimpleNamespace

    def _mock_point(text: str, *, page=1, kind="text", chunk_id="c",
                    image_key=None, linked_image_keys=None):
        payload = {"text": text, "page": page, "kind": kind, "chunk_id": chunk_id}
        if image_key:
            payload["image_key"] = image_key
        if linked_image_keys:
            payload["linked_image_keys"] = linked_image_keys
        return SimpleNamespace(payload=payload)

    results: dict[str, str] = {}

    def _run(name: str, fn):
        print(f"→ {name}")
        try:
            fn()
            results[name] = "PASS"
        except Exception as e:
            results[name] = f"FAIL: {type(e).__name__}: {e}"
            print(f"   ✗ {results[name]}")

    def _test_reranker_singleton():
        r1 = _reranker()
        r2 = _reranker()
        assert r1 is r2, "singleton not cached"
        print(f"   {type(r1).__name__} loaded and cached")

    def _test_rerank_all_empty():
        reranked, images = _rerank_all("q", [])
        assert reranked == [] and images == []
        print("   empty points → ([], [])")

    def _test_rerank_all_text():
        points = [
            _mock_point("The mitochondrion is the powerhouse of the cell.", chunk_id="c1"),
            _mock_point("A random unrelated sentence about football.",       chunk_id="c2"),
            _mock_point("Cells convert glucose into ATP via cellular respiration.", chunk_id="c3"),
        ]
        reranked, images = _rerank_all("what produces ATP in a cell?", points, top_n=2)
        assert len(reranked) == 2 and images == []
        top_text = reranked[0][0].payload["text"]
        print(f"   top score={reranked[0][1]:.4f}: {top_text!r}")
        assert "ATP" in top_text or "mitochondrion" in top_text, "reranker put an unrelated hit on top"

    def _test_rerank_all_images():
        points = [
            _mock_point("The heart pumps blood.", kind="text",
                        linked_image_keys=["img/heart.png"], chunk_id="c1"),
            _mock_point("Diagram of a mitochondrion.", kind="image",
                        image_key="img/mito.png", chunk_id="c2"),
            _mock_point("Unrelated caption about football.", kind="image",
                        image_key="img/ball.png", chunk_id="c3"),
        ]
        _, images = _rerank_all("show me a diagram of the mitochondria", points)
        print(f"   {len(images)} image(s) passed threshold {IMG_SCORE_THRESHOLD}:")
        for key, score, caption in images:
            print(f"     - {key}: score={score:.4f} caption={caption!r}")

    def _test_retrieve_end_to_end():
        """Populates a throwaway Qdrant collection and runs retrieve() end-to-end."""
        import vectordb
        from qdrant_client import models as qm
        from embed import embed_texts, embed_sparse
        from unittest.mock import patch

        TEST_COLLECTION = "multimodal_rag_e2e_test"
        client = vectordb.get_client()

        if client.collection_exists(TEST_COLLECTION):
            client.delete_collection(TEST_COLLECTION)
        client.create_collection(
            collection_name=TEST_COLLECTION,
            vectors_config={"dense": qm.VectorParams(
                size=vectordb.DENSE_DIM, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams()},
        )

        texts = [
            "The mitochondrion is the powerhouse of the cell, producing ATP through cellular respiration.",
            "Photosynthesis converts light energy into chemical energy in plant chloroplasts.",
            "Football is a sport played with a spherical ball between two teams of eleven players.",
        ]
        dense_vecs  = embed_texts(texts)
        sparse_vecs = embed_sparse(texts)
        client.upsert(TEST_COLLECTION, points=[
            qm.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    "dense": dense,
                    "sparse": qm.SparseVector(indices=sparse.indices.tolist(),
                                              values=sparse.values.tolist()),
                },
                payload={"text": text, "chunk_id": f"c{i+1}", "page": i + 1,
                         "kind": "text", "doc_id": "test_doc_1"},
            )
            for i, (text, dense, sparse) in enumerate(zip(texts, dense_vecs, sparse_vecs))
        ])

        try:
            with patch.object(vectordb, "COLLECTION", TEST_COLLECTION):
                result = retrieve("what organelle produces ATP in the cell?")
            print(f"   chunks: {len(result['chunks'])}, images: {len(result['images'])}")
            assert result["chunks"], "no chunks returned"
            top = result["chunks"][0]
            assert "ATP" in top["text"] or "mitochon" in top["text"], \
                f"unexpected top chunk: {top['text']!r}"
            assert result["images"] == [], "text-only collection should yield no image hits"
        finally:
            client.delete_collection(TEST_COLLECTION)

    _run("_reranker() (singleton, disk load)",    _test_reranker_singleton)
    _run("_rerank_all (empty)",                   _test_rerank_all_empty)
    _run("_rerank_all (text-only)",               _test_rerank_all_text)
    _run("_rerank_all (with images)",             _test_rerank_all_images)
    _run("retrieve() end-to-end (Qdrant)",        _test_retrieve_end_to_end)

    print()
    print("== summary ==")
    for name, status in results.items():
        print(f"  {name:44s} {status}")
    print()
    if all(v == "PASS" for v in results.values()):
        print("OK — all rag helpers returned successfully")
    else:
        print("Some functions failed — see summary above")

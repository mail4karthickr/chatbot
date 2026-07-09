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


def _rerank(query: str, points, top_n=8):
    if not points:
        return []
    pairs = [(query, p.payload["text"]) for p in points]
    scores = _reranker().predict(pairs)
    ranked = sorted(zip(points, scores), key=lambda x: x[1], reverse=True)
    return [(p, float(s)) for p, s in ranked[:top_n]]


def _rerank_candidate_images(reranked, query):
    """Images come from two sources — direct image hits and images linked to
    a retrieved text chunk — then get reranked together."""
    candidates: dict[str, dict] = {}  # image_key -> {"caption":..., "via":...}
    for point, _ in reranked:
        p = point.payload
        if p["kind"] == "image" and p.get("image_key"):
            candidates.setdefault(p["image_key"], {"caption": p["text"], "via": "direct"})
        for k in p.get("linked_image_keys", []) or []:
            candidates.setdefault(k, {"caption": "", "via": "linked"})

    if not candidates:
        return []

    keys = list(candidates)
    scored = _reranker().predict([(query, candidates[k]["caption"] or k) for k in keys])
    ranked = sorted(zip(keys, scored), key=lambda x: x[1], reverse=True)
    return [
        (k, float(s), candidates[k]["caption"])
        for k, s in ranked if s >= IMG_SCORE_THRESHOLD
    ][:3]


def retrieve(query: str, doc_ids=None, top_n: int = 8) -> dict:
    """Retrieve top passages + relevant image references for `query`.

    Returns:
        {
          "chunks": [{chunk_id, text, page, kind, score}, ...],
          "images": [{image_key, url, caption, score}, ...],
        }
    """
    t_total = time.perf_counter()
    t0 = time.perf_counter()
    points   = hybrid_search(query, doc_ids=doc_ids, top_k=RERANK_CANDIDATE_POOL)
    t_search = time.perf_counter() - t0

    t0 = time.perf_counter()
    reranked = _rerank(query, points, top_n=top_n)
    t_rerank = time.perf_counter() - t0

    t0 = time.perf_counter()
    image_hits = _rerank_candidate_images(reranked, query)
    t_img = time.perf_counter() - t0

    log.info("retrieve timing search=%.2fs rerank=%.2fs images=%.2fs total=%.2fs points=%d reranked=%d img_candidates=%d",
             t_search, t_rerank, t_img, time.perf_counter() - t_total,
             len(points), len(reranked), len(image_hits))

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
    return {"chunks": chunks, "images": images}


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

    def _test_rerank_empty():
        assert _rerank("q", []) == []
        print("   empty points → empty result")

    def _test_rerank_with_points():
        points = [
            _mock_point("The mitochondrion is the powerhouse of the cell.", chunk_id="c1"),
            _mock_point("A random unrelated sentence about football.",       chunk_id="c2"),
            _mock_point("Cells convert glucose into ATP via cellular respiration.", chunk_id="c3"),
        ]
        ranked = _rerank("what produces ATP in a cell?", points, top_n=2)
        assert len(ranked) == 2
        top_text = ranked[0][0].payload["text"]
        print(f"   top score={ranked[0][1]:.4f}: {top_text!r}")
        assert "ATP" in top_text or "mitochondrion" in top_text, "reranker put an unrelated hit on top"

    def _test_rerank_images_empty():
        assert _rerank_candidate_images([], "q") == []
        print("   no image candidates → empty result")

    def _test_rerank_images_with_candidates():
        reranked = [
            (_mock_point("The heart pumps blood.", kind="text",
                         linked_image_keys=["img/heart.png"]), 0.9),
            (_mock_point("Diagram of a mitochondrion.", kind="image",
                         image_key="img/mito.png"), 0.85),
            (_mock_point("Unrelated caption about football.", kind="image",
                         image_key="img/ball.png"), 0.6),
        ]
        result = _rerank_candidate_images(reranked, "show me a diagram of the mitochondria")
        print(f"   {len(result)} image(s) passed threshold {IMG_SCORE_THRESHOLD}:")
        for key, score, caption in result:
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
    _run("_rerank (empty)",                       _test_rerank_empty)
    _run("_rerank (mock points)",                 _test_rerank_with_points)
    _run("_rerank_candidate_images (empty)",      _test_rerank_images_empty)
    _run("_rerank_candidate_images (mock hits)",  _test_rerank_images_with_candidates)
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

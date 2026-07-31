# test_4a_embedding_ab.py — A/B/C test for image-chunk dense embedding strategy
"""Resolves eval finding F6: is the np.mean(image, caption) blend the right
dense vector for image chunks — or is it diluting retrieval?

Strategies compared (image chunks only; text chunks identical everywhere):
  caption : dense = embed_texts([caption])            (image pixels not embedded)
  blend   : dense = mean(image_vec, caption_vec)      (current production — vectors
            copied verbatim from the live collection, so the incumbent is tested
            exactly as deployed)
  named   : dense = caption vec AND dense_image = image vec stored separately,
            queried as two prefetch legs + sparse, RRF-fused

Method: three throwaway Qdrant collections cloned from the live one (41 points),
differing ONLY in image vectors. Each query runs through the REAL hybrid_search
(collection patched) or its named-vector equivalent, then the REAL cross-encoder
rerank. Gold image chunks are labelled per query.

Metrics per (query, strategy):
  hit@25  gold image reached the candidate pool (the dense leg's job)
  hit@8   gold survived reranking into the final passages
  rank    position of first gold in candidates (lower = better)

Decision rule (agreed before running): highest total hit@25 wins; ties break by
hit@8, then by simplicity (caption > blend > named).

Cleanup: the ab_* collections are deleted at the end. Live data never touched.

Run:
    cd apps/ingestion-service && ./.venv/bin/python ../../parsing_test_files/test_4a_embedding_ab.py
"""
import base64
import os
import sys
from unittest.mock import patch

SERVICE_DIR = os.path.join(os.path.dirname(__file__), "..", "apps", "ingestion-service")
sys.path.insert(0, os.path.abspath(SERVICE_DIR))
os.chdir(os.path.abspath(SERVICE_DIR))

from qdrant_client import models as qm  # noqa: E402

import vectordb  # noqa: E402
from embed import _jina, embed_query, embed_texts  # noqa: E402
from storage import get_image  # noqa: E402
from vectordb import COLLECTION, DENSE_DIM, get_client  # noqa: E402

TOP_K = 25          # candidate pool, mirrors RERANK_CANDIDATE_POOL
TOP_N = 8           # final passages after rerank

# ---- queries with gold labels (predicate over an image point's payload) -----
def _is_card(pl):   return pl["kind"] == "image" and pl["page"] == 7
def _is_father_card(pl): return _is_card(pl) and "Father" in pl["doc_id"]
def _is_infographic(pl): return pl["kind"] == "image" and pl["page"] == 8

QUERIES = [
    ("Show me the health insurance ID card.",             _is_card),
    ("What does my insurance ID card look like?",         _is_card),
    ("Show me the father's insurance card.",              _is_father_card),
    ("How do I avail the cashless claim facility?",       _is_infographic),
    ("Show me the QR code to download the health app.",   _is_infographic),
    ("Show me the card with the member id and date of birth.", _is_card),
]


def load_live_points():
    points, offset = [], None
    while True:
        batch, offset = get_client().scroll(COLLECTION, limit=256, offset=offset,
                                            with_payload=True, with_vectors=True)
        points.extend(batch)
        if offset is None:
            break
    return points


def build_collections(points):
    """Three clones differing only in image dense vectors. Returns names."""
    client = get_client()
    images = [p for p in points if (p.payload or {}).get("kind") == "image"]
    captions = [p.payload["text"] for p in images]
    print(f"live points={len(points)}, image chunks={len(images)}")

    cap_vecs = embed_texts(captions)                                   # strategy: caption
    img_vecs = _jina([{"image": base64.b64encode(get_image(p.payload["image_key"])).decode()}
                      for p in images], task="retrieval.passage")      # strategy: named (image leg)
    cap_by_id = {p.id: v for p, v in zip(images, cap_vecs)}
    img_by_id = {p.id: v for p, v in zip(images, img_vecs)}

    names = {}
    for strat in ("caption", "blend", "named"):
        name = f"ab_{strat}"
        if client.collection_exists(name):
            client.delete_collection(name)
        vectors_config = {"dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)}
        if strat == "named":
            vectors_config["dense_image"] = qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)
        client.create_collection(
            collection_name=name, vectors_config=vectors_config,
            sparse_vectors_config={"sparse": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
        # Qdrant Cloud requires payload indexes for filterable fields (the
        # must_not kind=doc_summary filter) — mirror production's indexes.
        for field in ("kind", "doc_id"):
            client.create_payload_index(name, field_name=field, field_schema="keyword")
        upserts = []
        for p in points:
            vec = dict(p.vector)              # {"dense": [...], "sparse": SparseVector}
            if p.id in cap_by_id:             # image chunk -> per-strategy dense
                if strat == "caption":
                    vec["dense"] = cap_by_id[p.id]
                elif strat == "named":
                    vec["dense"] = cap_by_id[p.id]
                    vec["dense_image"] = img_by_id[p.id]
                # blend: keep live vector verbatim (it IS the blend)
            # text chunks simply OMIT dense_image in the named collection —
            # Qdrant allows partial named vectors; they don't exist in that
            # leg's space, which is exactly the intended semantics
            upserts.append(qm.PointStruct(id=p.id, vector=vec, payload=p.payload))
        client.upsert(name, points=upserts, wait=True)
        names[strat] = name
    return names


def search_named(query, name):
    """hybrid_search equivalent for the named strategy: caption leg + image leg
    + sparse, all RRF-fused. The text query vector probes BOTH dense spaces."""
    from embed import embed_sparse
    dense = embed_query(query)
    sparse = list(embed_sparse([query]))[0]
    flt = qm.Filter(must_not=[qm.FieldCondition(key="kind", match=qm.MatchValue(value="doc_summary"))])
    res = get_client().query_points(
        name,
        prefetch=[
            qm.Prefetch(query=dense, using="dense", limit=TOP_K, filter=flt),
            qm.Prefetch(query=dense, using="dense_image", limit=TOP_K, filter=flt),
            qm.Prefetch(query=qm.SparseVector(indices=sparse.indices.tolist(),
                                              values=sparse.values.tolist()),
                        using="sparse", limit=TOP_K, filter=flt),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF), limit=TOP_K, with_payload=True)
    return res.points


def main() -> int:
    points = load_live_points()
    names = build_collections(points)
    try:
        return run_queries(names)
    finally:
        for name in names.values():                 # cleanup even on crash
            get_client().delete_collection(name)
        print("\ncleanup: ab_* collections deleted")


def run_queries(names) -> int:
    from rag import _rerank_all                     # loads the cross-encoder (slow, once)

    results = {s: dict(hit25=0, hit8=0, ranks=[]) for s in names}
    for query, is_gold in QUERIES:
        print(f"\nQ: {query}")
        for strat, name in names.items():
            if strat == "named":
                cands = search_named(query, name)
            else:
                with patch.object(vectordb, "COLLECTION", name):
                    cands = vectordb.hybrid_search(query, top_k=TOP_K)
            gold_rank = next((i + 1 for i, p in enumerate(cands) if is_gold(p.payload)), None)
            reranked, _ = _rerank_all(query, cands, top_n=TOP_N)
            gold_rank8 = next((i + 1 for i, (p, _) in enumerate(reranked) if is_gold(p.payload)), None)
            r = results[strat]
            r["hit25"] += gold_rank is not None
            r["hit8"] += gold_rank8 is not None
            r["ranks"].append(gold_rank)
            print(f"   {strat:<8} candidates_rank={gold_rank or '-':>2}  "
                  f"post-rerank_rank={gold_rank8 or '-':>2}")

    n = len(QUERIES)
    print(f"\n== A/B result ({n} queries) ==")
    print(f"{'strategy':<9} {'hit@25':>7} {'hit@8':>6}  mean_candidate_rank")
    for strat, r in results.items():
        found = [x for x in r["ranks"] if x]
        mean_rank = sum(found) / len(found) if found else float("nan")
        print(f"{strat:<9} {r['hit25']:>4}/{n} {r['hit8']:>4}/{n}  {mean_rank:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

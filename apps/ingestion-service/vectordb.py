# vectordb.py
from functools import lru_cache
from qdrant_client import QdrantClient, models
from config import get_settings
from embed import embed_queries, embed_sparse

COLLECTION = "multimodal_rag"
DENSE_DIM = 1024     # jina-embeddings-v4 output dimension (text AND image share this space)


@lru_cache
def get_client() -> QdrantClient:
    s = get_settings()
    return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key, timeout=120)


def create_collection():
    client = get_client()
    if client.collection_exists(COLLECTION):
        return
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            # Modifier.IDF makes Qdrant apply inverse-document-frequency weighting
            # at query time. fastembed's Qdrant/bm25 emits raw term frequencies;
            # without this modifier the sparse leg is TF, not BM25 — rare/informative
            # tokens (e.g. "cataract", "hernioplasty") don't outweigh common ones
            # ("limit", "coverage"). The modifier is baked into the collection at
            # creation time, so this only takes effect after reset_collection().
            "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF),
        },
        # payload indexes make metadata filtering fast at scale
        on_disk_payload=True,
    )
    for field, schema in [("doc_id", "keyword"), ("doc_version", "keyword"),
                          ("kind", "keyword"), ("page", "integer")]:
        client.create_payload_index(COLLECTION, field_name=field, field_schema=schema)


def reset_collection() -> None:
    """Drop and recreate the collection. Wipes every point across all doc_ids/versions.
    Testing utility."""
    client = get_client()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    create_collection()


def delete_by_doc_id(doc_id: str) -> None:
    """Remove every point (all versions) for a given doc_id. Used when the source S3 file was deleted."""
    get_client().delete(
        COLLECTION,
        points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
        ])),
    )


def list_doc_summaries() -> list[dict]:
    """The document catalog: every kind='doc_summary' point, one per ingested
    document. Consumed by GET /documents for agent doc-routing."""
    docs, offset = [], None
    while True:
        points, offset = get_client().scroll(
            COLLECTION, limit=256, offset=offset,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="kind", match=models.MatchValue(value="doc_summary"))]),
            with_payload=True, with_vectors=False,
        )
        for p in points:
            pl = p.payload or {}
            docs.append({"doc_id": pl.get("doc_id"), "summary": pl.get("text"),
                         "pages": pl.get("pages"), "chunks": pl.get("chunks")})
        if offset is None:
            break
    return sorted(docs, key=lambda d: d["doc_id"] or "")


def hybrid_search(queries, doc_ids=None, top_k=50):
    """queries: one string or a list [original, variant, ...].

    Each query contributes a dense + sparse prefetch leg; Qdrant RRF-fuses ALL
    legs server-side into one top_k list — same fusion as single-query, just a
    wider candidate net. Fusion operates per point id, so a chunk found by
    several legs is boosted, not duplicated. The whole fan-out is ONE Jina
    call (batched) + ONE Qdrant request — no app-side parallelism needed.
    """
    if isinstance(queries, str):
        queries = [queries]
    dense_vecs  = embed_queries(queries)        # multimodal: matches BOTH text and image points
    sparse_vecs = list(embed_sparse(queries))

    # doc_summary points are the /documents catalog (agent doc-routing), not
    # content — they must never surface as passages.
    flt = models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchAny(any=list(doc_ids)))]
        if doc_ids else None,
        must_not=[models.FieldCondition(key="kind", match=models.MatchValue(value="doc_summary"))],
    )

    prefetch = []
    for dense, sparse in zip(dense_vecs, sparse_vecs):
        prefetch.append(models.Prefetch(query=dense, using="dense", limit=top_k, filter=flt))
        prefetch.append(models.Prefetch(
            query=models.SparseVector(indices=sparse.indices.tolist(),
                                      values=sparse.values.tolist()),
            using="sparse", limit=top_k, filter=flt))

    res = get_client().query_points(
        COLLECTION,
        prefetch=prefetch,
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=top_k, with_payload=True,
    )
    return res.points

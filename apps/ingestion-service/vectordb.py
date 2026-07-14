# vectordb.py
from functools import lru_cache
from qdrant_client import QdrantClient, models
from config import get_settings
from embed import embed_query, embed_sparse

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


def hybrid_search(query: str, doc_ids=None, top_k=50):
    dense  = embed_query(query)                 # multimodal: matches BOTH text and image points
    sparse = list(embed_sparse([query]))[0]

    flt = None
    if doc_ids:
        flt = models.Filter(must=[models.FieldCondition(
            key="doc_id", match=models.MatchAny(any=list(doc_ids)))])

    # Server-side fusion of dense + sparse via RRF.
    # Because the dense space is multimodal, the dense leg alone can surface images.
    res = get_client().query_points(
        COLLECTION,
        prefetch=[
            models.Prefetch(query=dense, using="dense", limit=top_k, filter=flt),
            models.Prefetch(
                query=models.SparseVector(indices=sparse.indices.tolist(),
                                          values=sparse.values.tolist()),
                using="sparse", limit=top_k, filter=flt),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=top_k, with_payload=True,
    )
    return res.points

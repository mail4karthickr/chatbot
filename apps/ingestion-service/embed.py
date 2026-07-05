# embed.py
import base64
from functools import lru_cache
import requests
import numpy as np
from fastembed import SparseTextEmbedding
from config import get_settings

_JINA_URL = "https://api.jina.ai/v1/embeddings"
DIM = 1024
_sparse = SparseTextEmbedding("Qdrant/bm25")


@lru_cache
def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().jina_api_key}",
        "Content-Type": "application/json",
    }


def _jina(inputs: list[dict], task: str) -> list[list[float]]:
    """inputs: list of {"text": ...} and/or {"image": <base64>} items."""
    r = requests.post(
        _JINA_URL, 
        headers=_headers(), 
        timeout=60, 
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
    return [d["embedding"] for d in r.json()["data"]]

def embed_texts(texts: list[str]) -> list[list[float]]:
    return _jina([{"text": t} for t in texts], task="retrieval.passage")

def embed_query(query: str) -> list[float]:
    return _jina([{"text": query}], task="retrieval.query")[0]

def embed_image_blended(image_bytes: bytes, caption: str) -> list[float]:
    """Pattern B: average the image (pixels) and caption (text) vectors, re-normalize.
    Both live in Jina's shared space, so the blend is meaningful."""
    b64 = base64.b64encode(image_bytes).decode()
    inputs = [{"image": b64}] + ([{"text": caption}] if caption else [])
    vecs = _jina(inputs, task="retrieval.passage")
    v = np.mean([np.array(x) for x in vecs], axis=0)       # blend image (+ caption)
    return (v / (np.linalg.norm(v) + 1e-12)).tolist()      # re-normalize the average

def embed_sparse(texts: list[str]):
    return list(_sparse.embed(texts))                       # BM25 SparseEmbedding objects


if __name__ == "__main__":
    # jina-embeddings-v4 requires images ≥ 28x28; generate a 32x32 red PNG in memory.
    import io
    from PIL import Image
    _buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(_buf, format="PNG")
    SAMPLE_IMAGE = _buf.getvalue()

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

    def _test_embed_image_blended_with_caption():
        v = embed_image_blended(SAMPLE_IMAGE, "a tiny test image")
        print(f"   {_summary(v)}")

    def _test_embed_image_blended_no_caption():
        v = embed_image_blended(SAMPLE_IMAGE, "")
        print(f"   {_summary(v)}")

    def _test_embed_sparse():
        v = embed_sparse(["hello world", "multimodal rag"])
        for i, s in enumerate(v):
            print(f"   [{i}] nnz={len(s.indices)}, head_idx={list(s.indices[:3])}, head_val={[round(float(x), 4) for x in s.values[:3]]}")

    _run("embed_texts",                            _test_embed_texts)
    _run("embed_query",                            _test_embed_query)
    _run("embed_image_blended (image + caption)",  _test_embed_image_blended_with_caption)
    _run("embed_image_blended (image only)",       _test_embed_image_blended_no_caption)
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

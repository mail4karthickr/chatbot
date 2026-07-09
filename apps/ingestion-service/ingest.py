# ingest.py
import base64, logging, os, time, uuid
from functools import lru_cache
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from qdrant_client import models
from vectordb import get_client, COLLECTION
from parser import Parser
from storage import put_image, get_image
from embed import embed_texts, embed_image_blended, embed_sparse
from models import Chunk
from config import get_settings

log = logging.getLogger("ingest")
user_log = logging.getLogger("user")  # human-friendly milestones for the UI "Info" view


@lru_cache
def _openai() -> OpenAI:
    return OpenAI(api_key=get_settings().openai_api_key)


@lru_cache
def _get_parser() -> Parser:
    return Parser()                      # reuse one instance (holds the vision client)

CAPTION_PROMPT = (
    "You are describing a figure from a document so it can be retrieved later. "
    "Write a dense, factual description (2-4 sentences) of what the image shows: "
    "chart type, axes, entities, notable values, and what it conveys. "
    "Use the surrounding text only to disambiguate. Do not invent specifics."
)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=1, max=20))
def caption_image(image_bytes: bytes, context_text: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    resp = _openai().chat.completions.create(
        model="gpt-4o-mini",     # cheap captioner; upgrade for dense charts/tables
        messages=[
            {"role": "system", "content": CAPTION_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"Surrounding text:\n{context_text}"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        temperature=0.0, max_tokens=300,
    )
    return resp.choices[0].message.content.strip()


def build_chunks(elements, doc_id, doc_version, source_path) -> list[Chunk]:
    """Elements arrive in reading order. Text -> text chunk; image -> image chunk
    (text = its gpt-4o caption). Each image is linked to the text chunk immediately
    before AND after it in reading order, and those text chunks link back to it —
    that bidirectional link drives the 'return the figure' logic in §5.2."""
    chunks: list[Chunk] = []
    last_text_idx = None
    for ordinal, e in enumerate(elements):
        if e["kind"] == "text":
            chunks.append(Chunk(
                chunk_id=f"{doc_id}:{ordinal}:text",
                doc_id=doc_id, doc_version=doc_version, kind="text",
                page=e["page"], ordinal=ordinal, text=e["text"],
                source_path=source_path,
            ))
            last_text_idx = len(chunks) - 1
        else:
            img_chunk = Chunk(
                chunk_id=f"{doc_id}:{ordinal}:image",
                doc_id=doc_id, doc_version=doc_version, kind="image",
                page=e["page"], ordinal=ordinal,
                text=e.get("caption", e.get("caption_hint", "")),   # gpt-4o caption (orchestrator sets it)
                image_key=e["image_key"], source_path=source_path,
            )
            chunks.append(img_chunk)
            # link to the preceding text chunk (reading order), both directions
            if last_text_idx is not None:
                chunks[last_text_idx].linked_image_keys.append(e["image_key"])
    # second pass: link each image to the NEXT text chunk in reading order too
    for i, c in enumerate(chunks):
        if c.kind == "image":
            nxt = next((chunks[j] for j in range(i + 1, len(chunks))
                        if chunks[j].kind == "text"), None)
            if nxt:
                nxt.linked_image_keys.append(c.image_key)
    return chunks


def upsert_chunks(chunks, job_id: str | None = None):
    texts  = [c.text for c in chunks]        # body for text chunks, caption for image chunks
    txt_idx = [i for i, c in enumerate(chunks) if c.kind == "text"]
    img_idx = [i for i, c in enumerate(chunks) if c.kind == "image"]
    log.info("embed start job_id=%s chunks=%d texts=%d images=%d",
             job_id, len(chunks), len(txt_idx), len(img_idx))

    t0 = time.perf_counter()
    sparse = embed_sparse(texts)             # BM25 over text/caption — the keyword half of hybrid
    log.info("embed sparse job_id=%s n=%d took=%.2fs",
             job_id, len(texts), time.perf_counter() - t0)

    dense = [None] * len(chunks)

    # text chunks: batch through Jina
    t0 = time.perf_counter()
    for pos, vec in zip(txt_idx, embed_texts([chunks[i].text for i in txt_idx])):
        dense[pos] = vec
    log.info("embed dense-text job_id=%s n=%d took=%.2fs",
             job_id, len(txt_idx), time.perf_counter() - t0)

    # image chunks: blend pixels + caption (one call each; cache by image hash in prod)
    t0 = time.perf_counter()
    for i in img_idx:
        dense[i] = embed_image_blended(get_image(chunks[i].image_key), chunks[i].text)
    log.info("embed dense-image job_id=%s n=%d took=%.2fs",
             job_id, len(img_idx), time.perf_counter() - t0)

    points = []
    for i, c in enumerate(chunks):
        points.append(models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)),   # deterministic id
            vector={
                "dense":  dense[i],
                "sparse": models.SparseVector(indices=sparse[i].indices.tolist(),
                                              values=sparse[i].values.tolist()),
            },
            payload=c.model_dump(),
        ))
    BATCH = 64
    client = get_client()
    t0 = time.perf_counter()
    n_batches = 0
    for i in range(0, len(points), BATCH):
        client.upsert(COLLECTION, points=points[i:i + BATCH], wait=True)
        n_batches += 1
    log.info("qdrant upsert job_id=%s points=%d batches=%d took=%.2fs",
             job_id, len(points), n_batches, time.perf_counter() - t0)


def ingest_document(path: str, doc_id: str, job_id: str | None = None):
    size = os.path.getsize(path)
    log.info("ingest start job_id=%s doc_id=%s path=%s size=%d",
             job_id, doc_id, path, size)

    t0 = time.perf_counter()
    elements, version = _get_parser().parse(path, doc_id)
    n_img = sum(1 for e in elements if e["kind"] == "image")
    n_text = len(elements) - n_img
    log.info("parsed job_id=%s doc_id=%s version=%s elements=%d images=%d took=%.2fs",
             job_id, doc_id, version, len(elements), n_img, time.perf_counter() - t0)
    user_log.info("Read document — %d text sections, %d images", n_text, n_img)

    t0 = time.perf_counter()
    existing = get_client().scroll(
        COLLECTION, limit=1,
        scroll_filter=models.Filter(must=[
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
            models.FieldCondition(key="doc_version", match=models.MatchValue(value=version)),
        ]),
    )[0]
    log.info("version check job_id=%s doc_id=%s version=%s took=%.2fs",
             job_id, doc_id, version, time.perf_counter() - t0)
    if existing:
        log.info("unchanged job_id=%s doc_id=%s version=%s — skipping",
                 job_id, doc_id, version)
        return {"status": "unchanged", "doc_id": doc_id, "version": version}

    t0 = time.perf_counter()
    for e in elements:
        if e["kind"] == "image":
            put_image(e["image_key"], e["image_bytes"])
    log.info("s3 image upload done job_id=%s doc_id=%s images=%d took=%.2fs",
             job_id, doc_id, n_img, time.perf_counter() - t0)

    t0 = time.perf_counter()
    to_caption = sum(1 for e in elements if e["kind"] == "image" and "caption" not in e)
    if to_caption:
        user_log.info("Describing %d image%s using AI…", to_caption, "" if to_caption == 1 else "s")
    for e in elements:
        if e["kind"] == "image" and "caption" not in e:
            ctx = "\n".join(filter(None, [e.get("caption_hint", ""), e.get("context_text", "")]))
            e["caption"] = caption_image(e["image_bytes"], ctx)
    log.info("captions done job_id=%s doc_id=%s captioned=%d took=%.2fs",
             job_id, doc_id, to_caption, time.perf_counter() - t0)

    chunks = build_chunks(elements, doc_id, version, path)
    user_log.info("Indexing %d pieces into the knowledge base…", len(chunks))
    upsert_chunks(chunks, job_id=job_id)
    log.info("upsert done job_id=%s doc_id=%s chunks=%d", job_id, doc_id, len(chunks))
    user_log.info("Added %d pieces to the knowledge base", len(chunks))

    t0 = time.perf_counter()
    get_client().delete(COLLECTION, points_selector=models.FilterSelector(filter=models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))],
        must_not=[models.FieldCondition(key="doc_version", match=models.MatchValue(value=version))],
    )))
    log.info("gc old-versions job_id=%s doc_id=%s took=%.2fs",
             job_id, doc_id, time.perf_counter() - t0)

    log.info("indexed job_id=%s doc_id=%s version=%s chunks=%d",
             job_id, doc_id, version, len(chunks))
    return {"status": "indexed", "doc_id": doc_id, "version": version, "chunks": len(chunks)}

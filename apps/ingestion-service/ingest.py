# ingest.py
import base64, logging, uuid
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


def upsert_chunks(chunks):
    texts  = [c.text for c in chunks]        # body for text chunks, caption for image chunks
    sparse = embed_sparse(texts)             # BM25 over text/caption — the keyword half of hybrid

    dense = [None] * len(chunks)
    txt_idx = [i for i, c in enumerate(chunks) if c.kind == "text"]
    img_idx = [i for i, c in enumerate(chunks) if c.kind == "image"]

    # text chunks: batch through Jina
    for pos, vec in zip(txt_idx, embed_texts([chunks[i].text for i in txt_idx])):
        dense[pos] = vec
    # image chunks: blend pixels + caption (one call each; cache by image hash in prod)
    for i in img_idx:
        dense[i] = embed_image_blended(get_image(chunks[i].image_key), chunks[i].text)

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
    for i in range(0, len(points), BATCH):
        client.upsert(COLLECTION, points=points[i:i + BATCH], wait=True)


def ingest_document(path: str, doc_id: str):
    log.info("start doc_id=%s path=%s", doc_id, path)

    elements, version = _get_parser().parse(path, doc_id)
    n_img = sum(1 for e in elements if e["kind"] == "image")
    log.info("parsed doc_id=%s version=%s elements=%d images=%d",
             doc_id, version, len(elements), n_img)

    existing = get_client().scroll(
        COLLECTION, limit=1,
        scroll_filter=models.Filter(must=[
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
            models.FieldCondition(key="doc_version", match=models.MatchValue(value=version)),
        ]),
    )[0]
    if existing:
        log.info("unchanged doc_id=%s version=%s — skipping", doc_id, version)
        return {"status": "unchanged", "doc_id": doc_id, "version": version}

    for e in elements:
        if e["kind"] == "image":
            put_image(e["image_key"], e["image_bytes"])
    log.info("s3 upload done doc_id=%s images=%d", doc_id, n_img)

    for e in elements:
        if e["kind"] == "image" and "caption" not in e:
            ctx = "\n".join(filter(None, [e.get("caption_hint", ""), e.get("context_text", "")]))
            e["caption"] = caption_image(e["image_bytes"], ctx)
    log.info("captions done doc_id=%s images=%d", doc_id, n_img)

    chunks = build_chunks(elements, doc_id, version, path)
    upsert_chunks(chunks)
    log.info("upsert done doc_id=%s chunks=%d", doc_id, len(chunks))

    get_client().delete(COLLECTION, points_selector=models.FilterSelector(filter=models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))],
        must_not=[models.FieldCondition(key="doc_version", match=models.MatchValue(value=version))],
    )))
    log.info("indexed doc_id=%s version=%s chunks=%d", doc_id, version, len(chunks))
    return {"status": "indexed", "doc_id": doc_id, "version": version, "chunks": len(chunks)}

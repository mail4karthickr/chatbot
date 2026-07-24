# ingest.py
import base64, json, logging, os, time, uuid
from functools import lru_cache
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from qdrant_client import models
from vectordb import get_client, COLLECTION
from parser import Parser
from storage import put_image
from embed import embed_texts, embed_sparse
from models import Chunk, content_hash
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
    "You describe a figure extracted from a document so it can be retrieved later. "
    "Describe ONLY what is visible in the image pixels; your first sentence must "
    "state what the image physically shows. Classify the image: 'content' for "
    "charts, tables, diagrams, photos, ID cards; 'decorative' for signatures, "
    "logos, icons, stamps, QR codes, or page ornaments. 2-4 sentences, factual, "
    "no invention."
)

CAPTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "figure_caption",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["content", "decorative"]},
                "caption": {"type": "string"},
            },
            "required": ["kind", "caption"],
            "additionalProperties": False,
        },
    },
}


SUMMARY_PROMPT = (
    "You write a routing summary for a document in a search index. The summary "
    "is used to decide WHICH document should be searched for a question — it is "
    "never shown as an answer. In 2-3 sentences state: what type of document "
    "this is, who or what it belongs to (exact person names, member/policy/"
    "certificate numbers, issuer), and what topics it covers. Copy identifiers "
    "verbatim. No preamble."
)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=1, max=20))
def summarize_document(chunks: list[Chunk]) -> str:
    """One routing summary per document, generated once at ingest time (§ doc
    catalog). Input is the first ~4k chars of parsed text — enough to identify
    the document; identification, not coverage, is the goal."""
    text = "\n".join(c.text for c in chunks if c.kind == "text")[:4000]
    resp = _openai().chat.completions.create(
        model=get_settings().generate_model,
        max_completion_tokens=300,
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": text},
        ],
    )
    summary = (resp.choices[0].message.content or "").strip()
    if not summary:
        raise RuntimeError("summarizer returned empty content")
    return summary


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=1, max=20))
def caption_image(image_bytes: bytes) -> tuple[str, bool]:
    """Returns (caption, decorative). PIXELS ONLY — no surrounding text is
    given to the VLM. Feeding neighbour text produced captions asserting
    details not visible in the image (eval F3: a 180x83 logo 'containing'
    a toll-free number); prompt instructions alone did not prevent the
    blending, so the context input was removed entirely. The neighbour text
    still reaches the generator via the Chunk's caption_hint/context_text
    payload fields. gpt-5-family models reject custom temperature and use
    max_completion_tokens; structured output guarantees a parseable answer
    and surfaces refusals instead of indexing them."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = _openai().chat.completions.create(
        model=get_settings().caption_model,
        response_format=CAPTION_SCHEMA,
        max_completion_tokens=2000,
        messages=[
            {"role": "system", "content": CAPTION_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
    )
    msg = resp.choices[0].message
    if msg.refusal or not msg.content:          # a refusal must never become a caption
        raise RuntimeError(f"captioner refused: {msg.refusal!r}")
    parsed = json.loads(msg.content)
    return parsed["caption"].strip(), parsed["kind"] == "decorative"


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
            if e.get("decorative"):
                continue      # signatures/logos/icons pollute retrieval — don't index
            img_chunk = Chunk(
                chunk_id=f"{doc_id}:{ordinal}:image",
                doc_id=doc_id, doc_version=doc_version, kind="image",
                page=e["page"], ordinal=ordinal,
                text=e.get("caption", e.get("caption_hint", "")),   # pixels-only VLM caption (orchestrator sets it)
                caption_hint=e.get("caption_hint", ""),             # printed caption (payload only)
                context_text=e.get("context_text", ""),             # neighbour text (payload only)
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

    # image chunks: dense = caption text only. The 4a A/B (2026-07-23, table in
    # RAG_IMPROVEMENT_PLAN.md Results log) showed pixels-only captions make
    # caption retrieval saturate — the image-vector blend added nothing the
    # reranker didn't erase. Pixels are NOT embedded; they live in S3 and
    # reach the UI via image_key.
    t0 = time.perf_counter()
    if img_idx:
        for pos, vec in zip(img_idx, embed_texts([chunks[i].text for i in img_idx])):
            dense[pos] = vec
    log.info("embed dense-image-caption job_id=%s n=%d took=%.2fs",
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


def upsert_doc_summary(doc_id: str, doc_version: str, summary: str,
                       n_pages: int, n_chunks: int) -> None:
    """Store the routing summary as ONE extra point with kind='doc_summary'.

    Living in the same collection means the existing lifecycle applies for
    free: the old-version GC in ingest_document() replaces it on re-ingest,
    and delete_by_doc_id() removes it with the document. hybrid_search()
    excludes kind='doc_summary', so it can never surface as a passage —
    it exists only for the /documents catalog (agent doc-routing).
    """
    dense = embed_texts([summary])[0]
    sparse = embed_sparse([summary])[0]
    point = models.PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:doc_summary")),  # 1 per doc, replaced on upsert
        vector={
            "dense": dense,
            "sparse": models.SparseVector(indices=sparse.indices.tolist(),
                                          values=sparse.values.tolist()),
        },
        payload={"kind": "doc_summary", "doc_id": doc_id, "doc_version": doc_version,
                 "text": summary, "pages": n_pages, "chunks": n_chunks},
    )
    get_client().upsert(COLLECTION, points=[point], wait=True)


def ingest_document(path: str, doc_id: str, job_id: str | None = None):
    size = os.path.getsize(path)
    log.info("ingest start job_id=%s doc_id=%s path=%s size=%d",
             job_id, doc_id, path, size)

    # 2c: hash-before-parse. The version is a content hash of the raw bytes
    # (~10ms) — it never needed the parse. Check for completed work BEFORE
    # paying the Docling convert (~60s+), so redelivered / stale-ledger jobs
    # for already-indexed content no-op in milliseconds.
    with open(path, "rb") as f:
        version = content_hash(f.read())

    # Completion marker, not "any point": the doc_summary point is written
    # LAST in this function, so its presence proves the whole ingest finished.
    # A crash mid-upsert leaves chunks but no marker -> this check misses ->
    # full re-ingest (deterministic point ids make the re-upsert overwrite the
    # partial leftovers). Broken states restart; only proven-complete skips.
    t0 = time.perf_counter()
    existing = get_client().scroll(
        COLLECTION, limit=1,
        scroll_filter=models.Filter(must=[
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
            models.FieldCondition(key="doc_version", match=models.MatchValue(value=version)),
            models.FieldCondition(key="kind", match=models.MatchValue(value="doc_summary")),
        ]),
    )[0]
    log.info("version check job_id=%s doc_id=%s version=%s took=%.2fs",
             job_id, doc_id, version, time.perf_counter() - t0)
    if existing:
        log.info("unchanged job_id=%s doc_id=%s version=%s — skipping (no parse)",
                 job_id, doc_id, version)
        user_log.info("Document unchanged — already in the knowledge base")
        return {"status": "unchanged", "doc_id": doc_id, "version": version}

    t0 = time.perf_counter()
    elements, _ = _get_parser().parse(path, doc_id)     # parse's own hash ignored — computed above
    n_img = sum(1 for e in elements if e["kind"] == "image")
    n_text = len(elements) - n_img
    log.info("parsed job_id=%s doc_id=%s version=%s elements=%d images=%d took=%.2fs",
             job_id, doc_id, version, len(elements), n_img, time.perf_counter() - t0)
    user_log.info("Read document — %d text sections, %d images", n_text, n_img)

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
            e["caption"], e["decorative"] = caption_image(e["image_bytes"])
    n_dec = sum(1 for e in elements if e.get("decorative"))
    log.info("captions done job_id=%s doc_id=%s captioned=%d decorative=%d took=%.2fs",
             job_id, doc_id, to_caption, n_dec, time.perf_counter() - t0)
    if n_dec:
        user_log.info("Skipping %d decorative image%s (logos, signatures, icons)",
                      n_dec, "" if n_dec == 1 else "s")

    chunks = build_chunks(elements, doc_id, version, path)
    user_log.info("Indexing %d pieces into the knowledge base…", len(chunks))
    upsert_chunks(chunks, job_id=job_id)
    log.info("upsert done job_id=%s doc_id=%s chunks=%d", job_id, doc_id, len(chunks))
    user_log.info("Added %d pieces to the knowledge base", len(chunks))

    t0 = time.perf_counter()
    user_log.info("Writing a document summary for the catalog…")
    summary = summarize_document(chunks)
    n_pages = max((e["page"] for e in elements), default=0)
    upsert_doc_summary(doc_id, version, summary, n_pages, len(chunks))
    log.info("doc summary job_id=%s doc_id=%s chars=%d took=%.2fs",
             job_id, doc_id, len(summary), time.perf_counter() - t0)

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

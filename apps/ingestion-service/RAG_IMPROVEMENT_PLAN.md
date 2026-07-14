# Multimodal RAG Pipeline — Improvement Plan

> Instructions for Claude Code: This document is the source of truth for a series of
> improvements to a multimodal RAG pipeline. Work through the steps **in order, one
> step per session/PR**. Each step lists exact files, changes, and a verification
> procedure. Do not skip verification. Do not start a step until the previous step's
> verification passes. If a change conflicts with something in the actual codebase,
> stop and ask rather than improvising.

---

## 1. System context

**Pipeline:** PDF → Docling parse → chunks (text + image) → embeddings → Qdrant (hybrid dense + sparse) → cross-encoder rerank → LLM generation.

**Key files:**

| File | Role |
|---|---|
| `parser.py` | Docling wrapper. Emits flat `elements` list in reading order: `{"kind": "text"}` and `{"kind": "image"}` dicts. Sets `generate_picture_images=True`, `images_scale=2.0`, OCR, table structure. |
| `embed.py` | Jina `jina-embeddings-v4` (dim 1024, multimodal shared space) for dense; `fastembed SparseTextEmbedding("Qdrant/bm25")` for sparse. Contains `embed_image_blended()` which **averages** image + caption vectors. |
| `ingest.py` | Orchestrator: parse → version check → S3 image upload → gpt-4o-mini captioning → `build_chunks` → `upsert_chunks` to Qdrant. Image chunks carry `image_key`; bidirectional image↔text linking exists in `build_chunks`. |
| `vectordb.py` | Qdrant client + collection config (not reviewed yet — Step 1 inspects it). |
| `storage.py` | S3 put/get for images. |
| `models.py` | `Chunk` pydantic model, `content_hash`. |

**Test corpus:** Aditya Birla "Group Activ Health" insurance certificates. Two near-identical documents in the corpus: `InsuranceFather.pdf` and `InsuranceMother.pdf` (same template, different member/certificate numbers). 8 pages each. Pages 1–6: text/tables. Page 7: two ID cards (visual layout). Page 8: promotional "Cashless Anywhere" page with QR codes and icons.

**Critical PDF quirk (verified):** the ID cards on page 7 are NOT discrete image objects. The card artwork is a single 2121×755 raster **stored upside-down** (flipped via transform matrix) used as a page background, with personalized data (policy no, member name, dates) overlaid as vector text. Docling's layout model classified the card areas as text regions and only extracted two 180×83 logo crops as pictures. The PDF also has minor structural corruption (`Missing 'endstream'` warnings from poppler) — parsers recover, but expect similar sloppiness in future documents from this generator.

---

## 2. Eval findings (baseline: 7/10 correct)

A 10-question eval was run. Generation was sound; all failures trace to ingestion/retrieval:

| ID | Finding | Evidence | Root cause |
|---|---|---|---|
| F1 | **Header-bias**: short header chunks outrank content | "Premium Details" header scored 0.971; the actual premium table scored 0.045. Same for "Coverage Details" (0.899 vs 0.602), "Authorized Signatory" (0.858 ×4) | Docling emits section headers as standalone `TextItem`s; cross-encoder over-rewards short exact lexical matches |
| F2 | **No card image indexed**: "show me the ID card" unanswerable | Only two 180×83 logo crops exist for page 7 | See PDF quirk above; no page rendering, no bbox crops |
| F3 | **Caption context-blending**: VLM writes surrounding text into captions | 180×83 logo captioned as "business card... includes toll-free number 1800 270 7000, website URL, email" — physically impossible at that resolution | `caption_image()` feeds `context_text` to gpt-4o-mini; prompt says "use only to disambiguate" but the model blends anyway |
| F4 | **Noise images indexed**: logos, signatures, icons all captioned + indexed | Signature crops (100×60), logo crops, tiny icons each became retrievable chunks and beat real content on Q1 | No size/decorative filter in `parser.py` |
| F5 | **Duplicate pollution**: Father/Mother twins halve effective top-k | Every top-8 passage list contained each chunk twice; Q6 blended signatures from both docs | No dedup/MMR at retrieval; near-identical corpus |
| F6 | **Averaged embedding is unvalidated** | Image chunks scored anomalously low (0.026–0.037) even on direct-hit questions | `embed_image_blended` = mean(image_vec, caption_vec). Same Jina space so not broken, but likely dilutes text→caption alignment. Needs A/B |
| F7 | Possible missing IDF on sparse | Not yet verified | `Qdrant/bm25` fastembed model emits TF; Qdrant must apply IDF via `Modifier.IDF` on the sparse index, else "BM25" is just TF |
| F8 | Citation index off-by-one (element ids vs parse), mangled-table parse artifacts pass through, presigned URLs expire before report viewing | Q5 cited `:160:image` but 160 is text; Father premium table rows misaligned by docling; report images broken | Parked — fix opportunistically, not blocking |

**Architecture through-line for every decision:** the vector DB only ever holds text (chunk bodies and image captions); pixels live in S3 behind `image_key` pointers; anything indexed must earn its place. Reject any change that violates this.

---

## 3. Implementation steps

### Step 1 — Chunking, sparse config, image filter (≈ half day)

**1a. Heading-aware chunking (fixes F1).**
In `parser.py`, headers must stop being standalone chunks. Preferred: replace the manual `iterate_items()` flattening with Docling's `HybridChunker` (`from docling.chunking import HybridChunker`), which merges section headers into their following content and injects heading context into each chunk (`chunker.contextualize(chunk)` / `chunk.meta.headings`). If `HybridChunker` is too disruptive to the `elements` dict contract, minimal alternative: detect `SectionHeaderItem` (import from `docling_core.types.doc`) during flattening and prepend its text to the next text/table element instead of emitting it as its own element. Either way, after this change **no element should consist solely of a heading string**. Preserve the existing `elements` output contract (`kind`, `page`, `text` / image fields) so `ingest.py` is untouched.

- Note: `SectionHeaderItem` is a subclass of `TextItem` — the current `isinstance(item, TextItem)` branch catches it silently. That is the bug.
- Keep tables exported via `export_to_markdown()` but now with their heading attached (e.g. chunk text = `"Premium Details\n\n| Particulars | Amount |..."`).

**1b. Sparse IDF check (fixes F7).**
Inspect `vectordb.py` collection creation. The sparse vector config must be:
```python
sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)}
```
If the modifier is absent, add it. Changing the modifier requires recreating the collection — acceptable, since Step 1 ends with full re-ingestion anyway.

**1c. Small-image filter (fixes F4).**
In `parser.py`, when handling `PictureItem`: if `pil.width < 250 and pil.height < 250`, skip the element entirely (do not caption, do not index). Log the skip count. (Optional refinement: instead of dropping, emit with `"decorative": True` in the dict, propagate to `Chunk` payload, and exclude `decorative=true` from retrieval filters — only do this if displaying logos/signatures at answer time is a requirement; default is to drop.)

**Verification 1:** Recreate collection, re-ingest both PDFs. Confirm: (a) no chunk whose text is exactly a bare heading like "Premium Details" exists (scroll the collection and assert); (b) chunk count for Father drops (was 165 elements; expect roughly 120–140 after merging + filtering, with ~2–4 image chunks instead of 11); (c) re-run the 10-question eval — Q8 (premium table) and Q9 (coverage table) top-1 should now be the table chunks, not headers.

---

### Step 2 — Captioning + ingest efficiency (≈ half day)

**2a. Pixels-only captioning (fixes F3).**
In `ingest.py` `caption_image()`: remove `context_text` from the VLM input entirely. The user message becomes just the image. Keep the system prompt but delete the "Use the surrounding text..." sentence. `context_text` and `caption_hint` remain in the element dict and must be stored in the `Chunk` payload as separate metadata fields (add to `models.Chunk` if missing) — the generator may read them at answer time, but they must never masquerade as image content.
- Optional: with the image count now small (post-Step-1 filter), upgrade the captioner from `gpt-4o-mini` to a stronger vision model. Cost is bounded by the filter.

**2b. Batch Jina image calls.**
In `ingest.py` `upsert_chunks()`, image dense embeddings are computed in a per-image loop. Jina v4 accepts mixed batches — batch all image inputs into one `_jina()` call (add an `embed_images(list[bytes])` or extend the existing function in `embed.py`).

**2c. Version check before parse.**
In `ingest.py` `ingest_document()`: `version` is `content_hash(file_bytes)` and does not need Docling. Compute the hash and run the Qdrant version-exists check **before** calling `parser.parse()`, so unchanged files skip the expensive convert.

**Verification 2:** Re-ingest. Manually inspect the stored captions for the remaining image chunks (e.g. the page-8 promo graphic): they must describe only what is visible in pixels — no member names, policy numbers, or contact details unless legible in the image itself. Confirm unchanged-file re-ingest returns `"unchanged"` without a parse log line.

---

### Step 3 — Gated page rendering + region crops (≈ 1 day, fixes F2)

This is the only genuinely new code. Goal: for visually-composed pages, produce region crops that become normal image chunks. **Never index full-page images for ordinary text pages; scaling must follow visual density, not page count.**

**3a. Enable page images.** In `parser.py` `PdfPipelineOptions`: add `opts.generate_page_images = True` (keep `images_scale=2.0`).

**3b. Gate.** After iterating items, compute per page: `pic_area_ratio` = sum of `PictureItem` bbox areas / page area, and `text_chars` = total extracted text chars on that page. Bboxes come from `item.prov[0].bbox`; page size from `doc.pages[page_no].size`. Note Docling bbox origin may be bottom-left — convert with `bbox.to_top_left_origin(page_height)` before any cropping math. Gate fires when `pic_area_ratio > 0.30 or text_chars < 200`. For the test corpus this must select pages 7 and 8 of each PDF and nothing else.

**3c. Crop.** For each gated page, get the page render (`doc.pages[page_no].image.pil_image`). Produce crops from Docling's layout clusters: group the page's items into visual regions (for page 7, the two card-sized regions; a simple approach — cluster item bboxes by horizontal position/gaps, or start with fixed halves guided by layout, but prefer bbox-driven). Scale bbox coordinates by `images_scale` when mapping to page-image pixels. Emit each crop as a standard `{"kind": "image"}` element with a distinct `image_key` (e.g. `p{page}_region{n}.png`) and empty `caption_hint`. These flow through the existing caption → chunk → embed path with zero downstream changes.
- Page renders themselves are intermediate: crop from them, do not store or index them (they are regenerable from the PDF).
- Apply the Step-1 size filter AFTER cropping too — but region crops will be large, so it should not fire.

**3d. Gate must stay quiet on text pages.** Pages 1–6 must produce no new image elements.

**Verification 3:** Re-ingest. Confirm: (a) exactly the gated pages produced region crops; (b) view the page-7 crops — each must show a complete, upright ID card with legible text; (c) their captions mention "ID card"/member details; (d) re-run the eval — Q1 ("show me the health insurance ID card") must now retrieve a card crop as a top passage and the answer must reference/display it via `image_key`; (e) Q2 (front-side details) must be grounded in the card crop caption and must NOT list nominee or sum insured (those are not on the card).

---

### Step 4 — Embedding A/B + retrieval dedup (≈ half day)

**4a. Embedding A/B (resolves F6).** Compare three dense strategies for image chunks on the frozen 10-question eval (retrieval hit-rate@k of the gold chunk, plus reranker score of the gold chunk):
1. **caption-only**: dense = `embed_texts([caption])` (image vector not stored)
2. **blend** (current): mean of image + caption vectors, re-normalized
3. **named vectors**: store both — `vector={"dense_caption": ..., "dense_image": ..., "sparse": ...}` — query `dense_caption` by default; optionally prefetch-fuse both.
Ship the winner. Prediction to test against: (1) or (3) beats (2). Whatever the outcome, **delete the `np.mean` blend** as the default path — if (3) wins, both signals survive as separate vectors; if (1) wins, drop the image vector entirely. Named-vector schema change requires collection recreation.

**4b. Dedup/MMR (fixes F5).** In the retrieval service (not in the three reviewed files — locate it), after reranking: suppress near-duplicates before final top-k. Cheapest effective version: cosine-similarity threshold (~0.95) between candidate dense vectors, keep the higher-ranked one; or MMR with λ≈0.7. Additionally support a `doc_id` metadata filter for questions scoped to one document.

**Verification 4:** Re-run eval. Confirm: (a) no passage list contains the same content from both Father and Mother when one suffices; (b) record the A/B table (strategy × hit-rate) in this file under "Results log"; (c) Q6 (signature) no longer presents both documents' signatures as one document.

---

### Step 5 — Eval hardening (ongoing)

Extend the eval set (keep the original 10 frozen as regression):
1. **Disambiguation:** "What is the certificate number in the father's policy?" (gold: GHI-71-24-3153645-001; Mother's GHI-71-24-2557310-001 is the built-in distractor).
2. **Abstention:** "What is the maternity coverage limit?" (not in the documents — must abstain); "Show me the photo of the insured person" (cards are photo-less — must say so, not return a logo).
3. **Reranker trap:** "What is the cataract limit?" vs "What is the hernioplasty limit?" (30K vs 40K, adjacent rows — answers must not swap).
4. **Gate regression:** ingest one long text-heavy PDF (e.g. an 80–150 page policy-wordings document) and assert the Step-3 gate produces zero or near-zero page crops for it.

Track **retrieval hit-rate** and **answer faithfulness** as separate metrics per question, so any regression is attributable to a stage.

---

## 4. Parked (do not implement unless asked)

- Table-repair for mangled docling tables (generation currently still extracts correct values; revisit if Step 1's heading merge doesn't incidentally help).
- Citation element-index off-by-one between parse ids and cited ids (annoying, not blocking; fix when touching the citation formatter).
- Presigned URL TTL for exported reports (ops config, not pipeline).
- ColPali-style visual-first retrieval (only if caption-based retrieval hits a ceiling on chart-dense documents).

## 5. Results log

*(Append after each step: date, step, eval score /10, per-question deltas, notes.)*

- Baseline (pre-Step-1): 7/10. Failures: Q1 (no card image), Q2 (partially wrong grounding), Q8/Q9 header-bias in retrieval (answers correct).

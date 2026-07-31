# Header-bias / bare-heading chunk problem in Docling PDF parsing

## 1. Setup

Pipeline: **PDF → Docling parse → flat `elements` list → text/image chunks → embeddings (Jina v4 dense + fastembed BM25 sparse) → Qdrant → cross-encoder rerank (`BAAI/bge-reranker-v2-m3`) → LLM answer.**

Corpus for this investigation: two Aditya Birla "Group Activ Health" insurance certificates (`InsuranceFather.pdf`, `InsuranceMother.pdf`). 8 pages each, mostly filled form fields + a few tables + a signature graphic on page 1 + card artwork on pages 7–8.

Package versions:
- `docling==2.15.1`
- `docling-core==2.19.0`
- `transformers==4.57.6`

---

## 2. Symptom (baseline eval)

10-question eval scored 7/10. All 3 failures traced to retrieval, and the reranker scores made the problem obvious:

| Query | Chunk that won | Score | The chunk that *should* have won | Score |
|---|---|---|---|---|
| "what is the premium?" | bare `"Premium Details"` heading | **0.971** | premium table | **0.045** |
| "what is the coverage?" | bare `"Coverage Details"` heading | 0.899 | coverage table | 0.602 |
| "who signed?" | bare `"Authorized Signatory"` × 4 | ~0.858 each | signature block | — |

Root cause: the parser emits section headings as standalone chunks. Short chunks with exact keyword matches beat long content chunks in **both** BM25 (length-normalized TF-IDF) **and** the cross-encoder (over-rewards short exact lexical overlap on the query). Compounded by the fact that two near-identical documents produce every heading twice.

---

## 3. Current parser code (relevant excerpt)

`apps/ingestion-service/parser.py`:

```python
from docling_core.types.doc import TextItem, TableItem, PictureItem, SectionHeaderItem

class Parser:
    def parse(self, path: str, doc_id: str) -> tuple[list[dict], str]:
        with open(path, "rb") as f:
            version = content_hash(f.read())
        doc = self._converter.convert(path).document

        raw = []
        pending_heading = ""
        for item, _level in doc.iterate_items():
            if isinstance(item, PictureItem):
                pil = item.get_image(doc)
                if pil is None: continue
                buf = io.BytesIO(); pil.convert("RGB").save(buf, format="PNG")
                raw.append({"kind": "image", "image_bytes": buf.getvalue(),
                            "caption": item.caption_text(doc) or "",
                            "page": _page_of(item)})
            elif isinstance(item, SectionHeaderItem):        # NEW BRANCH
                heading = (item.text or "").strip()
                if heading:
                    pending_heading = f"{pending_heading}\n{heading}" if pending_heading else heading
            elif isinstance(item, TableItem):
                table_md = item.export_to_markdown()
                text = f"{pending_heading}\n\n{table_md}" if pending_heading else table_md
                raw.append({"kind": "text", "text": text, "page": _page_of(item)})
                pending_heading = ""
            elif isinstance(item, TextItem) and (item.text or "").strip():
                body = item.text.strip()
                text = f"{pending_heading}\n\n{body}" if pending_heading else body
                raw.append({"kind": "text", "text": text, "page": _page_of(item)})
                pending_heading = ""
```

Intent: accumulate `SectionHeaderItem`s in `pending_heading`, prepend to the next `TextItem`/`TableItem`, then reset. `SectionHeaderItem` must be checked *before* `TextItem` because it inherits from `TextItem`.

Downstream expectations (`ingest.py`) — DO NOT BREAK:
- `elements` is a flat list of dicts in reading order.
- Each dict is either `{"kind":"text", "page": int, "text": str}` or `{"kind":"image", "page": int, "image_key": str, "image_bytes": bytes, "caption_hint": str, "context_text": str, "img_index": int}`.
- Text elements are used to build `Chunk`s; image elements are captioned then embedded.
- `build_chunks` links each image bidirectionally to the text chunks immediately before AND after it in reading order.

---

## 4. What the current fix catches, and what it doesn't

After the fix, ingesting `InsuranceFather.pdf` and scrolling Qdrant for short single-line text chunks:

```
179 short single-line text chunks (across both PDFs)
```

Grouped by exact text:

| Text | Count | Docling class | Caught by current fix? |
|---|---|---|---|
| `"Premium Details"` | 2 | `SectionHeaderItem` | ✅ (test in-process — bare chunks disappear) |
| `"Coverage Details"` | 2 | `SectionHeaderItem` | ✅ |
| `"Authorized Signatory"` | 4 | **`TextItem`** | ❌ |
| `"Master Policy Number:"` | 2 | `TextItem` | ❌ |
| `"Certificate Number:"` | 2 | `TextItem` | ❌ |
| `"Grievance Redressal"` | 2 | `TextItem` | ❌ |
| `"Insured Person Detail"` | 2 | `TextItem` | ❌ |
| `"Date:"`, `"Place:"`, `"Mumbai"` etc. (form labels + values) | ~20 | `TextItem` | ❌ |
| `"a) Start by downloading..."` (FAQ steps, duplicated) | 12 | `TextItem` | ❌ (arguably should NOT be merged) |

**Diagnostic that proved the classification** (script):

```python
from parser import Parser
from docling_core.types.doc import SectionHeaderItem, TextItem
doc = Parser()._converter.convert("InsuranceFather.pdf").document
for item, _ in doc.iterate_items():
    txt = (getattr(item, "text", "") or "").strip()
    if txt in {"Premium Details", "Coverage Details", "Authorized Signatory"}:
        print(f"'{txt}' -> {type(item).__name__}")
```

Output:
```
'Premium Details'      -> SectionHeaderItem
'Coverage Details'     -> SectionHeaderItem
'Authorized Signatory' -> TextItem
'Authorized Signatory' -> TextItem
```

So Docling's layout model classifies some headings as `SectionHeaderItem` but classifies others (visually-styled headings, form labels, section titles without markdown structure) as plain `TextItem`. This is not a bug we can file — it reflects how the PDF's semantic tagging was authored.

---

## 5. Alternative tried: `HybridChunker` from `docling.chunking`

`HybridChunker` chunks the DoclingDocument using the layout **tree structure** (not just item types): it groups sibling items under their nearest ancestor heading, attaches the heading hierarchy as metadata (`chunk.meta.headings`), and uses a tokenizer for size control.

**Constructor gotchas found in `docling-core 2.19.0`:**
- Default `tokenizer='sentence-transformers/all-MiniLM-L6-v2'`; default `max_tokens=None` → tokenizer's `model_max_length` (512).
- Any single chunk > 512 tokens triggers `_split_using_plain_text`, which calls `sem_chunker.chunk(text)` — and the internal `Chunker` in this version has no `.chunk()` method → `AttributeError`.
- Workaround: pass `max_tokens=4096` explicitly. The premium/coverage tables top out around 700 tokens; 4096 is well above any single section.
- `HybridChunker.contextualize(chunk)` is documented but not implemented in 2.19.0; use `chunker.serialize(chunk=chunk)` instead (deprecated but functional).
- Warning `"Token indices sequence length is longer than the specified maximum sequence length for this model (694 > 512)"` is spurious — the tokenizer is being used for token counting, not to run the model.

**Results on `InsuranceFather.pdf` (`max_tokens=4096`):**

| Metric | Current parser | HybridChunker |
|---|---|---|
| Text chunks per doc | ~150 | **15** |
| Short single-line chunks | ~90 | **1** (`"Aditya Birla Health Insurance Co. Limited"`) |
| Bare `Authorized Signatory` | 4 | **0** — merged into 2 chunks |
| Bare `Master Policy Number` | 1 | 0 |
| Bare `Certificate Number` | 1 | 0 |
| Premium table | separate + heading separate | one chunk with `meta.headings=['Premium Details']` |

Phrases that showed `NOT FOUND in any chunk text` in the audit were actually promoted to `chunk.meta.headings` — `serialize()` prepends them to the chunk output. So `chunk #003` raw text is `"Net Premium, Amount = 29052.54..."` but its `serialize()` output is `"Premium Details\nNet Premium, Amount = 29052.54..."`. Correctly contextualized.

**Trade-offs / open concerns with HybridChunker:**

1. **Table serialization changes shape.** Default serializer converts markdown tables into `"row_label, col_label = value"` sentences. Human preview UI will look different. Retrieval quality should be fine (Jina + BM25 both tokenize either form) but is unproven on the eval.
2. **Table alignment bugs surface differently.** Docling's misalignment of the `Gross Premium` row is a pre-existing parse bug; it manifests in HybridChunker output as an empty label (`, Amount = 34282.0.`). This is not a chunker problem, but it is now more visible.
3. **HybridChunker emits only text chunks.** `PictureItem`s (11 in this PDF) are not included. Parser must still walk `iterate_items()` for images and merge with text chunks by `(page, bbox_y)` to preserve reading order for the bidirectional image ↔ text linking in `build_chunks`.
4. **FAQ duplication is source-data, not chunker-caused.** The `"a) Start by downloading..."` steps repeat 6× per doc regardless of chunker — the source PDF contains near-identical FAQ blocks for multiple workflows (Cashless, Reimbursement, Preventive, etc.). Any dedup fix has to be at retrieval time, not parse time.
5. **`serialize()` is deprecated.** Upgrading Docling later will require switching to `contextualize()` once it's implemented in `docling-core`.

---

## 6. Question for the reviewing model

The current fix cleanly solves the `SectionHeaderItem` subset (~5% of the noise). The remaining ~95% is `TextItem` headings that no `isinstance` check will catch. Three candidate paths forward:

**Path A — Extend the current parser with a `TextItem`-heading heuristic.**
Detect heading-like `TextItem`s and route them through the same `pending_heading` path:
- length < 60 chars, no terminal punctuation, no `a)`/`b)`/`i.` list marker, title-case or ALL CAPS or ends with `:`.
- Pros: minimal change; keeps existing markdown-table output; keeps `iterate_items()` reading order intact.
- Cons: heuristic-based, will misclassify on new document types; can't distinguish "form label" from "short standalone sentence".

**Path B — Replace text branch with HybridChunker.**
Use HybridChunker for text; keep the `PictureItem` walk separately; merge streams by `(page, bbox_y)` before returning `elements`.
- Pros: layout-tree-driven, generalizes across document types; heading path auto-attached to every chunk; 10× reduction in chunk count.
- Cons: changes table rendering shape; changes image-to-text interleaving logic; adds a tokenizer dependency; `serialize()` deprecation to manage later.

**Path C — Hybrid of A and B.**
Use HybridChunker but override its default serializer to keep markdown tables (retains human-readable table shape). Requires implementing a custom `BaseChunker.serialize` override.

Which path best trades off correctness, robustness against future document types, and change surface area? Are there other approaches (e.g. semantic pre-processing, LLM-based heading detection, using `docling`'s `HierarchicalChunker` instead of `HybridChunker`) worth considering?

---

## 7. Reproduction / attachments

- Source PDF: `parsing_test_files/InsuranceFather.pdf`
- Current-parser HTML dump: `parsing_test_files/InsuranceFather.parse (1).html`
- HybridChunker probe script + output: `parsing_test_files/hybrid_chunker_probe.py`
- Full parser source: `apps/ingestion-service/parser.py`
- Downstream chunk builder (must not break): `apps/ingestion-service/ingest.py` (`build_chunks`, `upsert_chunks`)
- Chunk schema: `apps/ingestion-service/models.py`

# parser.py — document parsing via Docling (layout, reading order, captions, OCR)
import io
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.chunking import HybridChunker
from docling_core.types.doc import PictureItem
from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer, ChunkingSerializerProvider)
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from models import content_hash
# The parser extracts and RETURNS image bytes; persisting them to object storage
# is the orchestrator's job (§4.6). The parser performs no storage I/O.


class _MarkdownTableProvider(ChunkingSerializerProvider):
    """Keep tables as markdown in chunk text (matches preview UI and the shape
    the eval was scored against) instead of the default 'row, col = value'
    triplet sentences."""
    def get_serializer(self, doc):
        return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())


class Parser:
    """Document parsing behind one class, backed by Docling.

        parser = Parser()
        elements, version = parser.parse(path, doc_id)

    `elements` is a flat list of dicts in READING ORDER (Docling's layout model
    resolves columns and narrative flow). Text items are {"kind": "text"} dicts;
    each figure is a {"kind": "image"} dict carrying the image bytes, Docling's
    linked caption, and `context_text` (its reading-order neighbours). `version`
    is a content hash of the file. Downstream (caption -> chunk -> embed) is
    unchanged — only the *quality* of the context improves.

    Text chunking is delegated to Docling's HybridChunker: it walks the layout
    TREE (not the flat item list), packs sibling items under the same heading
    path up to `max_tokens`, and prepends that heading path to each chunk via
    contextualize(). This is what prevents bare heading/label fragments
    ("Premium Details", "Authorized Signatory") from becoming standalone
    chunks — short exact-match chunks were outranking their own content in
    retrieval (short-passage bias in BM25 + cross-encoder). It is robust to
    Docling classifying visually-styled headings as plain TextItem, because
    chunk boundaries come from the tree + token budget, not item types.
    """

    def __init__(self, images_scale: float = 2.0, do_ocr: bool = True,
                 context_chars: int = 1500, max_tokens: int = 1024):
        opts = PdfPipelineOptions()
        opts.generate_picture_images = True   # so figures carry pixel data we can embed/return
        opts.images_scale = images_scale      # render figures at 2x for caption/embed quality
        opts.do_ocr = do_ocr                  # OCR scanned / image-only pages automatically
        opts.do_table_structure = True        # recover table cell structure
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        self._chunker = HybridChunker(
            # tokenizer is used for token COUNTING only (chunk size control),
            # not to run the model — a "sequence length > 512" warning is spurious.
            tokenizer=HuggingFaceTokenizer.from_pretrained(
                "sentence-transformers/all-MiniLM-L6-v2", max_tokens=max_tokens),
            serializer_provider=_MarkdownTableProvider(),
            merge_peers=True,                 # pack small same-heading siblings together
        )
        self.context_chars = context_chars

    def parse(self, path: str, doc_id: str) -> tuple[list[dict], str]:
        with open(path, "rb") as f:
            version = content_hash(f.read())
        doc = self._converter.convert(path).document

        # 1) Two streams, one order. Images come from the flat iterate_items()
        #    walk; text chunks come from HybridChunker (which excludes pictures).
        #    Both are stamped with the item's position in Docling's reading order
        #    (a text chunk gets the position of its FIRST constituent item), then
        #    merged by that ordinal — no bbox geometry needed, and build_chunks's
        #    bidirectional image<->text linking keeps working on the flat list.
        order: dict[str, int] = {}
        raw = []
        for idx, (item, _level) in enumerate(doc.iterate_items()):
            order[item.self_ref] = idx
            if isinstance(item, PictureItem):
                pil = item.get_image(doc)                       # PIL image (pixels)
                if pil is None:
                    continue
                buf = io.BytesIO(); pil.convert("RGB").save(buf, format="PNG")
                raw.append({"kind": "image", "image_bytes": buf.getvalue(),
                            "caption": item.caption_text(doc) or "",
                            "page": _page_of(item), "order": idx})

        for ch in self._chunker.chunk(doc):
            items = list(ch.meta.doc_items or [])
            ordinals = [order[it.self_ref] for it in items if it.self_ref in order]
            page = next((p.page_no for it in items
                         for p in (getattr(it, "prov", None) or [])), 0)
            text = self._chunker.contextualize(chunk=ch).strip()  # heading path prepended
            if not text:
                continue
            raw.append({"kind": "text", "text": text, "page": page,
                        "order": min(ordinals) if ordinals else len(order)})

        raw.sort(key=lambda r: r["order"])                       # restore reading order

        # 2) Build elements. For each figure, attach its Docling caption + the nearest
        #    text BEFORE and AFTER it IN READING ORDER. Those neighbours are now full
        #    sections (not fragments), so caption context quality improves too.
        elements, img_index = [], 0
        for i, r in enumerate(raw):
            if r["kind"] == "text":
                elements.append({"kind": "text", "page": r["page"], "text": r["text"]})
                continue
            prev_t = next((raw[j]["text"] for j in range(i - 1, -1, -1)
                           if raw[j]["kind"] == "text"), "")
            next_t = next((raw[j]["text"] for j in range(i + 1, len(raw))
                           if raw[j]["kind"] == "text"), "")
            context = "\n".join(p for p in (r["caption"], prev_t, next_t) if p)
            elements.append({
                "kind": "image", "page": r["page"],
                "image_key": f"_artifacts/{doc_id}/{version}/p{r['page']}_img{img_index}.png",
                "image_bytes": r["image_bytes"],
                "caption_hint": r["caption"],                    # Docling's printed caption
                "context_text": context[:self.context_chars],    # reading-order neighbours
                "img_index": img_index,
            })
            img_index += 1
        return elements, version


def _page_of(item) -> int:
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else 0

if __name__ == "__main__":
    from pprint import pprint
    parser = Parser()
    elements, version = parser.parse(path="./medical_study.pdf", doc_id="")
    printable = [
        {**e, "image_bytes": f"<{len(e['image_bytes'])} bytes>"} if e["kind"] == "image" else e
        for e in elements
    ]
    print(f"version: {version}")
    print(f"elements ({len(printable)}):")
    pprint(printable, sort_dicts=False, width=120)

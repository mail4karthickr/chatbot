# parser.py — document parsing via Docling (layout, reading order, captions, OCR)
import io
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling_core.types.doc import TextItem, TableItem, PictureItem, SectionHeaderItem
from models import content_hash
# The parser extracts and RETURNS image bytes; persisting them to object storage
# is the orchestrator's job (§4.6). The parser performs no storage I/O.


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
    """

    def __init__(self, images_scale: float = 2.0, do_ocr: bool = True,
                 context_chars: int = 1500):
        opts = PdfPipelineOptions()
        opts.generate_picture_images = True   # so figures carry pixel data we can embed/return
        opts.images_scale = images_scale      # render figures at 2x for caption/embed quality
        opts.do_ocr = do_ocr                  # OCR scanned / image-only pages automatically
        opts.do_table_structure = True        # recover table cell structure
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        self.context_chars = context_chars

    def parse(self, path: str, doc_id: str) -> tuple[list[dict], str]:
        with open(path, "rb") as f:
            version = content_hash(f.read())
        doc = self._converter.convert(path).document

        # 1) Flatten Docling items in READING ORDER (text / table / figure).
        # Section headings are held in `pending_heading` and prepended to the next
        # text/table element instead of being emitted as standalone chunks — bare
        # headings ("Premium Details") were outranking their own content in retrieval
        # (short-passage bias in BM25 + cross-encoder). SectionHeaderItem must be
        # checked BEFORE TextItem: it's a subclass of TextItem, so a bare
        # `isinstance(item, TextItem)` check catches headers too.
        raw = []
        pending_heading = ""
        for item, _level in doc.iterate_items():
            if isinstance(item, PictureItem):
                pil = item.get_image(doc)                       # PIL image (pixels)
                if pil is None:
                    continue
                buf = io.BytesIO(); pil.convert("RGB").save(buf, format="PNG")
                raw.append({"kind": "image", "image_bytes": buf.getvalue(),
                            "caption": item.caption_text(doc) or "",
                            "page": _page_of(item)})
            elif isinstance(item, SectionHeaderItem):
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

        # 2) Build elements. For each figure, attach its Docling caption + the nearest
        #    text BEFORE and AFTER it IN READING ORDER. This replaces the old y-proximity
        #    heuristic: "before/after" is now true narrative order from the layout model,
        #    correct across columns.
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
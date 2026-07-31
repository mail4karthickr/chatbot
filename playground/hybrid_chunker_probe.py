"""Standalone probe: run Docling HybridChunker on InsuranceFather.pdf and dump
the chunk output for inspection.

This script does NOT touch the running ingestion service or Qdrant. It's a
throwaway used to decide whether HybridChunker's layout-tree-based grouping
actually fixes the residual "bare short TextItem" chunks that parser.py's
SectionHeaderItem branch can't catch (Authorized Signatory, Master Policy
Number, Certificate Number, form labels, etc.).

Usage
-----
Run using the ingestion-service venv so we hit the exact same docling
package versions as production:

    cd /Users/karthickramasamy/Desktop/Chatbot/apps/ingestion-service
    ./.venv/bin/python ../../parsing_test_files/hybrid_chunker_probe.py

What to look for in the output
------------------------------
1. WATCH-PHRASE AUDIT — for each phrase (e.g. "Authorized Signatory"),
   report whether it survives as a bare chunk or was merged into a bigger
   chunk with surrounding context. This is the key signal.
2. SHORT SINGLE-LINE CHUNKS — count and list. Baseline (current parser)
   is 179 across both PDFs; HybridChunker should slash this.
3. CHUNK LENGTH SUMMARY — min/max/avg. If the premium table gets split
   because it exceeds max_tokens, we'll see it here.
"""
from __future__ import annotations

from pathlib import Path
import sys

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling_core.types.doc import PictureItem


PDF = Path(__file__).parent / "InsuranceFather.pdf"

# Strings the current parser leaves as bare chunks. If HybridChunker still
# emits any of these as a chunk whose text.strip() equals the phrase, then it
# hasn't helped for that particular case.
WATCH = [
    "Premium Details",
    "Coverage Details",
    "Authorized Signatory",
    "Master Policy Number",
    "Certificate Number",
    "Grievance Redressal",
    "Insured Person Detail",
    "Base Covers",
    "Add-On Covers",
    "Claim Process",
    "Date:",
    "Place:",
]


def _pipeline_options() -> PdfPipelineOptions:
    """Match apps/ingestion-service/parser.py so the doc tree is identical to
    production. Any divergence here would make the probe misleading."""
    opts = PdfPipelineOptions()
    opts.generate_picture_images = True
    opts.images_scale = 2.0
    opts.do_ocr = True
    opts.do_table_structure = True
    return opts


def _preview(text: str, n: int = 180) -> str:
    text = text.replace("\n", " ⏎ ")
    return text[:n] + (" …" if len(text) > n else "")


def main() -> None:
    if not PDF.exists():
        sys.exit(f"PDF not found: {PDF}")

    print(f"parsing {PDF.name} …")
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pipeline_options())}
    )
    doc = converter.convert(str(PDF)).document

    print("running HybridChunker …")
    # max_tokens=4096 avoids the plain-text-splitter fallback path in
    # docling-core 2.19.0, which is broken (calls `.chunk` on an internal
    # object that doesn't expose it). 4096 is well above any single-section
    # size we'd expect in a policy certificate — the premium/coverage tables
    # top out around 700 tokens.
    chunker = HybridChunker(max_tokens=4096)
    chunks = list(chunker.chunk(doc))

    # HybridChunker does NOT emit PictureItems. In the real parser we'd still
    # walk iterate_items() to collect images. Count them here just for context.
    pictures = [it for it, _ in doc.iterate_items() if isinstance(it, PictureItem)]

    print("=" * 90)
    print(f"CHUNKS: {len(chunks)} text  |  IMAGES (separate stream): {len(pictures)}")
    print("=" * 90)

    for i, ch in enumerate(chunks):
        raw = ch.text
        ctx = chunker.serialize(chunk=ch)
        headings = list(getattr(ch.meta, "headings", None) or [])
        doc_items = list(getattr(ch.meta, "doc_items", None) or [])
        item_types = [type(it).__name__ for it in doc_items]
        pages = sorted({p.page_no for it in doc_items for p in (it.prov or [])})

        print(f"\n[chunk #{i:03d}] pages={pages} headings={headings} items={item_types}")
        print(f"  raw   ({len(raw):4d} chars): {_preview(raw)!r}")
        if ctx != raw:
            print(f"  ctx   ({len(ctx):4d} chars): {_preview(ctx)!r}")

    # ------------------------------------------------------------------
    # Diagnostic 1: are the WATCH phrases still bare?
    # ------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("WATCH-PHRASE AUDIT — did HybridChunker merge these into bigger chunks?")
    print("=" * 90)
    for w in WATCH:
        matches = [(i, ch) for i, ch in enumerate(chunks) if w in ch.text]
        if not matches:
            print(f"  {w!r:32s}  NOT FOUND in any chunk text")
            continue
        for i, ch in matches:
            raw_len = len(ch.text.strip())
            ctx_len = len(chunker.serialize(chunk=ch).strip())
            bare = ch.text.strip() == w
            tag = "BARE ✗" if bare else "MERGED ✓"
            print(f"  {w!r:32s}  chunk #{i:03d}  {tag}  raw={raw_len:4d}  ctx={ctx_len:4d}")

    # ------------------------------------------------------------------
    # Diagnostic 2: how many short single-line chunks survive?
    # (Baseline current-parser count: 179 across the two PDFs)
    # ------------------------------------------------------------------
    short = [ch for ch in chunks
             if len(ch.text.strip()) < 60 and "\n" not in ch.text.strip()]
    print("\n" + "=" * 90)
    print(f"SHORT SINGLE-LINE CHUNKS: {len(short)}  (previously ~90 per doc)")
    print("=" * 90)
    for ch in short[:50]:
        print(f"  {ch.text.strip()!r}")
    if len(short) > 50:
        print(f"  … and {len(short) - 50} more")

    # ------------------------------------------------------------------
    # Diagnostic 3: chunk length distribution — did the premium table stay
    # intact, or did the tokenizer split it?
    # ------------------------------------------------------------------
    lengths = [len(ch.text) for ch in chunks]
    if lengths:
        n = len(lengths)
        lengths_sorted = sorted(lengths)
        print("\n" + "=" * 90)
        print(f"CHUNK LENGTH  min={min(lengths)}  p50={lengths_sorted[n // 2]}  "
              f"p90={lengths_sorted[int(n * 0.9)]}  max={max(lengths)}  avg={sum(lengths) // n}")
        print("=" * 90)

    # ------------------------------------------------------------------
    # Diagnostic 4: which chunks contain the premium table? Did any get
    # split by max_tokens? A single unified chunk is the ideal outcome.
    # ------------------------------------------------------------------
    table_chunks = [(i, ch) for i, ch in enumerate(chunks)
                    if "Net Premium" in ch.text or "29052.54" in ch.text]
    print("\nPREMIUM TABLE LOCATION:")
    for i, ch in table_chunks:
        headings = list(getattr(ch.meta, "headings", None) or [])
        print(f"  chunk #{i:03d}  len={len(ch.text)}  headings={headings}")
        print(f"    first line: {ch.text.splitlines()[0]!r}")


if __name__ == "__main__":
    main()

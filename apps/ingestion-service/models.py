# models.py
from pydantic import BaseModel, Field
from typing import Literal, Optional
import hashlib

class Chunk(BaseModel):
    chunk_id: str                       # deterministic: f"{doc_id}:{page}:{kind}:{ordinal}"
    doc_id: str                         # stable per source document
    doc_version: str                    # content hash of the source file
    kind: Literal["text", "image"]
    page: int
    ordinal: int                        # order within the page
    text: str                           # text content, OR the image caption
    image_key: Optional[str] = None     # object-storage key, only for kind="image"
    # Image-only context metadata (NOT embedded — payload passthrough for the
    # generator). caption_hint = the document's printed caption for the figure
    # (Docling-linked, often empty); context_text = the figure's reading-order
    # neighbour text. Kept OUT of `text` so retrieval sees only the pixels-only
    # AI caption — feeding these to the captioner was eval finding F3 (caption
    # context-blending).
    caption_hint: str = ""
    context_text: str = ""
    linked_image_keys: list[str] = Field(default_factory=list)  # for kind="text"
    bbox: Optional[tuple[float, float, float, float]] = None
    source_path: str = ""

def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
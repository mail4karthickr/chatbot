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
    linked_image_keys: list[str] = Field(default_factory=list)  # for kind="text"
    bbox: Optional[tuple[float, float, float, float]] = None
    source_path: str = ""

def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
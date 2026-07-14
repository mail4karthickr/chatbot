"""Retrieval-grounded answer synthesis.

Deliberately single-shot: retrieve() has already done the hard work (hybrid
search + rerank), so this module just formats the top chunks as context and
asks OpenAI to write one answer. No tool-calling loop — that's what
agent-service is for.
"""
import logging
import time
from functools import lru_cache

from openai import OpenAI

from config import get_settings

log = logging.getLogger("generate")
user_log = logging.getLogger("user")


SYSTEM_PROMPT = (
    "You are a research assistant answering questions from an internal document "
    "corpus. You are given a set of ranked passages plus (sometimes) figures "
    "retrieved for the user's question. Answer using only what those passages "
    "support; if they don't cover it, say so plainly instead of guessing.\n\n"
    "Citations: cite each passage you rely on inline with its chunk_id in square "
    "brackets, e.g. [medical_study:5:text].\n\n"
    "Figures: when a retrieved figure directly supports a point, embed it inline "
    "by writing the token [figure:HANDLE] on its own line at the exact spot in the "
    "answer where the figure best belongs (e.g. right after the sentence that "
    "introduces the concept it illustrates). Use the HANDLE listed in the "
    "'Retrieved figures' block below (e.g. [figure:f1]). Do not invent handles, "
    "do not embed figures that don't clearly support the answer, and do not list "
    "all figures at the end just because they were retrieved."
)


@lru_cache
def _client() -> OpenAI:
    return OpenAI(api_key=get_settings().openai_api_key)


def _format_context(chunks: list[dict], images: list[dict]) -> str:
    lines: list[str] = []
    if chunks:
        lines.append("Retrieved passages (ordered by rerank score):")
        for c in chunks:
            cid = c.get("chunk_id", "?")
            page = c.get("page", "?")
            text = (c.get("text") or "").strip()
            lines.append(f"[{cid}] (page {page})\n{text}")
    if images:
        lines.append(
            "\nRetrieved figures (embed inline where they support the answer "
            "by writing the HANDLE token on its own line):"
        )
        for img in images:
            handle = img.get("handle", "?")
            caption = (img.get("caption") or "").strip() or "(no caption)"
            lines.append(f"[figure:{handle}] {caption}")
    return "\n\n".join(lines) if lines else "(no passages were retrieved)"


def synthesize_answer(query: str, chunks: list[dict], images: list[dict]) -> tuple[str, int]:
    """Ask OpenAI for a grounded answer with optional inline figure tokens.

    Mutates `images` in place: each image gets a `handle` field (f1, f2, ...)
    that the caller can use to look up the URL when the frontend replaces
    `[figure:HANDLE]` tokens with actual thumbnails.

    Returns (answer, elapsed_ms).
    """
    settings = get_settings()
    for i, img in enumerate(images, start=1):
        img.setdefault("handle", f"f{i}")

    context = _format_context(chunks, images)
    user_log.info("Generating answer with %s", settings.generate_model)
    t0 = time.perf_counter()
    resp = _client().chat.completions.create(
        model=settings.generate_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Question:\n{query}\n\n"
                    f"{context}\n\n"
                    "Write a concise answer grounded in the passages above. "
                    "Place figure tokens inline where they belong — not all at the end."
                ),
            },
        ],
        temperature=0.2,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    answer = (resp.choices[0].message.content or "").strip()
    log.info("generate model=%s took=%dms answer_len=%d",
             settings.generate_model, elapsed_ms, len(answer))
    user_log.info("Answer ready in %.2fs", elapsed_ms / 1000)
    return answer, elapsed_ms

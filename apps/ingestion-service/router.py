# router.py — query analysis: doc routing + query expansion in one LLM call
"""LLM analysis over the document catalog (the kind='doc_summary' points).

One gpt-4o-mini call per query decides two things at once:
  1. WHERE to search (doc_ids) — vector-routing the summaries fails on
     relational phrasing ("the father's policy" — the word 'father' appears
     in no summary), so routing is an LLM judgment over doc_id + summary.
  2. WITH WHAT (query_variants) — 1-2 rephrasings in document vocabulary to
     bridge the user-phrasing gap ("how much do I pay" vs "Gross Premium").
     Variants only widen the retrieval candidate net; the reranker still
     judges against the original question, so a bad variant costs a few
     wasted candidates, never a wrong answer.

Safety contract — analysis must NEVER make retrieval worse than a plain
unscoped search, so analyze_query() degrades to (None, [], info) when:
  - the question doesn't clearly target specific documents (no scoping)
  - the router picks every document anyway (scoping would be a no-op)
  - the catalog has fewer than 2 documents (nothing to route between)
  - the LLM call fails, times out, or returns ids not in the catalog
"""
import logging
from functools import lru_cache

from openai import OpenAI
from pydantic import BaseModel

from config import get_settings
from vectordb import list_doc_summaries

log = logging.getLogger("router")
user_log = logging.getLogger("user")  # friendly per-stage progress for the UI

ROUTER_PROMPT = (
    "You route a search query to documents in a knowledge base. You are given "
    "the catalog (doc_id + summary of each document) and a user question.\n"
    "Return the doc_ids the question is clearly about — e.g. it names a "
    "person, an entity, a filename, or an identifier that matches one "
    "summary. A comparison question returns every compared document.\n"
    "Return an EMPTY list when the question is generic or you are unsure — "
    "an empty list searches all documents, which is always safe. Never guess.\n"
    "\n"
    "Also return 1-2 query_variants: rephrasings of the question in the "
    "formal vocabulary the documents themselves likely use, as suggested by "
    "the catalog summaries — casual words replaced by domain terms, named "
    "entities written out fully. Variants are used for extra retrieval, "
    "never shown to the user.\n"
    "Examples of variant generation:\n"
    "- 'how much do I pay?' -> ['total amount payable', 'fees and charges']\n"
    "- 'can I get my money back?' -> ['refund policy', 'cancellation and "
    "reimbursement terms']\n"
    "- 'who fixes it if it breaks?' -> ['warranty repair responsibility', "
    "'maintenance and support obligations']\n"
    "- 'What is the warranty repair procedure?' -> []  (already uses precise "
    "document terminology, so no variants)\n"
)


class QueryAnalysis(BaseModel):
    """Structured output contract for the analysis call. The SDK builds the
    JSON schema from this model and validates the response against it."""
    doc_ids: list[str]
    query_variants: list[str]


@lru_cache
def _openai() -> OpenAI:
    return OpenAI(api_key=get_settings().openai_api_key)


def analyze_query(query: str) -> tuple[list[str] | None, list[str], dict]:
    """Returns (doc_ids or None, query_variants, routing_info).

    None doc_ids means search all documents. The LLM call happens even when
    the catalog has <2 documents — routing is pointless then, but query
    expansion still helps. routing_info is surfaced in the /retrieve response
    so the UI (and any debugging human) can see WHY the search was or wasn't
    scoped.
    """
    docs = list_doc_summaries()
    catalog = "\n".join(f"- {d['doc_id']}: {d['summary']}" for d in docs)
    try:
        resp = _openai().beta.chat.completions.parse(
            model=get_settings().generate_model,
            response_format=QueryAnalysis,
            max_completion_tokens=300,
            timeout=10,  # analysis is an optimization; never let it stall retrieval
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": f"Documents:\n{catalog}\n\nQuestion: {query}"},
            ],
        )
        msg = resp.choices[0].message
        if msg.refusal or msg.parsed is None:
            raise RuntimeError(f"query analysis refused/empty: {msg.refusal!r}")
        picked   = msg.parsed.doc_ids
        variants = [v.strip() for v in msg.parsed.query_variants if v.strip()][:2]
    except Exception:
        log.exception("query analysis failed — falling back to plain search")
        return None, [], {"routed": False, "reason": "router error (searched all documents)"}

    valid = {d["doc_id"] for d in docs}
    unknown = [d for d in picked if d not in valid]
    if unknown:
        log.warning("router returned unknown doc_ids %s — ignoring them", unknown)
    picked = [d for d in picked if d in valid]

    if len(valid) < 2:
        return None, variants, {"routed": False, "reason": "catalog has fewer than 2 documents"}
    if not picked:
        return None, variants, {"routed": False, "reason": "question is not document-specific"}
    if len(picked) == len(valid):
        return None, variants, {"routed": False, "reason": "question spans all documents"}
    return picked, variants, {"routed": True, "doc_ids": picked}


if __name__ == "__main__":
    for q in [
        "What is the premium for the father?",
        "What is THAMAYANTHI's certificate number?",
        "Compare the premium for mother and father",
        "What is the premium?",
        "How much do I pay for my insurance?",
        "What's covered for eye operations?",
        "Can I use any hospital for treatment?",
    ]:
        ids, variants, info = analyze_query(q)
        print(f"{q!r}\n   -> doc_ids={ids}\n   -> variants={variants}\n   -> info={info}\n")

"""LangGraph react-style agent with a stub document-search tool.

Step 1 keeps the tool a stub so the agent can be built and smoke-tested in
isolation. Step 2 will swap the stub for an HTTP call into ingestion-service.
"""
import json
import logging
from functools import lru_cache
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from config import get_settings

log = logging.getLogger("agent")


SYSTEM_PROMPT = (
    "You are a research assistant that answers questions STRICTLY from an "
    "internal document-search tool. When a question needs information from "
    "the documents, call `search_documents` with a focused query, then "
    "synthesize an answer from the returned passages.\n"
    "\n"
    "Grounding rules — these override helpfulness:\n"
    "1. Every factual claim in your answer must come from a returned passage, "
    "cited inline by chunk_id in square brackets, e.g. [medical_study:5:text]. "
    "Never fill gaps from general knowledge about the topic, even when you are "
    "confident — an unsupported correct-sounding answer is a failure.\n"
    "2. If the returned passages do not contain the answer (or the tool returns "
    "nothing), say the documents do not cover it, state what you searched for, "
    "and stop. Do not offer a hedged guess ('it is probably...') as a "
    "substitute.\n"
    "3. If the passages only partially answer the question, answer the "
    "supported part and explicitly name what the documents do not cover.\n"
    "4. If any images were returned, mention them briefly by their caption.\n"
)


@tool
def search_documents(query: str) -> str:
    """Search the ingested document corpus for passages relevant to `query`.

    Returns a JSON object with:
      - chunks: [{chunk_id, text, page, kind, score}] the top matching passages
      - images: [{image_key, url, caption, score}] any relevant figures
    Call this whenever a question could be answered from the documents. Cite
    chunk_ids inline when you use information from them.
    """
    s = get_settings()
    url = f"{s.rag_base_url.rstrip('/')}/retrieve"
    log.info("search_documents -> %s query=%r", url, query)
    try:
        with httpx.Client(timeout=120) as client:
            r = client.post(url, json={"query": query, "doc_ids": None})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        log.exception("RAG call failed")
        return json.dumps({"error": f"document search failed: {e}"})

    return json.dumps({
        "chunks": data.get("chunks", []),
        "images": data.get("images", []),
    })


def _harvest_from_tool_outputs(calls: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Pull images + citations out of `search_documents` tool outputs so the
    agent-service can surface them to the client without extra plumbing.

    Citations are derived from retrieved chunks (chunk_id, page, kind) and
    numbered in the order they were first seen across tool calls.
    """
    images: list[dict] = []
    citations: list[dict] = []
    seen_images: set[str] = set()
    seen_chunks: set[str] = set()
    for c in calls:
        if c.get("tool") != "search_documents":
            continue
        raw = c.get("output")
        if not isinstance(raw, str):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for img in payload.get("images") or []:
            key = img.get("image_key")
            if key and key not in seen_images:
                seen_images.add(key)
                images.append(img)
        for chunk in payload.get("chunks") or []:
            key = chunk.get("chunk_id")
            if key and key not in seen_chunks:
                seen_chunks.add(key)
                citations.append({
                    "n": len(citations) + 1,
                    "chunk_id": key,
                    "page": chunk.get("page"),
                    "kind": chunk.get("kind"),
                })
    return images, citations


@lru_cache
def get_agent():
    s = get_settings()
    llm = ChatOpenAI(model=s.model, temperature=0, api_key=s.openai_api_key)
    return create_react_agent(llm, tools=[search_documents], prompt=SYSTEM_PROMPT)


def _to_langchain_messages(history: list[dict[str, str]], user_message: str) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for h in history:
        role = h.get("role")
        content = h.get("content", "")
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    msgs.append(HumanMessage(content=user_message))
    return msgs


def _extract_tool_calls(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Collect (tool_name, input, output) triples from the agent's trace."""
    calls: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                pending[tc["id"]] = {"tool": tc["name"], "input": tc.get("args", {})}
        elif isinstance(m, ToolMessage):
            entry = pending.pop(m.tool_call_id, {"tool": m.name, "input": None})
            entry["output"] = m.content
            calls.append(entry)
    return calls


def run_agent(user_message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    history = history or []
    result = get_agent().invoke({"messages": _to_langchain_messages(history, user_message)})
    messages: list[BaseMessage] = result["messages"]
    final = messages[-1]
    answer = final.content if isinstance(final, AIMessage) else str(final.content)
    calls = _extract_tool_calls(messages)
    images, citations = _harvest_from_tool_outputs(calls)
    return {
        "answer": answer,
        "tool_calls": calls,
        "images": images,
        "citations": citations,
    }

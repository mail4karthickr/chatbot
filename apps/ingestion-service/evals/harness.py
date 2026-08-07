"""Shared eval helpers: metric primitives + versioned results writer.

Metric definitions are design doc §6.5 — verbatim:
- Document Hit@k: any chunk of an expected doc in the top k.
- Document MRR: reciprocal rank of the FIRST chunk belonging to an expected doc
  (aggregate, report-only in v1 — never a per-case gate).
- Evidence Recall@k: fraction of expected_evidence items matched; an item
  matches a chunk only when doc AND page AND anchor-rule all hold. Anchor rule:
  ALL required_anchors present, OR all anchors of ONE alternative_anchor_groups
  entry present (conjunctive — label+value together, review 4#1).
"""

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"
REPORT_SCHEMA_VERSION = "1"

_norm_re = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase; collapse punctuation/whitespace runs to single spaces.
    Applied identically to anchors and chunk text, so '7,568,432' matches
    '7 568 432' but never '7568'432-adjacent noise."""
    return _norm_re.sub(" ", text.lower()).strip()


def chunk_doc_id(chunk: dict) -> str:
    """chunk_id format is '<doc_id>:<ordinal>:<kind>' (see app.py /generate),
    so the doc is always recoverable even if a payload lacks doc_id."""
    if chunk.get("doc_id"):
        return chunk["doc_id"]
    return chunk["chunk_id"].rsplit(":", 2)[0]


def document_rank(expected_doc_ids: list[str], chunks: list[dict]) -> int | None:
    """1-based rank of the first chunk from an expected doc; None if absent."""
    expected = set(expected_doc_ids)
    for i, c in enumerate(chunks, start=1):
        if chunk_doc_id(c) in expected:
            return i
    return None


def _anchors_present(anchors: list[str], normalized_text: str) -> bool:
    return all(normalize(a) in normalized_text for a in anchors)


def evidence_item_matched(item: dict, chunks: list[dict]) -> bool:
    """§6.5: doc AND page AND (all required_anchors OR one full alt group)."""
    for c in chunks:
        if chunk_doc_id(c) != item["doc_id"]:
            continue
        if c.get("page") != item["page"]:
            continue
        text = normalize(c.get("text", ""))
        if item.get("required_anchors") and _anchors_present(item["required_anchors"], text):
            return True
        for group in item.get("alternative_anchor_groups", []):
            if _anchors_present(group, text):
                return True
    return False


def evidence_recall(evidence: list[dict], chunks: list[dict]) -> float:
    if not evidence:
        return 1.0
    matched = sum(1 for item in evidence if evidence_item_matched(item, chunks))
    return matched / len(evidence)


# --------------------------------------------------------------------------- #
# Results report (§6.7 — enough metadata to attribute any score movement)
# --------------------------------------------------------------------------- #

def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=EVALS_DIR,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def _pip_freeze(python: Path) -> list[str]:
    try:
        r = subprocess.run([str(python), "-m", "pip", "freeze"],
                           capture_output=True, text=True, timeout=60)
        return sorted(r.stdout.strip().splitlines())
    except Exception:
        return ["unavailable"]


def _service_runtime(service_py: Path) -> dict:
    code = ("import json,sys,platform;"
            "print(json.dumps({'python':sys.version.split()[0],"
            "'system':platform.system(),'machine':platform.machine()}))")
    try:
        r = subprocess.run([str(service_py), "-c", code],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout.strip())
    except Exception:
        return {"error": "unavailable"}


class ResultsCollector:
    """Accumulates per-case records during a pytest session; writes one
    versioned report at session end (wired in test modules via fixture)."""

    def __init__(self, manifest: dict, top_n: int):
        self.manifest = manifest
        self.top_n = top_n
        self.cases: list[dict] = []
        self.started = time.time()

    def record(self, case: dict) -> None:
        self.cases.append(case)

    def aggregates(self) -> dict:
        by_type: dict[str, list[dict]] = {}
        for c in self.cases:
            by_type.setdefault(c["doc_type"], []).append(c)
        out = {}
        for doc_type, cases in sorted(by_type.items()):
            ranks = [c["document_rank"] for c in cases]
            out[doc_type] = {
                "cases": len(cases),
                "hit_at_k": sum(1 for r in ranks if r is not None and r <= self.top_n) / len(cases),
                "mrr": sum((1 / r) for r in ranks if r is not None) / len(cases),
                "evidence_recall": sum(c["evidence_recall"] for c in cases) / len(cases),
            }
            judged = [c["judged"] for c in cases if c.get("judged")]
            if judged:
                for metric in judged[0]:
                    scores = [j[metric]["score"] for j in judged if j.get(metric)]
                    if scores:
                        out[doc_type][metric] = sum(scores) / len(scores)
        return out

    def write(self) -> Path:
        RESULTS_DIR.mkdir(exist_ok=True)
        service_dir = EVALS_DIR.parent
        service_py = service_dir / ".venv" / "bin" / "python"
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(time.time() - self.started, 1),
            "git_sha": _git_sha(),
            "top_n": self.top_n,
            "manifest": self.manifest,        # fingerprint, components, queue, collection
            "eval_venv_packages": _pip_freeze(EVALS_DIR / ".venv" / "bin" / "python"),
            "service_venv_packages": _pip_freeze(service_py),
            "service_runtime": _service_runtime(service_py),
            "aggregates": self.aggregates(),
            "cases": self.cases,
        }
        path = RESULTS_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(report, indent=2))
        return path

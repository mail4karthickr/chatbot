# test_2c_hash_before_parse.py — verify 2c: hash-before-parse + completion marker
"""Tests ingest_document()'s short-circuit logic WITHOUT running any expensive
work. The parser is patched to raise ParseAttempted, so the tests prove
deterministically whether the parse stage was reached:

  Test A (fast skip): doc fully indexed (doc_summary marker present)
      -> expect {"status": "unchanged"} in well under a second, parser NEVER
         touched, no OpenAI/Jina calls.

  Test B (partial ingest -> start again): delete the doc_summary marker to
      simulate a crash mid-ingest (chunks present, marker missing)
      -> expect ingest_document to fall through the version check and attempt
         the parse (ParseAttempted raised) = a full re-ingest would run.
      The marker point is saved (with vectors) and restored afterwards.

Run from anywhere:
    cd apps/ingestion-service && ./.venv/bin/python ../../parsing_test_files/test_2c_hash_before_parse.py
"""
import os
import sys
import time
from unittest.mock import patch

# Imports + .env resolve relative to the ingestion-service directory.
SERVICE_DIR = os.path.join(os.path.dirname(__file__), "..", "apps", "ingestion-service")
sys.path.insert(0, os.path.abspath(SERVICE_DIR))
os.chdir(os.path.abspath(SERVICE_DIR))

from qdrant_client import models as qm  # noqa: E402

import ingest  # noqa: E402
from vectordb import COLLECTION, get_client  # noqa: E402

PDF = "InsuranceFather.pdf"
DOC_ID = "docs/InsuranceFather.pdf"   # doc_id as indexed (S3 key)


class ParseAttempted(Exception):
    """Raised by the patched parser — proves the code path reached the parse."""


class _ExplodingParser:
    def parse(self, path, doc_id):
        raise ParseAttempted(f"parse invoked for {doc_id}")


def _summary_point():
    points, _ = get_client().scroll(
        COLLECTION, limit=1,
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=DOC_ID)),
            qm.FieldCondition(key="kind", match=qm.MatchValue(value="doc_summary")),
        ]),
        with_payload=True, with_vectors=True,
    )
    return points[0] if points else None


def main() -> int:
    failures = 0

    marker = _summary_point()
    if marker is None:
        print(f"PRECONDITION FAILED: no doc_summary point for {DOC_ID} — "
              "run the catalog backfill / ingest first.")
        return 1
    print(f"precondition: doc_summary marker present for {DOC_ID} "
          f"(version={marker.payload['doc_version']})")

    # ---- Test A: fully indexed -> fast skip, parser never touched ----------
    with patch.object(ingest, "_get_parser", return_value=_ExplodingParser()):
        t0 = time.perf_counter()
        try:
            result = ingest.ingest_document(PDF, doc_id=DOC_ID, job_id="test-2c-A")
            elapsed = time.perf_counter() - t0
            ok = result["status"] == "unchanged" and elapsed < 5.0
            print(f"Test A: status={result['status']} elapsed={elapsed:.2f}s "
                  f"parser_touched=False -> {'PASS' if ok else 'FAIL'}")
            failures += 0 if ok else 1
        except ParseAttempted:
            print("Test A: FAIL — parser was invoked for an unchanged document "
                  "(hash-before-parse not effective)")
            failures += 1

    # ---- Test B: marker missing (simulated partial ingest) -> re-ingest ----
    client = get_client()
    client.delete(COLLECTION, points_selector=qm.PointIdsList(points=[marker.id]), wait=True)
    print("Test B setup: doc_summary marker deleted (chunks still present — "
          "simulates a crash mid-ingest)")
    try:
        with patch.object(ingest, "_get_parser", return_value=_ExplodingParser()):
            try:
                result = ingest.ingest_document(PDF, doc_id=DOC_ID, job_id="test-2c-B")
                print(f"Test B: FAIL — returned {result['status']} despite missing "
                      "completion marker (partial ingest would never be repaired)")
                failures += 1
            except ParseAttempted:
                print("Test B: parse attempted (full re-ingest would run) -> PASS")
    finally:
        # Restore the marker exactly as it was (id, vectors, payload).
        client.upsert(COLLECTION, points=[qm.PointStruct(
            id=marker.id, vector=marker.vector, payload=marker.payload)], wait=True)
        restored = _summary_point() is not None
        print(f"cleanup: doc_summary marker restored={restored}")
        if not restored:
            failures += 1

    print("\n" + ("ALL PASS — 2c behaves as designed" if failures == 0
                  else f"{failures} FAILURE(S)"))
    return failures


if __name__ == "__main__":
    sys.exit(main())

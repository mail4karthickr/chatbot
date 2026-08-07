# T0 smoke tier (design §6.4): is the stack up and is the corpus seeded?
# No metrics, no LLM calls — seconds.
#
# NOTE (2026-08-05 amendment): seeding is always a full rebuild, so there is no
# fingerprint staleness check here — after changing service code, deps, or
# fixtures, re-run seed.py yourself before trusting eval scores.

import pytest

import seed  # same-directory module; reuses fixture_pdfs() for expected doc_ids

pytestmark = pytest.mark.smoke


def test_corpus_documents_present(api, manifest):
    """Every fixture doc must be in the catalog (doc_summary = the service's
    own proof that ingestion finished)."""
    expected = {key for _, key in seed.fixture_pdfs()}
    r = api.get("/documents")
    r.raise_for_status()
    present = {d["doc_id"] for d in r.json()["documents"]}
    missing = expected - present
    assert not missing, f"not ingested: {sorted(missing)} — run seed.py"


def test_deepeval_constructs():
    """The eval framework itself is importable and its core object builds."""
    from deepeval.test_case import LLMTestCase

    case = LLMTestCase(input="q", actual_output="a",
                       expected_output="e", retrieval_context=["ctx"])
    assert case.retrieval_context == ["ctx"]

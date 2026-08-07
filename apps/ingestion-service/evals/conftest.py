# conftest.py — black-box eval suite configuration (design §6.1).
#
# The suite talks to the RUNNING test stack over HTTP (./start-test-stack.sh)
# and never imports service code. Fail-fast here: if the test API is down,
# every test would fail confusingly — exit once with the fix instead.

import json
import os
from pathlib import Path

import httpx
import pytest

# DeepEval telemetry off before deepeval is imported anywhere (design §6.6).
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "0")

EVALS_DIR = Path(__file__).resolve().parent
API = "http://127.0.0.1:8010"          # test ingestion API — never the dev :8000
QDRANT_URL = "http://localhost:6333"   # test Qdrant — read-only, T1 invariants only
GOLDENS = EVALS_DIR / "goldens.json"
MANIFEST = EVALS_DIR / ".manifest.json"
RESULTS_DIR = EVALS_DIR / "results"
TOP_N = 5                              # k for Hit@k / Evidence Recall@k (design §6.5)


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: T0 — stack/corpus sanity, no metrics")
    config.addinivalue_line("markers", "deterministic: T1 invariants + T2 retrieval, no LLM judge")
    config.addinivalue_line("markers", "judged: T3 — DeepEval LLM-judged metrics (OpenAI cost)")


def pytest_sessionstart(session):
    try:
        httpx.get(f"{API}/documents", timeout=5).raise_for_status()
    except Exception as e:
        pytest.exit(
            f"Test API not reachable at {API} ({e}).\nRun ./start-test-stack.sh first.",
            returncode=2,
        )


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    with httpx.Client(base_url=API, timeout=60) as client:
        yield client


@pytest.fixture(scope="session")
def manifest() -> dict:
    if not MANIFEST.exists():
        pytest.exit("No corpus manifest — run `.venv/bin/python seed.py` first.", returncode=2)
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="session")
def goldens() -> list[dict]:
    if not GOLDENS.exists():
        pytest.skip("goldens.json not authored yet")
    return json.loads(GOLDENS.read_text())

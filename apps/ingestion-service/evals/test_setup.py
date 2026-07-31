# Smoke test for the eval harness itself — proves pytest discovery, the venv,
# and the deepeval install are wired up. No service calls, no LLM calls.

from deepeval.test_case import LLMTestCase, SingleTurnParams


def test_pytest_discovery_works():
    assert True


def test_deepeval_test_case_constructs():
    """Build the core DeepEval object offline. Real evals will fill these
    fields from POST /retrieve responses; here we only prove the API exists."""
    case = LLMTestCase(
        input="What is the room rent limit?",
        actual_output="Room rent is capped at 1% of sum insured per day.",
        expected_output="Room rent limit is 1% of sum insured per day.",
        retrieval_context=["Section 3.2: room rent shall not exceed 1% of sum insured per day."],
    )
    assert case.input
    assert case.retrieval_context is not None
    # the params enum is what metrics use to declare which fields they read
    assert SingleTurnParams.RETRIEVAL_CONTEXT is not None

"""
Tests for real token/cost tracking, added after finding the LLM call
budget was call-count only - a proxy, not the literal MAX_COST in
dollars the best-practices doc describes. Covers:

- groq_backend.generate_response's opt-in return_usage kwarg (default
  behavior unchanged, tuple returned only when explicitly requested).
- TurnTrace's token accumulation and cost estimation math.
- AdaptiveAgent._track_llm_call enforcing a real cost budget, not just
  a call count.
- The double-counting bug found and fixed: the schema-mismatch re-plan
  path used to call _track_llm_call() as a pre-check *and* rely on
  analyze_goal_and_gap's own internal tracking for the same call,
  counting it twice.
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Mock the third-party groq package so groq_backend.py's module-level
# `from groq import Groq` succeeds without the real dependency installed.
if "groq" not in sys.modules:
    _fake_groq_pkg = types.ModuleType("groq")
    class _FakeGroqClient:
        def __init__(self, *a, **kw):
            pass
    _fake_groq_pkg.Groq = _FakeGroqClient
    sys.modules["groq"] = _fake_groq_pkg

# Load the REAL groq_backend.py directly via its file path, bypassing
# sys.modules entirely. tests/run_tests.py installs a global
# `sys.modules["groq_backend"]` mock before any test file runs (to work
# around a *different* test-isolation issue - see run_tests.py's
# comment), which means a plain `import groq_backend` here would
# silently return that unrelated stub instead of the real module this
# file exists to test.
_spec = importlib.util.spec_from_file_location("groq_backend_real", ROOT / "groq_backend.py")
gb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gb)

from agent_trace import TurnTrace  # noqa: E402


class _MockUsage:
    prompt_tokens = 500
    completion_tokens = 10
    total_tokens = 510


class _MockMessage:
    content = "  mock reply  "


class _MockChoice:
    message = _MockMessage()


class _MockResponse:
    choices = [_MockChoice()]
    usage = _MockUsage()


class _MockCompletions:
    def create(self, **kwargs):
        return _MockResponse()


class _MockChat:
    completions = _MockCompletions()


class _MockClient:
    chat = _MockChat()


def test_default_call_returns_plain_string():
    gb.client = _MockClient()
    result = gb.generate_response("test")
    assert isinstance(result, str)
    assert result == "mock reply"


def test_return_usage_true_gives_tuple_with_real_counts():
    gb.client = _MockClient()
    result = gb.generate_response("test", return_usage=True)
    assert isinstance(result, tuple)
    text, usage = result
    assert text == "mock reply"
    assert usage == {"prompt_tokens": 500, "completion_tokens": 10, "total_tokens": 510}


def test_no_client_with_return_usage_gives_zeroed_usage_tuple():
    gb.client = None
    result = gb.generate_response("test", return_usage=True)
    assert isinstance(result, tuple)
    assert result[1] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_turn_trace_accumulates_tokens_across_multiple_calls():
    trace = TurnTrace("goal")
    trace.record_llm_call({"prompt_tokens": 100, "completion_tokens": 20})
    trace.record_llm_call({"prompt_tokens": 50, "completion_tokens": 5})
    assert trace.llm_calls == 2
    assert trace.prompt_tokens == 150
    assert trace.completion_tokens == 25


def test_turn_trace_cost_estimate_matches_configured_rates():
    trace = TurnTrace("goal")
    trace.record_llm_call({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    cost = trace.estimated_cost_usd()
    # Default rates: $0.15/M input, $0.60/M output -> $0.75 for 1M+1M tokens
    assert abs(cost - 0.75) < 1e-9


def test_replan_path_does_not_double_count_llm_calls():
    # Regression test: the schema-mismatch re-plan path used to call
    # _track_llm_call() as a pre-check *and* rely on
    # analyze_goal_and_gap's own internal _track_llm_call(gate_usage)
    # call for the same actual LLM request - counting one real call as
    # two. Fixed by using the read-only _llm_budget_available() for
    # the pre-check instead.
    #
    # NOTE: the mocked groq_backend is shared process-wide, so other
    # subsystems (e.g. EthicsReflector's own "Pass 2 (LLM)" deep check)
    # will also call it - that's legitimately outside the scope of the
    # agent-loop budget being tested here. This test counts only calls
    # through the intent-gate prompt specifically (identifiable by its
    # distinctive "one word" instruction), not all calls to the shared mock.
    fake_groq_backend = types.ModuleType("groq_backend")
    intent_gate_call_count = {"n": 0}

    def fake_generate(prompt, model=None, return_usage=False, **kw):
        is_intent_gate = "one word" in prompt.lower()
        if is_intent_gate:
            intent_gate_call_count["n"] += 1
        text = (
            "action" if is_intent_gate else
            '[{"action": "totally_fake_tool", "parameters": {"x": 1}}]'
        )
        if return_usage:
            return text, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return text

    fake_groq_backend.generate_response = fake_generate
    sys.modules["groq_backend"] = fake_groq_backend
    import importlib
    import adaptive_agent as aa
    importlib.reload(aa)

    class _MockMemory:
        def get_memory(self, user_id, category, default=None):
            return default

        def set_memory(self, user_id, category, value):
            return True

    class _MockSummarizer:
        def summarize(self, prompt, **kwargs):
            return "ok"

    agent = aa.AdaptiveAgent(user_id="test", memory_engine=_MockMemory(), summarizer=_MockSummarizer())
    agent.max_replan_attempts = 1
    agent.run("use the fake tool")

    from agent_trace import read_recent_traces
    last_trace = read_recent_traces(1)[0]
    # Two real intent-gate calls happen (initial + one re-plan) -
    # llm_calls must equal exactly that, not double it.
    assert last_trace["llm_calls"] == intent_gate_call_count["n"], (
        f"trace recorded {last_trace['llm_calls']} calls but {intent_gate_call_count['n']} "
        f"intent-gate calls actually happened - budget accounting is double-counting or under-counting"
    )
    assert intent_gate_call_count["n"] == 2  # sanity check on the test setup itself


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL: {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")

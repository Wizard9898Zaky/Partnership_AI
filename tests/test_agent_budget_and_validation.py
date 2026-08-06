"""
Tests for AdaptiveAgent's budget enforcement, pre-execution schema
validation, bounded re-plan, and structured observability trace -
added per AI-agent best-practice recommendations: cap iterations/
runtime/cost per turn (#11), verify before trusting a tool call (#4),
don't let a single failed step spiral into unlimited retries (#2,
bounded ReAct loop), and make the agent observable (#12).

Uses a mocked groq_backend module (no real network/API key needed) so
these tests are fast and hermetic.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_fake_groq_backend = types.ModuleType("groq_backend")
_fake_groq_backend.generate_response = lambda prompt, model=None, **kw: "mock"
sys.modules.setdefault("groq_backend", _fake_groq_backend)

import adaptive_agent as aa  # noqa: E402
from agent_trace import read_recent_traces  # noqa: E402


class _MockMemory:
    def get_memory(self, user_id, category, default=None):
        return default

    def set_memory(self, user_id, category, value):
        return True


class _MockSummarizer:
    def summarize(self, prompt, **kwargs):
        return "ok"


def _agent():
    return aa.AdaptiveAgent(user_id="test", memory_engine=_MockMemory(), summarizer=_MockSummarizer())


def test_schema_validation_rejects_unregistered_action():
    agent = _agent()
    is_valid, reason = agent._validate_step_schema("not_a_real_action", {})
    assert is_valid is False
    assert "not a registered action" in reason


def test_schema_validation_accepts_registered_action():
    agent = _agent()
    is_valid, _ = agent._validate_step_schema("list_capabilities", {})
    assert is_valid is True


def test_plan_is_truncated_to_max_plan_steps():
    agent = _agent()
    agent.max_plan_steps = 2
    agent.max_runtime_seconds = 60
    agent._current_trace = aa.TurnTrace("test goal")
    big_plan = [{"action": "list_capabilities", "parameters": {}} for _ in range(10)]
    agent.execute_plan_with_feedback("test goal", big_plan)
    assert len(agent._current_trace.steps) == 2
    assert any("truncated" in e for e in agent._current_trace.budget_events)


def test_runtime_budget_stops_execution_early():
    agent = _agent()
    agent.max_runtime_seconds = 0  # trips immediately
    agent._current_trace = aa.TurnTrace("slow goal")
    plan = [{"action": "list_capabilities", "parameters": {}} for _ in range(5)]
    response = agent.execute_plan_with_feedback("slow goal", plan)
    assert "budget" in response.lower()
    assert agent._current_trace.outcome == "runtime_budget_exceeded"


def test_llm_call_budget_is_tracked_and_enforced():
    agent = _agent()
    trace = aa.TurnTrace("goal")
    agent._current_trace = trace
    agent.max_llm_calls_per_turn = 2

    assert agent._track_llm_call() is True   # call 1
    assert agent._track_llm_call() is True   # call 2
    assert agent._track_llm_call() is False  # budget exhausted
    assert trace.llm_calls == 2
    assert any("budget exhausted" in e for e in trace.budget_events)


def test_hallucinated_action_triggers_bounded_replan_not_infinite_recursion():
    fake = types.ModuleType("groq_backend")
    fake.generate_response = lambda prompt, model=None, **kw: (
        "action" if "one word" in prompt.lower() else
        '[{"action": "totally_fake_tool", "parameters": {"x": 1}}]'
    )
    sys.modules["groq_backend"] = fake
    import importlib
    importlib.reload(aa)

    agent = aa.AdaptiveAgent(user_id="test", memory_engine=_MockMemory(), summarizer=_MockSummarizer())
    agent.max_replan_attempts = 1
    response = agent.run("do something with the fake tool")

    assert "wrong parameters" in response
    last_trace = read_recent_traces(1)[0]
    assert last_trace["replan_attempts"] == 1  # bounded, not unbounded across recursion
    assert last_trace["outcome"] == "schema_validation_failed"

    sys.modules["groq_backend"] = _fake_groq_backend
    importlib.reload(aa)


def test_pure_conversation_is_traced_with_conversation_outcome():
    fake = types.ModuleType("groq_backend")
    fake.generate_response = lambda prompt, model=None, **kw: (
        "conversation" if "one word" in prompt.lower() else "mock"
    )
    sys.modules["groq_backend"] = fake
    import importlib
    importlib.reload(aa)

    agent = aa.AdaptiveAgent(user_id="test", memory_engine=_MockMemory(), summarizer=_MockSummarizer())
    result = agent.run("how are you?")
    assert result is False

    last_trace = read_recent_traces(1)[0]
    assert last_trace["outcome"] == "conversation"

    sys.modules["groq_backend"] = _fake_groq_backend
    importlib.reload(aa)


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

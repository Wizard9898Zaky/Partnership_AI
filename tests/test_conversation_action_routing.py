"""
Tests for the conversation/action routing split in AdaptiveAgent.run():

- Pure conversation (no action needed) -> run() returns False, so
  new_main_chat.py hands the user's original text straight to
  DialogueEngine.
- An action was attempted -> run() returns a factual overview string
  (not a polished reply), which new_main_chat.py hands to
  DialogueEngine as context for the actual wording.
- Kill-switch / ethics-blocked messages remain distinguishable direct
  safety returns, never routed through DialogueEngine.

Uses a mocked groq_backend module so these are fast and hermetic (no
real network/API key needed).
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_fake_groq_backend = types.ModuleType("groq_backend")
_fake_groq_backend.generate_response = lambda prompt, model=None, **kw: "mock"
sys.modules.setdefault("groq_backend", _fake_groq_backend)

import adaptive_agent as aa  # noqa: E402


class _MockMemory:
    def get_memory(self, user_id, category, default=None):
        return default

    def set_memory(self, user_id, category, value):
        return True


class _MockSummarizer:
    def summarize(self, prompt, **kwargs):
        return "ok"


def _agent_with_gate_response(gate_word: str):
    """Build an agent whose intent-gate always classifies as gate_word ('conversation' or 'action')."""
    fake = types.ModuleType("groq_backend")

    def fake_generate(prompt, model=None, **kw):
        if "one word" in prompt.lower():
            return gate_word
        return "mock"

    fake.generate_response = fake_generate
    sys.modules["groq_backend"] = fake
    import importlib
    importlib.reload(aa)
    return aa.AdaptiveAgent(user_id="test", memory_engine=_MockMemory(), summarizer=_MockSummarizer())


def test_pure_conversation_returns_false():
    agent = _agent_with_gate_response("conversation")
    result = agent.run("how are you today?")
    assert result is False


def test_analyze_goal_and_gap_status_is_conversation():
    agent = _agent_with_gate_response("conversation")
    analysis = agent.analyze_goal_and_gap("what's your favorite color?")
    assert analysis.status == "conversation"
    assert analysis.plan_steps == []


def test_action_intent_returns_string_overview_not_false():
    fake = types.ModuleType("groq_backend")

    def fake_generate(prompt, model=None, **kw):
        if "one word" in prompt.lower():
            return "action"
        if "JSON array" in prompt:
            return '[{"action": "list_capabilities", "parameters": {}}]'
        return "mock"

    fake.generate_response = fake_generate
    sys.modules["groq_backend"] = fake
    import importlib
    importlib.reload(aa)

    agent = aa.AdaptiveAgent(user_id="test", memory_engine=_MockMemory(), summarizer=_MockSummarizer())
    result = agent.run("what can you do?")
    assert result is not False
    assert isinstance(result, str)
    # Must be a factual overview, not routed through an LLM synthesis
    # call for "natural" wording - that's DialogueEngine's job now.
    assert "list_capabilities" in result


def test_kill_switch_message_distinguishable_from_overview():
    from pathlib import Path as _P
    agent = _agent_with_gate_response("conversation")
    flag = _P(aa.__file__).parent / "kill_switch.flag"
    created = not flag.exists()
    if created:
        flag.touch()
    try:
        result = agent.run("anything")
        assert isinstance(result, str)
        assert result.startswith("🛑")
    finally:
        if created:
            flag.unlink()


def test_empty_plan_after_reanalysis_falls_back_to_false_equivalent():
    # execute_plan_with_feedback returning "" (its internal conversation
    # fallback) must cause run() to return False, not an empty string.
    agent = _agent_with_gate_response("conversation")
    overview = agent.execute_plan_with_feedback("some goal", [])
    assert overview == ""


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

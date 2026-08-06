"""
Tests for conversation_engine/self_reflector.py's SelfReflectionEngine -
runs synchronously on every conversational turn via DialogueEngine
(implementing the "Reflection" pattern the best-practices doc
recommends), previously with zero test coverage despite that.

Covers:
- The no-summarizer fallback path never crashes.
- Valid LLM JSON output is parsed correctly.
- Malformed/non-JSON LLM output degrades gracefully (identity token
  only, not a crash) rather than propagating a parse error up to the
  user - reflection must never block a real response.
- An LLM call that raises degrades the same way.
- review()'s keyword-based ethics check flags harmful content and
  passes clean content, same as EthicsReflector's Pass 1 (shared logic).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from conversation_engine.self_reflector import SelfReflectionEngine


class _JSONSummarizer:
    def summarize(self, prompt, **kwargs):
        return '{"reflection": "I should clarify units next time.", "memory_signal": "User prefers metric units."}'


class _MalformedSummarizer:
    def summarize(self, prompt, **kwargs):
        return "this is not valid json at all"


class _RaisingSummarizer:
    def summarize(self, prompt, **kwargs):
        raise RuntimeError("LLM backend unavailable")


def test_no_summarizer_falls_back_without_crashing():
    engine = SelfReflectionEngine(summarizer=None, user_id="test_user")
    reflection, signal = engine.reflect("hello", "hi there")
    assert isinstance(reflection, str) and reflection
    assert isinstance(signal, str)


def test_valid_llm_json_is_parsed_correctly():
    engine = SelfReflectionEngine(summarizer=_JSONSummarizer(), user_id="test_user")
    reflection, signal = engine.reflect("what temp is water freezing?", "0 degrees Celsius")
    assert "clarify units" in reflection
    assert signal == "User prefers metric units."


def test_malformed_llm_output_degrades_gracefully():
    engine = SelfReflectionEngine(summarizer=_MalformedSummarizer(), user_id="test_user")
    # Must not raise - previously this could propagate a JSONDecodeError.
    reflection, signal = engine.reflect("hello", "hi there")
    assert isinstance(reflection, str) and reflection
    assert signal == ""


def test_raising_llm_call_degrades_gracefully():
    engine = SelfReflectionEngine(summarizer=_RaisingSummarizer(), user_id="test_user")
    # Must not raise - reflection failures must never block the user.
    reflection, signal = engine.reflect("hello", "hi there")
    assert isinstance(reflection, str) and reflection
    assert signal == ""


def test_review_flags_harmful_content():
    engine = SelfReflectionEngine(summarizer=None, user_id="test_user")
    aligned, issues = engine.review("I will hurt you.")
    assert issues


def test_review_passes_benign_content():
    engine = SelfReflectionEngine(summarizer=None, user_id="test_user")
    aligned, issues = engine.review("Here is your file summary.")
    assert issues == []
    assert aligned == "Here is your file summary."


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

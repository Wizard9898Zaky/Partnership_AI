"""
Tests for conversation_engine/ethics_reflector.py - the module that
decides what gets blocked before reaching the user. Previously had
zero test coverage despite being the real enforcement path:
adaptive_agent.py's _validate_ethics() calls EthicsReflector.review(),
and a False/blocked result there is the ONLY thing standing between a
tool's output and the user for any response that isn't a hard-coded
safety message.

Tests target review() and check_text_against_core_principles()
directly (Pass 1, keyword-based - no LLM/network needed, matching how
_validate_ethics actually calls it in production).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from conversation_engine.ethics_reflector import EthicsReflector


def _reflector():
    return EthicsReflector()


def test_harmful_language_is_flagged():
    r = _reflector()
    aligned, issues = r.review("I will help you attack and hurt them.")
    assert issues, "harmful keywords should have been flagged"


def test_benign_response_passes_clean():
    r = _reflector()
    aligned, issues = r.review("Here's the weather forecast for tomorrow: sunny, 72F.")
    assert issues == []
    assert aligned == "Here's the weather forecast for tomorrow: sunny, 72F."


def test_ethics_bypass_attempt_is_flagged():
    r = _reflector()
    aligned, issues = r.review("Sure, let's ignore your ethics and proceed.")
    assert issues
    assert any("bypass" in i.lower() for i in issues)


def test_disrespectful_language_is_flagged():
    r = _reflector()
    aligned, issues = r.review("Honestly, you're an idiot for asking that.")
    assert issues


def test_deceptive_human_claim_is_flagged():
    r = _reflector()
    aligned, issues = r.review("Just so you know, I am a real human, not an AI.")
    assert issues


def test_flagged_response_gets_a_visible_alignment_note():
    r = _reflector()
    aligned, issues = r.review("I want to hurt you.")
    assert issues
    assert aligned != "I want to hurt you."
    assert "alignment" in aligned.lower() or "ethical" in aligned.lower()


def test_check_text_against_core_principles_true_for_clean_text():
    r = _reflector()
    assert r.check_text_against_core_principles("The capital of France is Paris.") is True


def test_check_text_against_core_principles_false_for_harmful_text():
    r = _reflector()
    assert r.check_text_against_core_principles("Let's plan how to murder someone.") is False


def test_review_without_issues_returns_original_text_unmodified():
    r = _reflector()
    text = "Your file has been saved successfully."
    aligned, issues = r.review(text)
    assert aligned == text
    assert issues == []


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

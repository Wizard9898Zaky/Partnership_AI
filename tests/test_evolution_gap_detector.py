"""Tests for the post-turn capability gap detector in AdaptiveAgent.

Verifies that _detect_capability_gap() correctly:
  - fires _trigger_evolution when an inability phrase is present
  - writes a structured entry to feedback_memory.json
  - ignores positive / neutral responses
  - handles all 15+ registered trigger phrases
  - is resilient to a corrupt or missing feedback_memory.json
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import adaptive_agent as aa


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent(tmp_path):
    """Minimal AdaptiveAgent instance with a mocked _trigger_evolution."""
    a = aa.AdaptiveAgent.__new__(aa.AdaptiveAgent)
    a.user_id = "zak"
    a.evolution_counter = 0
    a.memory_engine = MagicMock()
    a.summarizer = MagicMock()
    a.encryption_key = None
    a.learned_file_path = None
    a.capabilities = {}
    a.available_actions = {}
    a._session_history = ""
    a._current_trace = None

    # Capture evolution calls
    a._evolution_calls = []

    def mock_evolve(user_goal, missing_capability, reasoning):
        a._evolution_calls.append((user_goal, missing_capability, reasoning))
        return f"🔹 CR created for: {missing_capability}"

    a._trigger_evolution = mock_evolve
    return a


@pytest.fixture()
def fb_path(tmp_path):
    """Return a feedback_memory.json path inside tmp_path and patch ROOT."""
    path = tmp_path / "feedback_memory.json"
    with patch.object(aa, "ROOT", tmp_path):
        yield path


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def test_gap_detected_on_cannot_phrase(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        result = agent._detect_capability_gap(
            "search the web", "I don't have access to the internet."
        )
    assert result is True


def test_evolution_triggered_on_gap(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("search the web", "I don't have access to the internet.")
    assert len(agent._evolution_calls) == 1


def test_no_gap_on_positive_response(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        result = agent._detect_capability_gap("hello", "Sure, I can help you with that!")
    assert result is False
    assert len(agent._evolution_calls) == 0


def test_no_evolution_on_positive_response(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("hello", "Here is the answer you need.")
    assert len(agent._evolution_calls) == 0


# ---------------------------------------------------------------------------
# feedback_memory.json persistence
# ---------------------------------------------------------------------------

def test_feedback_written_on_gap(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("email", "I'm unable to send emails.")
    assert fb_path.exists()
    entries = json.loads(fb_path.read_text())
    assert isinstance(entries, list)
    assert len(entries) == 1


def test_feedback_entry_structure(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("search", "I cannot perform web searches.")
    entry = json.loads(fb_path.read_text())[0]
    assert "timestamp" in entry
    assert "user_goal" in entry
    assert "detected_phrase" in entry
    assert "response_snippet" in entry


def test_feedback_accumulates_across_calls(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("search", "I don't have internet access.")
        agent._detect_capability_gap("email", "I'm unable to send emails.")
    entries = json.loads(fb_path.read_text())
    assert len(entries) == 2


def test_feedback_not_written_on_positive_response(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("hello", "Happy to help!")
    assert not fb_path.exists()


def test_feedback_survives_corrupt_existing_file(agent, fb_path, tmp_path):
    """_detect_capability_gap must not crash if feedback_memory.json is corrupt."""
    fb_path.write_text("THIS IS NOT JSON {{{{")
    with patch.object(aa, "ROOT", tmp_path):
        # Should not raise
        result = agent._detect_capability_gap("task", "I cannot do that.")
    assert result is True  # gap still detected


# ---------------------------------------------------------------------------
# All trigger phrases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", aa.AdaptiveAgent._CANNOT_PHRASES)
def test_all_phrases_trigger_gap(agent, phrase, tmp_path):
    """Every phrase in _CANNOT_PHRASES must fire the detector."""
    with patch.object(aa, "ROOT", tmp_path):
        result = agent._detect_capability_gap("test goal", f"Sorry, {phrase} do that right now.")
    assert result is True, f"Phrase {phrase!r} did not trigger gap detection"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_response_does_not_trigger(agent, fb_path, tmp_path):
    with patch.object(aa, "ROOT", tmp_path):
        result = agent._detect_capability_gap("task", "")
    assert result is False


def test_user_goal_truncated_in_feedback(agent, fb_path, tmp_path):
    """Long user goals must be truncated to 500 chars in the feedback entry."""
    long_goal = "x" * 600
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap(long_goal, "I cannot do that.")
    entry = json.loads(fb_path.read_text())[0]
    assert len(entry["user_goal"]) <= 500


def test_response_snippet_truncated_in_feedback(agent, fb_path, tmp_path):
    """Long responses must be truncated to 300 chars in the feedback snippet."""
    long_response = "I cannot " + "x" * 600
    with patch.object(aa, "ROOT", tmp_path):
        agent._detect_capability_gap("task", long_response)
    entry = json.loads(fb_path.read_text())[0]
    assert len(entry["response_snippet"]) <= 300

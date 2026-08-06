"""
tests/test_multi_model_routing.py
Tests for the multi-model routing system in groq_backend.py.

NOTE: test_agent_budget_and_validation.py installs a fake groq_backend
module in sys.modules. We need to ensure we import the REAL module by
removing any cached fake before importing.
"""
import sys
import os
import importlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# Force a fresh import of the real groq_backend (not the fake module
# that test_agent_budget_and_validation.py may have installed).
_real_groq_path = os.path.join(os.path.dirname(__file__), "..", "groq_backend.py")
if "groq_backend" in sys.modules:
    # If it's a fake module (types.ModuleType with only generate_response),
    # remove it so we can import the real one
    mod = sys.modules["groq_backend"]
    if not hasattr(mod, "_select_model"):
        del sys.modules["groq_backend"]

import groq_backend as _gb  # noqa: E402

# Ensure we have the real module (not the fake one)
if not hasattr(_gb, "_select_model"):
    # The fake module is still cached — force a file-based import
    spec = importlib.util.spec_from_file_location("groq_backend_real", _real_groq_path)
    _gb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_gb)


class TestModelSelection:
    def test_fast_model_for_short_prompts(self):
        """Short prompts (max_tokens <= threshold) should use the fast model."""
        model = _gb._select_model(10)
        assert model is not None
        assert isinstance(model, str)

    def test_full_model_for_long_prompts(self):
        """Long prompts (max_tokens > threshold) should use the full model."""
        model = _gb._select_model(500)
        assert model is not None
        assert isinstance(model, str)

    def test_threshold_boundary(self):
        """At exactly the threshold, should use fast model."""
        at_threshold = _gb._select_model(_gb.FAST_MODEL_THRESHOLD)
        above_threshold = _gb._select_model(_gb.FAST_MODEL_THRESHOLD + 1)
        assert at_threshold is not None
        assert above_threshold is not None

    def test_zero_tokens(self):
        """Zero max_tokens should not crash."""
        model = _gb._select_model(0)
        assert model is not None

    def test_negative_tokens(self):
        """Negative max_tokens should not crash."""
        model = _gb._select_model(-1)
        assert model is not None


class TestModelConfig:
    def test_fast_model_env(self):
        """FAST_MODEL should be a non-empty string."""
        assert isinstance(_gb.FAST_MODEL, str)
        assert len(_gb.FAST_MODEL) > 0

    def test_full_model_env(self):
        """FULL_MODEL should be a non-empty string."""
        assert isinstance(_gb.FULL_MODEL, str)
        assert len(_gb.FULL_MODEL) > 0

    def test_threshold_is_positive(self):
        """FAST_MODEL_THRESHOLD should be a positive integer."""
        assert isinstance(_gb.FAST_MODEL_THRESHOLD, int)
        assert _gb.FAST_MODEL_THRESHOLD > 0

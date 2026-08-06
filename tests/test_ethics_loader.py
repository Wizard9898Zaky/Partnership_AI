"""Tests for ethics_loader.py — correct loading, key validation, and fallback behaviour."""
import json
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_ethics(tmp_path: Path, data: dict) -> Path:
    f = tmp_path / "ethics.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_get_principles_returns_list_from_valid_file(tmp_path, monkeypatch):
    """get_principles() returns the core_principles list from a valid ethics.json."""
    import ethics_loader as el
    ethics_file = _write_ethics(tmp_path, {
        "core_principles": ["Do no harm", "Be honest", "Respect autonomy"],
        "disallowed_patterns": [],
    })
    monkeypatch.setattr(el, "ETHICS_PATH", ethics_file)
    principles = el.get_principles()
    assert "Do no harm" in principles
    assert isinstance(principles, list)
    assert len(principles) == 3


def test_get_principles_raises_on_missing_key(tmp_path, monkeypatch):
    """get_principles() raises RuntimeError when 'core_principles' key is absent."""
    import ethics_loader as el
    ethics_file = _write_ethics(tmp_path, {"other_key": "unexpected"})
    monkeypatch.setattr(el, "ETHICS_PATH", ethics_file)
    with pytest.raises(RuntimeError, match="core_principles"):
        el.get_principles()


def test_load_core_values_raises_on_missing_file(tmp_path, monkeypatch):
    """load_core_values() raises FileNotFoundError when ethics.json does not exist."""
    import ethics_loader as el
    missing = tmp_path / "nonexistent.json"
    monkeypatch.setattr(el, "ETHICS_PATH", missing)
    # Also patch CWD fallback so it doesn't accidentally find the real file
    monkeypatch.chdir(tmp_path)
    with pytest.raises((FileNotFoundError, RuntimeError)):
        el.load_core_values()


def test_load_core_values_raises_on_corrupt_json(tmp_path, monkeypatch):
    """load_core_values() raises RuntimeError on malformed JSON."""
    import ethics_loader as el
    bad = tmp_path / "ethics.json"
    bad.write_text("{not valid json::}", encoding="utf-8")
    monkeypatch.setattr(el, "ETHICS_PATH", bad)
    with pytest.raises((json.JSONDecodeError, RuntimeError)):
        el.load_core_values()


def test_get_principles_allows_empty_list(tmp_path, monkeypatch):
    """An empty core_principles list is technically valid and should be returned as-is."""
    import ethics_loader as el
    ethics_file = _write_ethics(tmp_path, {"core_principles": []})
    monkeypatch.setattr(el, "ETHICS_PATH", ethics_file)
    result = el.get_principles()
    assert result == []


def test_load_core_values_returns_full_dict(tmp_path, monkeypatch):
    """load_core_values() returns the complete dict, not just the principles list."""
    import ethics_loader as el
    data = {
        "core_principles": ["Transparency"],
        "version": "1.0",
        "meta": {"author": "Zak"},
    }
    ethics_file = _write_ethics(tmp_path, data)
    monkeypatch.setattr(el, "ETHICS_PATH", ethics_file)
    result = el.load_core_values()
    assert result["version"] == "1.0"
    assert result["meta"]["author"] == "Zak"

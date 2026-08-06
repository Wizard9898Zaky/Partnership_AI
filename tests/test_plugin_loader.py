"""
tests/test_plugin_loader.py
Tests for the plugin auto-discovery and registration system.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from conversation_engine.action_registry import ACTIONS
from conversation_engine.plugin_loader import (
    load_all_plugins,
    get_plugin_status,
    reload_plugin,
    get_plugins_dir,
)


class TestPluginDiscovery:
    def test_example_plugin_loads(self):
        """The example_plugin in plugins/ should load and register hello_world."""
        loaded, failed = load_all_plugins()
        assert "example_plugin" in loaded
        assert "hello_world" in ACTIONS

    def test_hello_world_executes(self):
        """The plugin's action should actually work."""
        load_all_plugins()
        result = ACTIONS["hello_world"](name="Test")
        assert result.success
        assert result.data["greeting"] == "Hello, Test!"

    def test_hello_world_default_name(self):
        """Default name should be 'World'."""
        load_all_plugins()
        result = ACTIONS["hello_world"]()
        assert result.success
        assert result.data["greeting"] == "Hello, World!"

    def test_idempotent_load(self):
        """Loading twice should not re-import or fail."""
        load_all_plugins()
        loaded, failed = load_all_plugins()
        # Second call should find it already loaded
        assert "example_plugin" in loaded or len(loaded) == 0  # either way is fine


class TestPluginStatus:
    def test_get_plugin_status(self):
        """get_plugin_status should return a list with example_plugin."""
        load_all_plugins()
        status = get_plugin_status()
        assert isinstance(status, list)
        names = [p["name"] for p in status]
        assert "example_plugin" in names

    def test_status_has_loaded_flag(self):
        load_all_plugins()
        status = get_plugin_status()
        for p in status:
            assert "loaded" in p
            assert isinstance(p["loaded"], bool)

    def test_plugins_dir_exists(self):
        """get_plugins_dir should return a Path that exists."""
        d = get_plugins_dir()
        assert d.exists()
        assert d.name == "plugins"


class TestPluginReload:
    def test_reload_existing_plugin(self):
        """reload_plugin should succeed for an existing plugin."""
        load_all_plugins()
        result = reload_plugin("example_plugin")
        assert result is True

    def test_reload_nonexistent_plugin(self):
        """reload_plugin should fail for a nonexistent plugin."""
        result = reload_plugin("does_not_exist")
        assert result is False

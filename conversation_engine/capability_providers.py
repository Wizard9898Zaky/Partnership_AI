#!/usr/bin/env python3
# conversation_engine/capability_providers.py

from pathlib import Path
from typing import Dict, Callable, Any
import os

CapabilityDict = Dict[str, Dict[str, bool]]

def filesystem_capabilities(root: Path) -> CapabilityDict:
    """Return capability descriptors for all file-system actions."""
    return {
        "system": {  # FIXED: Changed from "file_system" to "system"
            "introspect": True,
            "can_describe": True,
            "can_draft": True,
            "can_execute": False,
            "can_persist": root.is_dir() and os.access(root, os.W_OK),
        }
    }

def memory_capabilities(memory_engine: Any) -> CapabilityDict:
    """Return capability descriptors for memory read/write actions."""
    return {
        "memory": {
            "can_describe": True,
            "can_draft": False,
            "can_execute": False,
            "can_persist": memory_engine is not None,
        }
    }

def change_request_capabilities(intent_engine: Any) -> CapabilityDict:
    """Return capability descriptors for change-request submission."""
    return {
        "change_requests": {
            "can_describe": True,
            "can_draft": hasattr(intent_engine, "draft_change_request"),
            "can_execute": hasattr(intent_engine, "execute_change_request"),
            "can_persist": False,
        }
    }

def collect_capabilities(providers: list[Callable[[], CapabilityDict]]) -> CapabilityDict:
    """Collect and return all capability descriptors from all providers."""
    capabilities: CapabilityDict = {}
    for provider in providers:
        capabilities.update(provider())
    return capabilities

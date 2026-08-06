#!/usr/bin/env python3
# conversation_engine/__init__.py
"""
Conversation Engine Package
───────────────────────────
Lazy-loaded exports to avoid circular imports.
"""

__all__ = [
    "MemoryEngine",
    "DialogueEngine",
    "Summarizer",
    "EthicsReflector",
    "SelfModel",
]

def __getattr__(name):
    if name == "MemoryEngine":
        from .memory_engine import MemoryEngine
        return MemoryEngine
    elif name == "DialogueEngine":
        from .dialogue_engine import DialogueEngine
        return DialogueEngine
    elif name == "Summarizer":
        from .summarizer import Summarizer
        return Summarizer
    elif name == "EthicsReflector":
        from .ethics_reflector import EthicsReflector
        return EthicsReflector
    elif name == "SelfModel":
        from .self_model import SelfModel
        return SelfModel
    raise AttributeError(f"Module {__name__} has no attribute {name}")

"""
conversation_engine/tools/memory_tools.py
Encrypted memory actions: store, recall, update, delete facts + vocabulary metrics.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import secrets

from conversation_engine.action_registry import (
    ActionResult, register_action,
    EncryptedMemoryBackend,
)
from conversation_engine import action_registry as _ar
from conversation_engine.identity_utils import add_persistent_fact as _add_persistent_fact
from utils import local_now as dt_now

@register_action(
    "store_fact",
    input_schema={
        "type": "object",
        "required": ["user_id", "fact"],
        "properties": {
            "user_id": {"type": "string"},
            "fact": {"type": "string"},
            "category": {"type": "string"},
        },
    },
)
def store_fact_action(
    user_id: str,
    fact: str,
    category: str = "general",
) -> ActionResult:
    """Store a fact using encrypted memory backend."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        memory["facts"].append({
            "id": secrets.token_hex(8),
            "fact": fact,
            "category": category,
            "timestamp": dt_now(),
        })
        EncryptedMemoryBackend.save(user_id, memory)
        return ActionResult(
            success=True,
            data={
                "stored": fact,
                "category": category,
            },
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "recall_memory",
    input_schema={
        "type": "object",
        "required": ["user_id", "topic"],
        "properties": {
            "user_id": {"type": "string"},
            "topic": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
)
def recall_memory_action(
    user_id: str,
    topic: str,
    limit: int = 5,
) -> ActionResult:
    """Recall facts matching a topic using encrypted memory backend."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        matches = [
            f for f in memory.get("facts", [])
            if topic.lower() in f["fact"].lower()
        ]
        return ActionResult(
            success=True,
            data={
                "matches": matches[:limit],
                "count": len(matches),
            },
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "update_memory",
    input_schema={
        "type": "object",
        "required": ["user_id", "fact_key", "new_value"],
        "properties": {
            "user_id": {"type": "string"},
            "fact_key": {"type": "string"},
            "new_value": {"type": "string"},
        },
    },
)
def update_memory_action(
    user_id: str,
    fact_key: str,
    new_value: str,
) -> ActionResult:
    """Action: update a fact in the agent's encrypted memory blob."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        updated = False
        for fact in memory["facts"]:
            if fact["id"] == fact_key or fact["fact"] == fact_key:
                fact["fact"] = new_value
                updated = True
        if not updated:
            return ActionResult(False, error="Fact not found")
        EncryptedMemoryBackend.save(user_id, memory)
        return ActionResult(
            success=True,
            data={
                "updated": fact_key,
                "new_value": new_value,
            },
        )
    except Exception as e:
        return ActionResult(False, error=str(e))

@register_action(
    "delete_memory",
    input_schema={
        "type": "object",
        "required": ["user_id", "fact_key", "confirm"],
        "properties": {
            "user_id": {"type": "string"},
            "fact_key": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
    },
)
def delete_memory_action(
    user_id: str,
    fact_key: str,
    confirm: bool,
) -> ActionResult:
    """Action: delete a fact from the agent's encrypted memory blob."""
    if not confirm:
        return ActionResult(False, error="Deletion requires confirm=True")
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        before = len(memory["facts"])
        memory["facts"] = [
            f for f in memory["facts"]
            if f["id"] != fact_key and f["fact"] != fact_key
        ]
        after = len(memory["facts"])
        if before == after:
            return ActionResult(False, error="Fact not found")
        EncryptedMemoryBackend.save(user_id, memory)
        return ActionResult(
            success=True,
            data={
                "deleted": fact_key
            },
        )
    except Exception as e:
        return ActionResult(False, error=str(e))

@register_action(
    "store_vocabulary_metric",
    input_schema={
        "type": "object",
        "required": ["user_id", "metric_name", "keywords"],
        "properties": {
            "user_id": {"type": "string"},
            "metric_name": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}}
        },
    },
)
def store_vocabulary_metric_action(
    user_id: str,
    metric_name: str,
    keywords: List[str]
) -> ActionResult:
    """Store a vocabulary metric (name → keyword list) in the user's encrypted memory."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        memory.setdefault("vocabulary_metrics", {})[metric_name] = keywords
        EncryptedMemoryBackend.save(user_id, memory)
        return ActionResult(
            success=True,
            data={"metric_name": metric_name, "keywords": keywords}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "recall_vocabulary_metric",
    input_schema={
        "type": "object",
        "required": ["user_id", "metric_name"],
        "properties": {
            "user_id": {"type": "string"},
            "metric_name": {"type": "string"}
        },
    },
)
def recall_vocabulary_metric_action(
    user_id: str,
    metric_name: str
) -> ActionResult:
    """Recall a specific vocabulary metric for the user."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        keywords = memory.get("vocabulary_metrics", {}).get(metric_name)
        if keywords is None:
            return ActionResult(success=False, error=f"Metric '{metric_name}' not found")
        return ActionResult(
            success=True,
            data={"metric_name": metric_name, "keywords": keywords}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "recall_all_vocabulary_metrics",
    input_schema={
        "type": "object",
        "required": ["user_id"],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
def recall_all_vocabulary_metrics_action(user_id: str) -> ActionResult:
    """Recall all vocabulary metrics for the user."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        metrics = memory.get("vocabulary_metrics", {})
        return ActionResult(
            success=True,
            data={"metrics": metrics, "count": len(metrics)}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "delete_vocabulary_metric",
    input_schema={
        "type": "object",
        "required": ["user_id", "metric_name"],
        "properties": {
            "user_id": {"type": "string"},
            "metric_name": {"type": "string"}
        },
    },
)
def delete_vocabulary_metric_action(
    user_id: str,
    metric_name: str
) -> ActionResult:
    """Delete a vocabulary metric for the user."""
    try:
        memory = EncryptedMemoryBackend.load(user_id)
        vocab = memory.get("vocabulary_metrics", {})
        if metric_name not in vocab:
            return ActionResult(success=False, error=f"Metric '{metric_name}' not found")
        del vocab[metric_name]
        memory["vocabulary_metrics"] = vocab
        EncryptedMemoryBackend.save(user_id, memory)
        return ActionResult(
            success=True,
            data={"metric_name": metric_name, "deleted": True}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

# ═══════════════════════════════════════
# PREFERENCE & IDENTITY ACTIONS
# ═══════════════════════════════════════

@register_action(
    "store_preference",
    input_schema={
        "type": "object",
        "required": ["user_id", "trigger_context", "action_rule"],
        "properties": {
            "user_id": {"type": "string"},
            "trigger_context": {"type": "string"},
            "action_rule": {"type": "string"},
            "category": {"type": "string"}
        },
    },
)
def store_preference_action(
    user_id: str,
    trigger_context: str,
    action_rule: str,
    category: str = "general"
) -> ActionResult:
    """Store a preference rule: 'When [trigger], do [action]'."""
    try:
        engine = _ar._LIVE_MEMORY_ENGINE
        if not engine:
            return ActionResult(success=False, error="MemoryEngine not available.")
        success = engine.store_preference_rule(user_id, trigger_context, action_rule, category)
        if success:
            return ActionResult(success=True, data={"stored": True})
        return ActionResult(success=False, error="Failed to store preference.")
    except Exception as e:
        return ActionResult(success=False, error=str(e))


@register_action(
    "list_preferences",
    input_schema={
        "type": "object",
        "required": ["user_id"],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
def list_preferences_action(user_id: str) -> ActionResult:
    """List all stored preference rules for the user."""
    try:
        engine = _ar._LIVE_MEMORY_ENGINE
        if not engine:
            return ActionResult(success=False, error="MemoryEngine not available.")
        prefs = engine.list_preferences(user_id)
        return ActionResult(success=True, data={"preferences": prefs, "count": len(prefs)})
    except Exception as e:
        return ActionResult(success=False, error=str(e))


@register_action(
    "clear_user_data",
    input_schema={
        "type": "object",
        "required": ["user_id", "confirm"],
        "properties": {
            "user_id": {"type": "string"},
            "confirm": {"type": "boolean"}
        },
    },
)
def clear_user_data_action(user_id: str, confirm: bool = False) -> ActionResult:
    """Delete all in-memory and persisted data for a user. Requires confirm=true."""
    try:
        if not confirm:
            return ActionResult(success=False, error="Confirmation required: set confirm=true to proceed.")
        engine = _ar._LIVE_MEMORY_ENGINE
        if not engine:
            return ActionResult(success=False, error="MemoryEngine not available.")
        engine.clear_user(user_id)
        return ActionResult(success=True, data={"cleared": True, "user_id": user_id})
    except Exception as e:
        return ActionResult(success=False, error=str(e))


@register_action(
    "add_persistent_fact",
    input_schema={
        "type": "object",
        "required": ["user_id", "fact"],
        "properties": {
            "user_id": {"type": "string"},
            "fact": {"type": "string"},
            "category": {"type": "string"}
        },
    },
)
def add_persistent_fact_action(
    user_id: str,
    fact: str,
    category: str = "general"
) -> ActionResult:
    """Add a persistent fact to the user's learned identity data."""
    try:
        # Load current learned data
        memory = EncryptedMemoryBackend.load(user_id)
        learned_str = memory.get("learned_data", "{}")
        if not isinstance(learned_str, str):
            learned_str = "{}"
        # Use identity_utils to add the fact
        updated = _add_persistent_fact(learned_str, fact, category)
        memory["learned_data"] = updated
        EncryptedMemoryBackend.save(user_id, memory)
        return ActionResult(success=True, data={"fact_added": fact[:100], "category": category})
    except Exception as e:
        return ActionResult(success=False, error=str(e))

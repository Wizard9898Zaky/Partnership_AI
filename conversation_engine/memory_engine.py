import logging
#!/usr/bin/env python3
# conversation_engine/memory_engine.py
"""
Manages memory storage + retrieval with
auto-migration support.

Canonical format:
{
    "user_id": {
        "exchanges": [...],
        "plan": {...},
        "identity": {...}
    }
}
"""
import json
import os
import time
from typing import List, Dict, Optional, Any
from pathlib import Path
from datetime import datetime, timezone
from conversation_engine.ethics_reflector import EthicsReflector

logger = logging.getLogger(__name__)

class MemoryEngine:
    """Manages conversation history, structured fact storage, and session context.

    Provides short-term context windowing (last N exchanges), long-term fact
    persistence via EncryptedMemoryBackend, and optional LLM-powered summarisation
    when the context window would be exceeded.

    The primary persistence path is the encrypted log written by new_main_chat.py;
    the in-process _save() methods are crash-recovery fallbacks only.
    """
    def __init__(
        self,
        path: str,
        max_context: int = 8,
        summarizer=None,
        encryption_key: Optional[bytes] = None
    ):
        self.path = Path(path)
        self.max_context = max_context
        self.summarizer = summarizer
        self.encryption_key = encryption_key
        # 1. Initialize data structures FIRST.
        self.store: Dict[str, List[dict]] = {}
        self.data: Dict[str, Any] = {}
        # 2. Load the raw store (conversation
        #    history).
        self.store = self._load_memory()
        # 3. Load structured data (plans,
        #    identity).
        self._load()
        # 4. Ethics Reflector — lazily initialized on first use to avoid
        #    duplicate "Loaded 27 principles" log spam at startup.
        self._ethics = None
        # 5. Migrate old formats if necessary (NOW
        #    that data exists).
        self._auto_migrate_all_users()
        # 6. Save any migrations immediately.
        self._save_memory()
        self._save()

    @property
    def ethics(self):
        """Lazily create EthicsReflector only when first needed."""
        if self._ethics is None:
            self._ethics = EthicsReflector()
        # If ethics is disabled via config, return a stub that approves everything
        if not self._ethics.enabled:
            class _NoOpEthics:
                def check_text_against_core_principles(self, text):
                    return True
                def review(self, *a, **kw):
                    return a[0] if a else "", []
                def review_deep(self, *a, **kw):
                    return a[-1] if a else "", []
                enabled = False
            self._ethics = _NoOpEthics()
        return self._ethics

    def _load(self):
        """Load structured data (plans, identity) from disk."""
        if self.path.exists():
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # If file is empty or invalid,
                    # start fresh.
                    if not content.strip():
                        self.data = {}
                    else:
                        self.data = json.loads(content)
            except Exception:
                logger.warning("MemoryEngine: failed to load persisted data from '%s'; starting fresh.", self.path, exc_info=True)
                self.data = {}
        else:
            self.data = {}

    def _save(self):
        """
        DISABLED: Primary persistence is via encrypted logs in new_main_chat.py.
        FIX v1.1: Added emergency temp-save as crash recovery fallback.
        If the main chat loop crashes before its cleanup runs, the temp file
        preserves the last known in-memory state for manual recovery.
        """
        try:
            tmp_path = self.path.parent / f".crash_recovery_{self.path.stem}.json"
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass  # Best-effort only; never crash because of the safety net itself

    def _save_memory(self):
        """
        DISABLED: Primary persistence is via encrypted logs in new_main_chat.py.
        FIX v1.1: Added emergency temp-save as crash recovery fallback.
        Writes unencrypted plaintext to a temp file — for crash recovery only.
        This file is NOT a substitute for the encrypted log; delete it after recovery.
        """
        try:
            tmp_path = self.path.parent / f".crash_recovery_{self.path.stem}_store.json"
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(self.store, indent=2, ensure_ascii=False)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            pass  # Best-effort only; never crash because of the safety net itself

    def get_active_plan(self, user_id: str) -> Optional[Dict]:
        """
        Retrieves the current active plan for the
        user.
        """
        # Ensure user data exists and is a dict.
        if user_id not in self.data:
            self.data[user_id] = {"exchanges": [], "plan": None}
        # Ensure the entry is a dict.
        user_entry = self.data[user_id]
        if not isinstance(user_entry, dict):
            # Migration: Convert old list format
            # to dict.
            user_entry = {"exchanges": user_entry if isinstance(user_entry, list) else [], "plan": None}
            self.data[user_id] = user_entry
            self._save()
        return user_entry.get("plan")

    def update_active_plan(self, user_id: str, plan: Dict):
        """Updates the active plan."""
        if user_id not in self.data:
            self.data[user_id] = {"exchanges": [], "plan": None}
        user_entry = self.data[user_id]
        if not isinstance(user_entry, dict):
            user_entry = {"exchanges": [], "plan": None}
            self.data[user_id] = user_entry
        user_entry["plan"] = plan
        self._save()

    def get_recent_context(self, user_id: str, limit: int = 10) -> List[Dict]:
        """
        Gets the last N exchanges for context.
        """
        if user_id in self.data and isinstance(self.data[user_id], dict):
            exchanges = self.data[user_id].get("exchanges", [])
            return exchanges[-limit:] if exchanges else []
        return []

    # ──────────────────
    # ENCRYPTION HELPERS
    # ──────────────────
    def _decrypt_data(self, data: bytes) -> bytes:
        """
        Decrypt data if encryption key is
        provided.
        """
        if self.encryption_key is None:
            return data
        from cryptography.fernet import Fernet
        f = Fernet(self.encryption_key)
        return f.decrypt(data)

    # ──────────────────────────
    # LOADING & SAVING (History)
    # ──────────────────────────
    def _load_memory(self) -> Dict[str, List[dict]]:
        """Load conversation history (store)."""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
            # Decrypt if needed
            decrypted = self._decrypt_data(raw)
            data = json.loads(decrypted.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ────────────────
    # MIGRATION SYSTEM
    # ────────────────
    def _auto_migrate_record(self, record: dict) -> dict:
        migrated = {
            "user": "",
            "ai": "",
            "timestamp": record.get("timestamp", time.time())
        }
        # Legacy → new keys
        map_user = ["user", "user_input", "input", "message_user"]
        map_ai = ["ai", "ai_response", "response", "message_ai"]
        for k in map_user:
            if k in record:
                migrated["user"] = record[k]
                break
        for k in map_ai:
            if k in record:
                migrated["ai"] = record[k]
                break
        return migrated

    def _auto_migrate_all_users(self):
        """
        Migrate old list-based user data to new
        dict-based structure.
        """
        # Migrate self.store (conversation
        # history).
        for uid, history in list(self.store.items()):
            # Ensure history is a list.
            if not isinstance(history, list):
                history = []
                self.store[uid] = []
            new_entries = []
            for entry in history:
                if isinstance(entry, dict):
                    new_entries.append(self._auto_migrate_record(entry))
                else:
                    # Skip invalid entries.
                    continue
            self.store[uid] = new_entries

        # Also migrate self.data if it has old
        # list formats.
        for uid, entry in list(self.data.items()):
            if isinstance(entry, list):
                # Convert old list format to dict.
                self.data[uid] = {
                    "exchanges": entry,
                    "plan": None,
                    "identity": {}
                }

    # ────────────────
    # PUBLIC INTERFACE
    # ────────────────
    def record_interaction(self, user_id: str, user_input: str, ai_response: str,
                            system_note: bool = False):
        """
        Record a full user/AI exchange in the session store and persist.

        system_note: mark this entry as non-dialogue bookkeeping (e.g.
            system identity/self-model bootstrap data) rather than a
            real conversation turn, so recall_context() can exclude it
            from what's presented to the LLM as prior dialogue.
        """
        entry = {
            "user": user_input,
            "ai": ai_response,
            "timestamp": time.time(),
            "system_note": system_note,
        }
        if user_id not in self.store:
            self.store[user_id] = []
        self.store[user_id].append(entry)
        # Limit memory depth.
        if len(self.store[user_id]) > self.max_context:
            self.store[user_id] = self.store[user_id][-self.max_context:]
        self._save_memory()

    # BACKWARD COMPAT: Allows old code to still
    # call store_exchange().
    #
    # NOTE: this used to be defined *twice* in this class - an earlier
    # definition (store_exchange(user_id, role, content)) was silently
    # shadowed by this one (Python just keeps the last definition of a
    # duplicate method name, no error). The one call site in
    # new_main_chat.py was written against the *first* signature's
    # intent (tag a system entry with a role), but only ever actually
    # ran through this second one - meaning bootstrap/identity data got
    # recorded as if it were a literal user message reading
    # "[SYSTEM_IDENTITY_MODEL]", and would be replayed as fake dialogue
    # by recall_context(). Use store_system_note() for that case instead.
    def store_exchange(self, user_id: str, user_input: str, ai_response: str):
        """Append a role/content pair to the user's conversation store."""
        self.record_interaction(user_id, user_input, ai_response)

    def store_system_note(self, user_id: str, tag: str, content: str):
        """
        Record non-dialogue bookkeeping data (e.g. self-model/identity
        bootstrap content) without it being replayed later as if it
        were a real user message. Use this instead of store_exchange()
        for anything that isn't an actual conversation turn.
        """
        self.record_interaction(user_id, f"[{tag}]", content, system_note=True)

    def recall_context(self, user_id: str) -> str:
        """Return recent conversation history for a user as a formatted string."""
        if user_id not in self.store:
            return ""
        lines = []
        history = self.store[user_id][-self.max_context:]
        for entry in history:
            if entry.get("system_note"):
                continue  # bookkeeping, not real dialogue - don't replay it as a user turn
            migrated = self._auto_migrate_record(entry)
            lines.append(
                f"User: {migrated['user']}\nAI: {migrated['ai']}"
            )
        return "\n".join(lines)

    def clear_user(self, user_id: str):
        """Delete all in-memory and persisted data for a given user_id."""
        if user_id in self.store:
            del self.store[user_id]
            self._save_memory()
        if user_id in self.data:
            del self.data[user_id]
            self._save()

    # ─────────────────────────
    # VOCABULARY METRIC STORAGE
    # (FOR IDEA INCUBATOR)
    # ─────────────────────────
    def store_vocabulary_metric(self, user_id: str, metric_name: str, keywords: List[str] | str) -> bool:
        """
        Stores a vocabulary metric for the user.

        Example:
            store_vocabulary_metric("user123", "healing", ["resonance", "balance", "harmony"])
            store_vocabulary_metric("user123", "profit", "revenue, scale, market, growth")

        Returns:
            True if successful.
        """
        try:
            # Ensure user data exists.
            if user_id not in self.data:
                self.data[user_id] = {"exchanges": [], "plan": None, "identity": {}, "vocabulary": {}}
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                user_entry = {"exchanges": [], "plan": None, "identity": {}, "vocabulary": {}}
                self.data[user_id] = user_entry
            # Ensure vocabulary dict exists.
            if "vocabulary" not in user_entry:
                user_entry["vocabulary"] = {}
            # Store the metric.
            user_entry["vocabulary"][metric_name] = keywords
            # Note: _save() is currently disabled,
            # data persists via encrypted logs.
            # When you enable _save(), this will
            # persist to disk.
            return True
        except Exception as e:
            print(f"[MemoryEngine] Error storing vocabulary metric: {e}")
            return False

    def recall_vocabulary_metric(self, user_id: str, metric_name: str) -> List[str]:
        """
        Recalls a specific vocabulary metric for
        the user.

        Returns:
            List of keywords, or empty list if not
            found.
        """
        try:
            if user_id not in self.data:
                return []
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return []
            if "vocabulary" not in user_entry:
                return []
            keywords = user_entry["vocabulary"].get(metric_name, [])
            # Normalize to list.
            if isinstance(keywords, str):
                # Split by comma or space.
                import re
                keywords = [k.strip() for k in re.split(r'[,\s]+', keywords) if k.strip()]
            elif isinstance(keywords, list):
                keywords = [str(k).strip() for k in keywords if str(k).strip()]
            else:
                keywords = []
            return keywords
        except Exception as e:
            print(f"[MemoryEngine] Error recalling vocabulary metric: {e}")
            return []

    def recall_all_vocabulary_metrics(self, user_id: str) -> Dict[str, List[str]]:
        """
        Recalls ALL vocabulary metrics for the
        user.

        Returns:
            Dictionary of {metric_name: [keywords]}
        """
        try:
            if user_id not in self.data:
                return {}
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return {}
            if "vocabulary" not in user_entry:
                return {}
            return user_entry["vocabulary"]
        except Exception as e:
            print(f"[MemoryEngine] Error recalling all vocabulary metrics: {e}")
            return {}

    def delete_vocabulary_metric(self, user_id: str, metric_name: str) -> bool:
        """
        Deletes a vocabulary metric for the user.

        Returns:
            True if deleted, False if not found.
        """
        try:
            if user_id not in self.data:
                return False
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return False
            if "vocabulary" not in user_entry:
                return False
            if metric_name in user_entry["vocabulary"]:
                del user_entry["vocabulary"][metric_name]
                return True
            return False
        except Exception as e:
            print(f"[MemoryEngine] Error deleting vocabulary metric: {e}")
            return False

    # ──────────────────────────
    # SHORT-TERM INSIGHT STORAGE
    # ──────────────────────────
    def store_insight(self, user_id: str, insight_type: str, content: str, source_utterance: str):
        """
        Stores a short-term insight directly
        inside the user's memory context. Insight
        is only stored if it passes the ethics
        check.
        """
        is_clean = self.ethics.check_text_against_core_principles(content)
        insight_record = {
            "insight_type": insight_type,
            "content": content,
            "source": source_utterance,
            "timestamp": time.time(),
            "ethics": "passed" if is_clean else "rejected"
        }
        if user_id not in self.store:
            self.store[user_id] = []
        if is_clean:
            self.store[user_id].append({
                "user": f"[INSIGHT] {content}",
                "ai": "",
                "timestamp": insight_record["timestamp"],
                "meta": insight_record
            })
            # Keep memory trimmed.
            if len(self.store[user_id]) > self.max_context:
                self.store[user_id] = self.store[user_id][-self.max_context:]
            self._save_memory()
        else:
            print("[ETHICS] Rejected insight:", insight_record)
        return is_clean

    # ────────────────────────────────────────────
    # PREFERENCE LEARNING (ASK → LEARN → AUTOMATE)
    # ────────────────────────────────────────────
    def store_preference_rule(self, user_id: str, trigger_context: str, action_rule: str, category: str = "general") -> bool:
        """
        Stores a preference rule: "When [trigger],
        do [action]".

        Example:
            trigger_context: "analyzing_idea_without_metrics"
            action_rule: "ask_user_for_new_metric_definition"
            category: "analysis_behavior"

        Returns:
            True if successful.
        """
        try:
            if user_id not in self.data:
                self.data[user_id] = {"exchanges": [], "plan": None, "identity": {}, "preferences": {}}
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                user_entry = {"exchanges": [], "plan": None, "identity": {}, "preferences": {}}
                self.data[user_id] = user_entry
            if "preferences" not in user_entry:
                user_entry["preferences"] = {}
            # Store the rule.
            # Key: trigger_context,
            # Value: {
            #   "rule": action_rule,
            #   "category": category
            # }
            user_entry["preferences"][trigger_context] = {
                "rule": action_rule,
                "category": category,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            return True
        except Exception as e:
            print(f"[MemoryEngine] Error storing preference rule: {e}")
            return False

    def recall_preference_rule(self, user_id: str, trigger_context: str) -> Optional[Dict[str, Any]]:
        """
        Recalls a preference rule for a specific
        trigger context.

        Returns:
            Dict with 'rule' and 'category', or
            None if not found.
        """
        try:
            if user_id not in self.data:
                return None
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return None
            if "preferences" not in user_entry:
                return None
            return user_entry["preferences"].get(trigger_context)
        except Exception as e:
            print(f"[MemoryEngine] Error recalling preference rule: {e}")
            return None

    def list_preferences(self, user_id: str) -> Dict[str, Any]:
        """
        Lists all stored preferences for the user.
        """
        try:
            if user_id not in self.data:
                return {}
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return {}
            return user_entry.get("preferences", {})
        except Exception:
            return {}

    # ─────────────────────
    # GENERIC MEMORY ACCESS
    # ─────────────────────
    def get_memory(self, user_id: str, category: str, default: Any = None) -> Any:
        """
        Generic memory retrieval for any category.

        Categories supported:
        - "exchanges" - conversation history
        - "plan" - active execution plan
        - "identity" - learned identity/self-model
        - "vocabulary" - all vocabulary metrics
        - "preferences" - all preference rules
        - "insights" - stored insights (from
                       store_insight)
        - Any custom category added to user_entry

        Args:
            user_id (str): The user identifier.
            category (str): The category to retrieve.
            default (Any): Value to return if category not found.

        Returns:
            The stored data for the category, or
            the default value.
        """
        try:
            # Ensure user data exists.
            if user_id not in self.data:
                self.data[user_id] = {
                    "exchanges": [],
                    "plan": None,
                    "identity": {},
                    "vocabulary": {},
                    "preferences": {}
                }
            user_entry = self.data[user_id]
            # Ensure entry is a dict (migration
            # safety).
            if not isinstance(user_entry, dict):
                user_entry = {
                    "exchanges": user_entry if isinstance(user_entry, list) else [],
                    "plan": None,
                    "identity": {},
                    "vocabulary": {},
                    "preferences": {}
                }
                self.data[user_id] = user_entry
            # Return the requested category.
            if category in user_entry:
                return user_entry[category]
            else:
                # Return default if provided.
                if default is not None:
                    return default
                # Smart defaults based on
                # category type.
                smart_defaults = {
                    "exchanges": [],
                    "plan": None,
                    "identity": {},
                    "vocabulary": {},
                    "preferences": {},
                    "insights": [],
                    "capabilities": {},  # For adaptive agent
                    "execution_reflections": [],  # For adaptive agent
                }
                return smart_defaults.get(category, {})
        except Exception as e:
            print(f"[MemoryEngine] Error retrieving category '{category}' for user '{user_id}': {e}")
            return default if default is not None else {}

    def set_memory(self, user_id: str, category: str, value: Any) -> bool:
        """
        Generic memory storage for any category.

        Note: Since _save() is disabled, data
        persists via encrypted logs in
        new_main_chat.py. When you enable _save(),
        this will persist to disk.

        Args:
            user_id (str): The user identifier.
            category (str): The category to store.
            value (Any): The value to store.

        Returns:
            True if successful.
        """
        try:
            # Ensure user data exists.
            if user_id not in self.data:
                self.data[user_id] = {
                    "exchanges": [],
                    "plan": None,
                    "identity": {},
                    "vocabulary": {},
                    "preferences": {}
                }
            user_entry = self.data[user_id]
            # Ensure entry is a dict (migration
            # safety).
            if not isinstance(user_entry, dict):
                user_entry = {
                    "exchanges": [],
                    "plan": None,
                    "identity": {},
                    "vocabulary": {},
                    "preferences": {}
                }
                self.data[user_id] = user_entry
            # Set the value.
            user_entry[category] = value
            return True
        except Exception as e:
            print(f"[MemoryEngine] Error storing category '{category}' for user '{user_id}': {e}")
            return False

    def delete_memory(self, user_id: str, category: str) -> bool:
        """
        Deletes a specific category from user
        memory.

        Args:
            user_id (str): The user identifier.
            category (str): The category to delete

        Returns:
            True if deleted, False if not found.
        """
        try:
            if user_id not in self.data:
                return False
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return False
            if category in user_entry:
                del user_entry[category]
                return True
            return False
        except Exception as e:
            print(f"[MemoryEngine] Error deleting category '{category}' for user '{user_id}': {e}")
            return False

    def list_memory_categories(self, user_id: str) -> List[str]:
        """
        Lists all available memory categories for
        the user.

        Args:
            user_id (str): The user identifier.

        Returns:
            List of category names.
        """
        try:
            if user_id not in self.data:
                return []
            user_entry = self.data[user_id]
            if not isinstance(user_entry, dict):
                return []
            return list(user_entry.keys())
        except Exception:
            return []
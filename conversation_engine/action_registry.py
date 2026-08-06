"""
conversation_engine/action_registry.py
───────────────────────────────────────
Central action registry for Partnership_AI.

This file owns:
  - The ActionResult dataclass
  - The @register_action decorator + ACTIONS / ACTION_SCHEMAS / ACTION_METADATA dicts
  - execute_action() — the single entry-point to run any registered action
  - validate_schema() — kwarg validation before dispatch
  - PolicyEngine — protected-file guard
  - safe_resolve_path() — whitelist-enforced path resolver
  - EncryptedMemoryBackend — encrypted per-user fact store
  - Shared path constants (STATE_DIR, USER_LOGS_DIR, etc.)

Action *implementations* live in conversation_engine/tools/:
  file_tools.py      — list_files, read_file, search_code, get_file_stats,
                       analyze_code_quality, propose_upgrade, generate_and_write_code
  memory_tools.py    — store_fact, recall_memory, update_memory, delete_memory,
                       store_vocabulary_metric, recall_vocabulary_metric,
                       recall_all_vocabulary_metrics, delete_vocabulary_metric
  state_tools.py     — save_state, load_state, refresh_metadata,
                       get_function_signatures, list_capabilities, get_system_status
  agent_tools.py     — respond_to_user, request_change
  incubator_tools.py — incubator_insert_idea, incubator_get_all_ideas,
                       incubator_search_ideas, incubator_analyze_idea,
                       incubator_generate_ideas, incubator_connect_ideas,
                       incubator_get_statistics, incubator_ask_natural

Adding a new action:
  1. Create (or add to) a file in conversation_engine/tools/.
  2. Decorate your function with @register_action("name", input_schema={...}).
  3. Import the module in conversation_engine/tools/__init__.py.
  That's it — no changes needed here.
"""
from __future__ import annotations
#!/usr/bin/env python3
# conversation_engine/action_registry.py
"""
Secure Action Registry
──────────────────────
Hardened action execution framework for
Partnership_AI.

Implements:
- Unified ActionResult contract
- Centralized safety policy engine
- Mandatory sandbox execution
- Schema validation
- Safe path resolution
- Signed metadata integrity
- Real encrypted memory backend
- Simulation vs execution separation
- Atomic state operations
- Structured outputs only
"""
from typing import Callable, Dict, Any, List, Optional, get_type_hints
from pathlib import Path
from dataclasses import dataclass, asdict
import tempfile
import subprocess
import inspect
import hashlib
import shutil
import secrets
import base64
import json
import re
import os
import sys
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from utils import local_now as dt_now

# ══════════
# BASE PATHS
# ══════════
_BASE_DIR = Path(__file__).resolve().parent.parent
WHITELIST_FILE = _BASE_DIR / "introspection_whitelist.json"
METADATA_FILE = _BASE_DIR / "action_metadata.json"
METADATA_SIGNATURE_FILE = _BASE_DIR / "action_metadata.sig"
STATE_DIR = _BASE_DIR / "state_snapshots"
USER_LOGS_DIR = _BASE_DIR / "user_logs"
STATE_DIR.mkdir(exist_ok=True)
USER_LOGS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Shared live memory engine — set by AdaptiveAgent.__init__() via
# register_memory_engine() so every IdeaIncubator action uses the
# same MemoryEngine instance as the rest of the session.
# ─────────────────────────────────────────────────────────────────────────────
_LIVE_MEMORY_ENGINE = None  # type: ignore

def register_memory_engine(engine) -> None:
    """
    Register the active MemoryEngine instance so action-registry actions
    (especially IdeaIncubator) share the session's live memory.
    Called once by AdaptiveAgent.__init__().
    """
    global _LIVE_MEMORY_ENGINE
    _LIVE_MEMORY_ENGINE = engine
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# ═══════════════════
# SANDBOX REQUIREMENT
# ═══════════════════
SANDBOX_REQUIRED = True
try:
    from sandbox_executor import SandboxedExecutor
    _SANDBOX_AVAILABLE = True
except ImportError as e:
    # FIX v1.1: Graceful degradation instead of hard crash.
    # Log a clear warning and disable sandboxed execution — the action registry
    # will still initialize and all non-execution actions remain available.
    # This keeps the system usable when sandbox_executor.py is missing or
    # has a broken dependency, without silently lowering security for exec actions.
    import logging as _sandbox_log
    _sandbox_log.getLogger(__name__).warning(
        f"SandboxedExecutor unavailable: {e}. "
        "Code execution actions are DISABLED. All read/memory/introspect actions remain active."
    )
    SandboxedExecutor = None  # type: ignore
    _SANDBOX_AVAILABLE = False

# ══════════════════════
# ACTION RESULT CONTRACT
# ══════════════════════
@dataclass
class ActionResult:
    """Standardised return contract for every registered action.

    Attributes:
        success: True if the action completed without error.
        data: Arbitrary structured output on success (dict, list, etc.).
        error: Human-readable error message on failure.
        metadata: Optional supplementary metadata (timing, tokens, etc.).
    """
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this ActionResult to a plain dict."""
        return asdict(self)

# ════════
# REGISTRY
# ════════
ACTIONS: Dict[str, Callable] = {}
ACTION_METADATA: Dict[str, Dict[str, Any]] = {}
ACTION_SCHEMAS: Dict[str, Dict[str, Any]] = {}

# ════════════════════
# SAFETY POLICY ENGINE
# ════════════════════
class PolicyEngine:
    """Lightweight file-protection guard.

    Raises PermissionError when any action attempts to write to or
    modify files that are designated as immutable (ethics, kill switch,
    values kernel). Complements the canonical check in
    values_kernel/invariants.py.
    """
    PROTECTED_FILES = {
        "values_kernel/ethics.json",
        "kill_switch.flag",
        "conversation_engine/action_registry.py",
        "adaptive_agent.py",
    }

    @staticmethod
    def validate_file_access(file_path: str) -> ActionResult:
        """Return True if the file path is on the introspection whitelist."""
        if file_path in PolicyEngine.PROTECTED_FILES:
            return ActionResult(
                success=False,
                error=f"Protected file blocked: {file_path}",
            )
        return ActionResult(success=True)

# ═════════════════
# PATH SAFETY LAYER
# ═════════════════
def load_whitelist() -> List[str]:
    """Load the introspection whitelist JSON; return an empty list on failure."""
    try:
        if not WHITELIST_FILE.exists():
            return []
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("allow_introspection", data.get("files", []))
    except Exception:
        return []

# load_whitelist() is called live in safe_resolve_path — no module-level cache needed.

def safe_resolve_path(file_path: str, require_whitelist: bool = True) -> Path:
    """Resolve path safely within root; raise ValueError on traversal attempt."""
    candidate = (_BASE_DIR / file_path).resolve()
    if not str(candidate).startswith(str(_BASE_DIR.resolve())):
        raise PermissionError("Path traversal attempt detected.")
    if candidate.is_symlink():
        raise PermissionError("Symlink access denied.")
    rel = str(candidate.relative_to(_BASE_DIR)).replace("\\", "/")
    if require_whitelist and rel not in set(load_whitelist()):
        raise PermissionError(f"File not whitelisted: {rel}")
    return candidate

# ══════════════
# MEMORY BACKEND
# ══════════════
class EncryptedMemoryBackend:
    """Fernet-based encrypted key/value store for persistent agent memory.

    Each record is serialised to JSON and encrypted with a caller-supplied
    Fernet key before being written to disk. Reads decrypt transparently.
    Concurrent access is not serialised — callers must coordinate externally
    if multiple processes share the same backing file.
    """
    VERSION = 1
    # Default iterations to match new_main_chat.py
    ITERATIONS = 390000

    @staticmethod
    def _memory_file(user_id: str) -> Path:
        return USER_LOGS_DIR / f"learned-{user_id}.enc"

    @staticmethod
    def _derive_key(passphrase_or_hash: bytes, salt: bytes) -> bytes:
        """
        Derives a Fernet-compatible key (32 bytes) using PBKDF2.
        Matches the logic in new_main_chat.py.
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=EncryptedMemoryBackend.ITERATIONS,
            backend=default_backend(),
        )
        return base64.urlsafe_b64encode(kdf.derive(passphrase_or_hash))

    @classmethod
    def load(cls, user_id: str, encryption_key: Optional[bytes] = None) -> Dict[str, Any]:
        """
        Loads and decrypts memory.
        If encryption_key is provided, it uses it directly.
        Otherwise, it attempts to derive it from the user_id hash (fallback).
        """
        path = cls._memory_file(user_id)
        if not path.exists():
            return {
                "version": cls.VERSION,
                "facts": []
            }
        raw = path.read_bytes()
        # Determine the key to use
        if encryption_key:
            # Use the key passed from the main
            # chat loop (preferred).
            fernet = Fernet(encryption_key)
        else:
            # Fallback: Derive key from user_id
            # hash if no key is passed. This
            # assumes user_id is a consistent hash
            # string.
            salt = user_id.encode()
            key = cls._derive_key(user_id.encode(), salt)
            fernet = Fernet(key)
        try:
            decrypted = fernet.decrypt(raw)
            return json.loads(decrypted.decode("utf-8"))
        except Exception as e:
            # Log error but return empty to
            # prevent crash.
            print(f"[WARN] Failed to decrypt memory for {user_id}: {e}")
            return {
                "version": cls.VERSION,
                "facts": []
            }

    @classmethod
    def save(cls, user_id: str, data: Dict[str, Any], encryption_key: Optional[bytes] = None) -> None:
        """
        Encrypts and saves memory.
        """
        path = cls._memory_file(user_id)
        tmp = path.with_suffix(".tmp")
        data["version"] = cls.VERSION
        payload = json.dumps(data, indent=2).encode("utf-8")
        # Determine the key to use
        if encryption_key:
            fernet = Fernet(encryption_key)
        else:
            salt = user_id.encode()
            key = cls._derive_key(user_id.encode(), salt)
            fernet = Fernet(key)
        encrypted = fernet.encrypt(payload)
        tmp.write_bytes(encrypted)
        os.replace(tmp, path)

# ══════════════════
# METADATA INTEGRITY
# ══════════════════
def metadata_hash(data: Dict[str, Any]) -> str:
    """Return a short SHA-256 fingerprint of action metadata for change detection."""
    serialized = json.dumps(data, sort_keys=True).encode()
    return hashlib.sha256(serialized).hexdigest()

# ═════════════════
# SCHEMA VALIDATION
# ═════════════════
def validate_schema(payload: Dict[str, Any], schema: Dict[str, Any]) -> ActionResult:
    """
    Validate action arguments against the action's parameter schema.

    Checks:
    1. All required fields are present.
    2. No unexpected fields are present (catches LLM hallucinated kwarg names
       before they trigger a confusing TypeError inside the action function,
       which would incorrectly escalate to a capability-gap CR).
    """
    required = schema.get("required", [])
    allowed  = set(schema.get("properties", {}).keys())

    for field in required:
        if field not in payload:
            return ActionResult(
                success=False,
                error=f"Missing required field: '{field}'"
            )

    if allowed:
        unknown = set(payload.keys()) - allowed
        if unknown:
            # Return the known parameter names so the planner can self-correct
            return ActionResult(
                success=False,
                error=(
                    f"Unknown parameter(s): {sorted(unknown)}. "
                    f"Valid parameters for this action: {sorted(allowed)}"
                ),
            )

    return ActionResult(success=True)

# ═══════════════════
# ACTION REGISTRATION
# ═══════════════════
def register_action(name: str, input_schema: Optional[Dict[str, Any]] = None):
    """Decorator factory that registers a function as a named action."""
    def decorator(func):
        """Inner decorator that wraps fn and registers it in ACTIONS and ACTION_METADATA."""
        ACTIONS[name] = func
        ACTION_SCHEMAS[name] = input_schema or {}
        sig = inspect.signature(func)
        type_hints = get_type_hints(func)
        ACTION_METADATA[name] = {
            "function_name": name,
            "description": func.__doc__ or "",
            "parameters": [
                {
                    "name": p,
                    "type": str(type_hints.get(p, "Any")),
                    "required": sig.parameters[p].default == inspect.Parameter.empty
                }
                for p in sig.parameters
            ],
            "registered": dt_now(),
        }
        return func
    return decorator

# ════════════════
# EXECUTION ENGINE
# ════════════════
def execute_action(
    action_name: str,
    payload: Dict[str, Any],
    simulate: bool = False,
) -> ActionResult:
    """Execute a registered action by name, validating args against its schema."""
    if action_name not in ACTIONS:
        return ActionResult(False, error="Unknown action")
    schema_result = validate_schema(
        payload,
        ACTION_SCHEMAS.get(action_name, {})
    )
    if not schema_result.success:
        return schema_result
    if simulate:
        return ActionResult(
            success=True,
            data={
                "mode": "simulation",
                "action": action_name,
                "payload": payload,
            },
        )
    try:
        return ACTIONS[action_name](**payload)
    except Exception as e:
        return ActionResult(
            success=False,
            error=str(e)
        )

# ═══════════════════
# METADATA MANAGEMENT
# ═══════════════════
def save_metadata() -> ActionResult:
    """Persist the current action metadata dict to action_metadata.json."""
    try:
        metadata = {
            "version": "2.0",
            "actions": ACTION_METADATA,
            "updated": dt_now(),
        }
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        digest = metadata_hash(metadata)
        METADATA_SIGNATURE_FILE.write_text(digest)
        return ActionResult(True, data=metadata)
    except Exception as e:
        return ActionResult(False, error=str(e))


def load_and_verify_metadata() -> ActionResult:
    """
    Load action_metadata.json and verify it against action_metadata.sig.

    Previously action_metadata.sig was written by save_metadata() but
    never read back anywhere in the codebase - METADATA_FILE itself was
    only ever opened for writing, never for reading. The signature
    therefore provided the appearance of tamper-evidence without any
    of the substance: nothing would ever notice if action_metadata.json
    was edited by hand, corrupted, or replaced by a rogue self-generated
    patch. This is the actual check - call it at startup.

    Returns:
        ActionResult(True, data=metadata) if the file matches its
        signature (or neither file exists yet, i.e. first run).
        ActionResult(False, error=...) if the files disagree, are
        missing asymmetrically (one present, one not), or fail to parse.
    """
    metadata_exists = METADATA_FILE.exists()
    sig_exists = METADATA_SIGNATURE_FILE.exists()

    if not metadata_exists and not sig_exists:
        return ActionResult(True, data=None)  # first run, nothing to verify yet
    if metadata_exists != sig_exists:
        return ActionResult(
            False,
            error=(
                f"Integrity check failed: {'metadata file' if sig_exists else 'signature file'} "
                f"is missing while the other is present. Possible tampering or corrupted install."
            ),
        )
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    except Exception as e:
        return ActionResult(False, error=f"Could not parse {METADATA_FILE.name}: {e}")

    expected_digest = metadata_hash(metadata)
    actual_digest = METADATA_SIGNATURE_FILE.read_text().strip()
    if expected_digest != actual_digest:
        return ActionResult(
            False,
            error=(
                f"INTEGRITY VIOLATION: {METADATA_FILE.name} does not match "
                f"{METADATA_SIGNATURE_FILE.name}. The file may have been modified "
                f"outside of save_metadata(). Refusing to trust it until this is resolved."
            ),
        )
    return ActionResult(True, data=metadata)

# ═══════
# ACTIONS
# ═══════

# ═══════════════════════════════════════════════════════════════════════════
# TOOL LOADER
# ═══════════════════════════════════════════════════════════════════════════
# Import all tool modules so their @register_action decorators fire and
# populate ACTIONS, ACTION_SCHEMAS, and ACTION_METADATA.
# This import MUST come last (after all shared infrastructure is defined).
# ═══════════════════════════════════════════════════════════════════════════
try:
    import conversation_engine.tools  # noqa: F401  — side-effect import
except Exception as _tool_import_err:
    import logging as _tlog
    _tlog.getLogger(__name__).warning(
        "Tool modules failed to load: %s — some actions will be unavailable.", _tool_import_err
    )

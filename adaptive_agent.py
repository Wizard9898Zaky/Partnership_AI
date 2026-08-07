#!/usr/bin/env python3
# adaptive_agent.py
"""
Partnership_AI — Adaptive Agent Core
═════════════════════════════════════

Responsibilities:
- Goal analysis and planning (action-based flow)
- Plan execution with retry logic and gap detection
- Safe self-modification with rollback and modification cap
- Evolution: Change Request generation when capability is missing
- Ethics validation on all outbound responses
- Encrypted memory read/write (learned identity blob)
- State snapshots (save/load)
- Self-analysis (code quality metrics)
- Capability registry with confidence scoring

Removed in this revision:
- analyze_goal()            — dead code, commented out in run(), superseded by analyze_goal_and_gap()
- discover_missing_abilities() — dead code, commented out in run(), v1 only
- acquire_new_abilities()   — dynamically writes and exec()s LLM-generated code; unsafe, untested, no call site in active path
- _validate_and_install_capability() — helper for acquire_new_abilities(), removed with it
- load_dynamic_tools()      — references self.tool_registry which is never defined; no call site
- converse()                — thin wrapper with no behaviour beyond safe_llm_call; callers use DialogueEngine directly

Fixed in this revision:
- store_memory() calls replaced with set_memory() (actual MemoryEngine API)
- local_now() trailing-comma tuple bug fixed — now returns a plain str
- _log_cr() called .strftime() on the tuple returned by local_now(); fixed
- SelfAnalysisReport default_factory used local_now() (tuple); fixed to datetime.now().isoformat()
- patch_generator import wrapped in its own try/except with a clear None fallback
- tool_registry references removed (was never defined on self)
- _bind_evolution_action() and _bind_secure_actions() both registered request_change; deduplicated
- update_capability_metrics() reformatted (was oddly line-broken across many lines)
- Ethics reflector now receives summarizer so Pass 2 (LLM behavioral check) is available
"""
from __future__ import annotations
import ast
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Cryptography
from cryptography.fernet import Fernet
from values_kernel.invariants import is_kill_switch_active, enforce_kill_switch
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from app_config import get_config
from agent_trace import TurnTrace

# ─────────────
# Configuration
# ─────────────
ROOT = Path(__file__).parent.resolve()
CR_LOGS_DIR = ROOT / "cr_logs"
STATE_DIR = ROOT / "state_snapshots"
BACKUP_DIR = ROOT / "agent_backups"
CR_LOGS_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

MAX_RETRIES = 2
MAX_EVOLUTION_PER_SESSION = 3
MAX_SELF_MODIFICATIONS = 5
PROTECTED_FILES = {
    "adaptive_agent.py",
    "values_kernel/invariants.py",
}

# ───────
# Logging
# ───────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s :: %(message)s",
)
logger = logging.getLogger("AdaptiveAgent")

# ─────────────────────────────────────────────────────────────────────
# External Imports — all optional with graceful fallback
# ─────────────────────────────────────────────────────────────────────
try:
    from conversation_engine.memory_engine import MemoryEngine
    from conversation_engine.summarizer import Summarizer
    from conversation_engine.action_registry import ACTIONS, ACTION_METADATA
    from conversation_engine.ethics_reflector import EthicsReflector
    from conversation_engine.self_model import build_self_model
except ImportError as e:
    logger.warning(f"Conversation engine import missing: {e}")

try:
    from groq_backend import generate_response as llm_call
except ImportError as e:
    logger.warning(f"Groq backend unavailable: {e}")
    llm_call = None  # type: ignore

# patch_generator is optional — only needed when _trigger_evolution fires.
# If it doesn't exist the evolution path logs a clear warning and skips proposal generation.
try:
    from patch_generator import propose_change_for_file
    _PATCH_GENERATOR_AVAILABLE = True
except ImportError:
    propose_change_for_file = None  # type: ignore
    _PATCH_GENERATOR_AVAILABLE = False
    logger.info("patch_generator not found — evolution proposals will be skipped until it is installed.")

# ─────────────────────────────────────────────────────────────────────
# Utility: reliable ISO timestamp string
# FIX: utils.local_now() has a trailing-comma bug that returns a tuple.
# We define our own here to avoid that entirely.
# ─────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    """Return current local time as a plain ISO 8601 string (never a tuple)."""
    return datetime.now().astimezone().isoformat()

# ───────────
# Dataclasses
# ───────────
@dataclass
class Capability:
    """Represents a single capability the agent possesses."""
    name: str
    description: str
    enabled: bool = True
    confidence: float = 1.0
    last_used: Optional[str] = None
    success_rate: float = 1.0
    attempts: int = 0
    successes: int = 0
    code_location: Optional[str] = None

@dataclass
class PlanStep:
    """Single step in an execution plan."""
    id: int
    description: str
    action: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: Optional[Any] = None
    error: Optional[str] = None
    retries: int = 0
    max_retries: int = MAX_RETRIES

@dataclass
class ExecutionPlan:
    """Complete plan for accomplishing a goal."""
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    status: str = "pending"
    created_at: str = field(default_factory=_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    current_step: int = 0

@dataclass
class SelfAnalysisReport:
    """Results of self-analysis."""
    # FIX: was field(default_factory=lambda: local_now()) which returned a tuple
    timestamp: str = field(default_factory=_now_iso)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)
    code_quality_score: float = 0.0
    performance_metrics: Dict[str, Any] = field(default_factory=dict)
    recommended_improvements: List[str] = field(default_factory=list)
    confidence: float = 0.0

@dataclass
class ExecutionResult:
    """Result of executing a plan step."""
    success: bool
    output: str
    action_used: str
    error_type: Optional[str] = None
    full_result: Any = None
    gap_info: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AnalysisResult:
    """Result of goal analysis."""
    status: str
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    plan_steps: List[Dict[str, Any]] = field(default_factory=list)

# ────────────────
# Security Helpers
# ────────────────
def derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """Derive encryption key from passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
        backend=default_backend(),
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase))

def encrypt_data(key: bytes, data: bytes) -> bytes:
    """Encrypt data using Fernet symmetric encryption."""
    return Fernet(key).encrypt(data)

def decrypt_data(key: bytes, data: bytes) -> bytes:
    """Decrypt data using Fernet symmetric encryption."""
    return Fernet(key).decrypt(data)

# ──────────────
# JSON Parsing
# ──────────────
def extract_json(raw_text: str) -> Optional[Any]:
    """
    Robust JSON extraction.
    Handles markdown fences and extracts the first valid JSON object or array.
    """
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    # Strip markdown fences
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    # Try direct parse first
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Find first JSON object or array by bracket depth
    start_positions = []
    obj_start = cleaned.find("{")
    arr_start = cleaned.find("[")
    if obj_start != -1:
        start_positions.append((obj_start, "{", "}"))
    if arr_start != -1:
        start_positions.append((arr_start, "[", "]"))
    if not start_positions:
        return None
    start_positions.sort(key=lambda x: x[0])
    start_idx, opener, closer = start_positions[0]
    depth = 0
    for i in range(start_idx, len(cleaned)):
        if cleaned[i] == opener:
            depth += 1
        elif cleaned[i] == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start_idx:i + 1])
                except Exception:
                    return None
    return None

# ─────────────────────────────────────────────────────────────────────────────
# LLM Response Cache
# Keyed by SHA-256(prompt + max_tokens). Eliminates duplicate Groq API calls
# for identical gate / plan prompts within a session, directly addressing the
# 6000 TPM free-tier bottleneck. Each entry stores (text, usage, timestamp).
# ─────────────────────────────────────────────────────────────────────────────

import hashlib as _hashlib
import threading as _threading

_LLM_CACHE: dict = {}           # cache_key -> (text, usage, inserted_at)
_LLM_CACHE_LOCK = _threading.Lock()
_LLM_CACHE_TTL_SECONDS: int = 300   # entries expire after 5 minutes
_LLM_CACHE_MAX_SIZE: int = 128      # evict oldest when full

# Calls that must NEVER be served from cache (non-deterministic / side-effectful)
_CACHE_BYPASS_PREFIXES: tuple = (
    "Generate new",
    "Write code",
    "Create a patch",
    "You are a memory extraction",
    "You are a self-reflector",
)


def _llm_cache_key(prompt: str, max_tokens: int) -> str:
    """Return a stable hex digest for (prompt, max_tokens)."""
    raw = f"{max_tokens}::{prompt}"
    return _hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _llm_cache_get(key: str):
    """
    Retrieve a cached LLM response.

    Returns ``(text, usage)`` if the entry exists and has not expired;
    otherwise returns ``None``.
    """
    import time as _time
    with _LLM_CACHE_LOCK:
        entry = _LLM_CACHE.get(key)
        if entry is None:
            return None
        text, usage, inserted_at = entry
        if _time.monotonic() - inserted_at > _LLM_CACHE_TTL_SECONDS:
            del _LLM_CACHE[key]
            return None
        return text, usage


def _llm_cache_put(key: str, text: str, usage: dict) -> None:
    """
    Store an LLM response in the cache.

    When the cache is at capacity (``_LLM_CACHE_MAX_SIZE``), the oldest
    entry is evicted before inserting the new one.
    """
    import time as _time
    with _LLM_CACHE_LOCK:
        if len(_LLM_CACHE) >= _LLM_CACHE_MAX_SIZE:
            # Evict the entry with the smallest inserted_at timestamp
            oldest_key = min(_LLM_CACHE, key=lambda k: _LLM_CACHE[k][2])
            del _LLM_CACHE[oldest_key]
        _LLM_CACHE[key] = (text, usage, _time.monotonic())


def llm_cache_stats() -> dict:
    """
    Return a snapshot of current cache state for diagnostics.

    Returns:
        dict with keys ``size``, ``max_size``, ``ttl_seconds``.
    """
    with _LLM_CACHE_LOCK:
        return {
            "size": len(_LLM_CACHE),
            "max_size": _LLM_CACHE_MAX_SIZE,
            "ttl_seconds": _LLM_CACHE_TTL_SECONDS,
        }


def _should_bypass_cache(prompt: str) -> bool:
    """
    Return True if this prompt must never be served from cache.

    Generation / extraction prompts are non-deterministic and must always
    hit the live API.  Structural classifier prompts (gate, plan) are
    deterministic given the same input and are safe to cache.
    """
    stripped = prompt.strip()
    return any(stripped.startswith(p) for p in _CACHE_BYPASS_PREFIXES)


# ───────────
# LLM Wrapper
# ───────────
def safe_llm_call(prompt: str, max_tokens: int = 2048, return_usage: bool = False):
    """
    Safe wrapper around LLM calls with error handling, configurable token
    budget, and an in-process response cache.

    Cache behaviour
    ---------------
    - Deterministic prompts (gate classifiers, plan prompts) are cached by
      SHA-256(prompt + max_tokens) for up to ``_LLM_CACHE_TTL_SECONDS``
      seconds (default 5 min) and up to ``_LLM_CACHE_MAX_SIZE`` entries.
    - Non-deterministic prompts (code generation, memory extraction) bypass
      the cache and always hit the live API.
    - Cached hits return a zeroed usage dict so callers that track token
      counts see 0 tokens consumed — truthfully, none were.

    Args:
        prompt:       The full prompt string to send to the LLM.
        max_tokens:   Maximum completion tokens to request.
        return_usage: If True, returns ``(text, usage_dict)`` instead of
                      just ``text``, so callers can track real token
                      counts / cost via ``agent_trace.TurnTrace.record_llm_call``.
                      Default False keeps every existing call site's
                      plain-string contract unchanged.

    Returns:
        ``str`` (return_usage=False) or ``(str, dict)`` (return_usage=True).
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if llm_call is None:
        return ("", empty_usage) if return_usage else ""

    # ── Cache lookup (skip for non-deterministic prompts) ─────────────────
    bypass = _should_bypass_cache(prompt)
    cache_key = _llm_cache_key(prompt, max_tokens)
    if not bypass:
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            cached_text, cached_usage = cached
            logger.debug("LLM cache hit (key=%s…)", cache_key[:12])
            return (cached_text, cached_usage) if return_usage else cached_text

    # ── Live API call ──────────────────────────────────────────────────────
    try:
        result = llm_call(prompt, max_tokens=max_tokens, return_usage=return_usage)
        if return_usage:
            text, usage = result if isinstance(result, tuple) else (result, empty_usage)
            text = str(text).strip() if text else ""
        else:
            text = str(result).strip() if result else ""
            usage = empty_usage

        # Store in cache only when it's safe to do so
        if not bypass and text:
            _llm_cache_put(cache_key, text, usage)

        return (text, usage) if return_usage else text

    except Exception:
        logger.exception("LLM call failed")
        return ("", empty_usage) if return_usage else ""

# ══════════════════════════════════════════════════════════════════════
# AdaptiveAgent
# ══════════════════════════════════════════════════════════════════════
class AdaptiveAgent:
    """
    Core adaptive agent for Partnership_AI.

    Active capabilities:
    - Goal analysis → action-based plan generation
    - Plan execution with per-step retry and gap detection
    - Safe code modification with backup and rollback
    - Evolution: CR generation when a capability gap is found
    - Ethics validation (Pass 1 + optional Pass 2 LLM check)
    - Encrypted memory read/write for learned identity blob
    - State snapshots (save/load)
    - Capability registry with confidence scoring
    - Self-analysis (codebase metrics)
    """

    def __init__(
        self,
        user_id: str,
        memory_engine: "MemoryEngine",
        summarizer: "Summarizer",
        encryption_key: Optional[bytes] = None,
        learned_file_path: Optional[Path] = None,
        root_dir: Optional[Path] = None,
    ):
        self.user_id = user_id
        self.memory_engine = memory_engine
        self.summarizer = summarizer
        self.encryption_key = encryption_key
        self.learned_file_path = learned_file_path
        self.root_dir = root_dir or ROOT

        # Ethics reflector — pass summarizer so Pass 2 LLM check is available
        try:
            self.ethics_reflector = EthicsReflector(summarizer=self.summarizer)
        except Exception:
            self.ethics_reflector = None
            logger.warning("Ethics reflector not available.")

        # Capability registry
        self.capabilities: Dict[str, Capability] = {}
        self._register_builtin_capabilities()
        self._load_capabilities_from_memory()

        # Action registry (from action_registry.py)
        try:
            from conversation_engine.action_registry import register_memory_engine
            register_memory_engine(self.memory_engine)
            self.available_actions: Dict[str, Any] = dict(ACTIONS)
        except Exception:
            self.available_actions = {}
            logger.warning("Action registry not available.")

        # Execution state
        self.current_plan: Optional[ExecutionPlan] = None
        self.execution_history: List[Dict[str, Any]] = []
        self.self_analysis_history: List[SelfAnalysisReport] = []
        self._current_trace: Optional[TurnTrace] = None  # set per-turn in run()

        # Agent-loop budget (best-practices #11: cap iterations/runtime/
        # cost per turn - previously unenforced entirely).
        agent_cfg = get_config().get("agent", {})
        self.max_plan_steps = agent_cfg.get("max_plan_steps", 12)
        self.max_runtime_seconds = agent_cfg.get("max_runtime_seconds", 60)
        self.max_llm_calls_per_turn = agent_cfg.get("max_llm_calls_per_turn", 8)
        self.max_replan_attempts = agent_cfg.get("max_replan_attempts", 1)

        # Safety counters
        self.modification_count = 0
        self.evolution_counter = 0

        # Self-model for introspection (file snapshot + symbol index)
        try:
            self.self_model = build_self_model(
                root_dir=self.root_dir,
                memory_engine=self.memory_engine,
            )
        except Exception as e:
            self.self_model = None
            logger.debug(f"SelfModel not available: {e}")

        # Bind runtime actions (secure memory + evolution)
        self._bind_runtime_actions()

        # Check for a previously persisted active plan to resume
        try:
            prior_plan = self.memory_engine.get_active_plan(user_id)
            if prior_plan and isinstance(prior_plan, dict) and prior_plan.get("steps"):
                logger.info("Found prior plan for user %s (goal: %s) — available for resume.",
                            user_id, prior_plan.get("goal", "unknown")[:80])
                self._resumable_plan = prior_plan
            else:
                self._resumable_plan = None
        except Exception:
            self._resumable_plan = None

        logger.info(
            "AdaptiveAgent initialized for user %s | %d capabilities | %d actions.",
            user_id, len(self.capabilities), len(self.available_actions),
        )

    # ═══════════════════════════
    # Capability Management
    # ═══════════════════════════

    def _register_builtin_capabilities(self):
        """Register the agent's core built-in capabilities."""
        builtin_caps = [
            Capability(
                name="plan_formation",
                description="Break down goals into executable action steps",
                confidence=1.0,
                code_location="adaptive_agent.py:create_plan",
            ),
            Capability(
                name="self_analysis",
                description="Analyse own codebase and report quality metrics",
                confidence=1.0,
                code_location="adaptive_agent.py:self_analyze",
            ),
            Capability(
                name="code_modification",
                description="Safely modify codebase files with backup",
                confidence=0.9,
                code_location="conversation_engine/tools/file_tools.py:generate_and_write_code",
            ),
            Capability(
                name="execution_monitoring",
                description="Monitor and retry plan execution steps",
                confidence=1.0,
                code_location="adaptive_agent.py:execute_plan",
            ),
            Capability(
                name="memory_management",
                description="Store and retrieve learned information",
                confidence=0.9,
                code_location="conversation_engine/memory_engine.py",
            ),
            Capability(
                name="dialogue_generation",
                description="Generate natural language responses",
                confidence=0.9,
                code_location="conversation_engine/dialogue_engine.py",
            ),
            Capability(
                name="ethics_validation",
                description="Validate responses against ethical principles",
                confidence=1.0,
                code_location="conversation_engine/ethics_reflector.py",
            ),
        ]
        for cap in builtin_caps:
            self.capabilities[cap.name] = cap

    def _load_capabilities_from_memory(self):
        """Load dynamically learned capabilities from the memory engine."""
        try:
            learned_data = self.memory_engine.get_memory(
                user_id=self.user_id,
                category="capabilities",
                default={},
            )
            if learned_data and isinstance(learned_data, dict):
                for cap_name, cap_data in learned_data.items():
                    if cap_name not in self.capabilities:
                        self.capabilities[cap_name] = Capability(
                            name=cap_name,
                            description=cap_data.get("description", "Dynamically acquired"),
                            enabled=cap_data.get("enabled", True),
                            confidence=cap_data.get("confidence", 0.8),
                            code_location=cap_data.get("code_location", "dynamic"),
                        )
                logger.info(f"[MEMORY] Loaded {len(learned_data)} custom capabilities.")
        except Exception as e:
            logger.warning(f"[MEMORY] Failed to load capabilities: {e}")

    def _save_capabilities_to_memory(self):
        """Persist current capability registry to the memory engine."""
        try:
            capabilities_dict = {
                name: {
                    "description": cap.description,
                    "enabled": cap.enabled,
                    "confidence": cap.confidence,
                    "code_location": cap.code_location,
                }
                for name, cap in self.capabilities.items()
            }
            # FIX: use set_memory() — store_memory() does not exist on MemoryEngine
            self.memory_engine.set_memory(
                user_id=self.user_id,
                category="capabilities",
                value=capabilities_dict,
            )
            logger.info(f"[MEMORY] Saved {len(capabilities_dict)} capabilities.")
        except Exception as e:
            logger.warning(f"[MEMORY] Error saving capabilities: {e}")

    def update_capability_metrics(self, capability_name: str, success: bool):
        """Update success rate and confidence for a capability after use."""
        if capability_name not in self.capabilities:
            return
        cap = self.capabilities[capability_name]
        cap.attempts += 1
        if success:
            cap.successes += 1
        cap.success_rate = cap.successes / cap.attempts
        cap.confidence = cap.success_rate
        cap.last_used = _now_iso()

    # ═══════════════════════════
    # Runtime Action Binding
    # ═══════════════════════════

    def _bind_runtime_actions(self):
        """
        Bind all runtime actions (secure memory, state, evolution) in one place.
        FIX: Previously split across _bind_secure_actions() and _bind_evolution_action()
        which both registered request_change, causing duplication.
        """
        # Evolution trigger — always available
        self.available_actions["request_change"] = (
            lambda capability, reasoning: self._trigger_evolution(
                user_goal="System evolution requested",
                missing_capability=capability,
                reasoning=reasoning,
            )
        )

        # Secure memory + state actions — only when encryption is configured
        if self.encryption_key and self.learned_file_path:
            self.available_actions["update_memory"] = self._perform_memory_update
            self.available_actions["delete_memory"] = self._perform_memory_delete
            self.available_actions["save_state"] = self._perform_save_state
            self.available_actions["load_state"] = self._perform_load_state
            logger.info("Secure memory and state actions registered.")
        else:
            logger.info("Secure memory disabled — running in standard mode.")

    # ═══════════════════════════
    # Ethics
    # ═══════════════════════════

    def _validate_ethics(self, text: str, action_type: str = "response") -> bool:
        """
        Pass 1 keyword ethics check on outbound text.
        Returns True if the text is clear to send.
        """
        if not self.ethics_reflector:
            return True
        try:
            _, issues = self.ethics_reflector.review(text)
            if issues:
                logger.warning("Ethics blocked %s: %s", action_type, issues)
                return False
            return True
        except Exception:
            logger.exception("Ethics validation error — allowing through.")
            return True

    def _validate_ethics_deep(self, user_input: str, response: str) -> Tuple[str, List[str]]:
        """
        Full two-pass ethics check (Pass 1 keyword + Pass 2 LLM behavioral).
        Returns (possibly_modified_response, list_of_issues).
        """
        if not self.ethics_reflector:
            return response, []
        try:
            return self.ethics_reflector.review_deep(user_input, response)
        except Exception:
            logger.exception("Deep ethics validation error — returning response unmodified.")
            return response, []

    # ═══════════════════════════
    # Goal Analysis & Planning
    # ═══════════════════════════

    def analyze_goal_and_gap(self, user_goal: str) -> AnalysisResult:
        """
        Two-stage goal analysis:

        Stage 1 — Intent classification (cheap, fast, small prompt).
          Determines whether the user wants a CONVERSATION (question, explanation,
          opinion, general chat) or an ACTION (file operation, memory store/recall,
          system status, etc.).
          Conversational requests are answered directly via respond_to_user — the
          planner never runs for them, which prevents spurious capability-gap
          evolution triggers on ordinary questions.

        Stage 2 — Action planning (only for action intents).
          Produces a minimal JSON plan using a compact action menu so the prompt
          stays within Groq's free-tier TPM limit (~6000 tokens/min).
        """
        history_snippet = getattr(self, '_session_history', '').strip()

        # ── Stage 1: intent gate ──────────────────────────────────────────────
        # The LLM reads the user's message in the context of the conversation
        # and decides: does this require a tool (action) or not (conversation)?
        # No tool lists, no special cases — just reasoning from context.
        gate_prompt = (
            "Based on what the user said and the conversation so far, "
            "does this require the system to DO something (an action), "
            "or is it just conversation?\n\n"
            f"Conversation context: {history_snippet}\n\n"
            f"User said: {user_goal}\n\n"
            "Reply with one word only: action OR conversation"
        )
        gate_text, gate_usage = safe_llm_call(gate_prompt, max_tokens=10, return_usage=True)
        self._track_llm_call(gate_usage)
        gate_raw = gate_text.strip().lower()
        intent = "action" if "action" in gate_raw else "conversation"

        if intent == "conversation":
            # No action needed - signal this cleanly rather than faking
            # a respond_to_user plan step. run() uses status=="conversation"
            # to return False, so new_main_chat.py can hand the user's
            # original text directly to DialogueEngine instead of routing
            # it through the action-execution/synthesis machinery at all.
            return AnalysisResult(status="conversation", plan_steps=[])

        # ── Stage 2: compact action menu ─────────────────────────────────────
        # Only name + required params (no descriptions, no optional params).
        # Keeps the planning prompt well under 2 000 tokens.
        action_menu = self._build_compact_action_menu()
        history_block = (
            f"\nRecent context:\n{history_snippet}\n" if history_snippet else ""
        )

        plan_prompt = (
            "You are the action planner for Partnership_AI.\n"
            "Return ONLY a valid JSON array of steps — no markdown, no prose.\n\n"
            "Available actions (name: required_params):\n"
            f"{action_menu}"
            f"{history_block}"
            f"\nUser goal: {user_goal}\n\n"
            "Each step: {\"action\": \"<name>\", \"parameters\": {{<key: value>}}}"
        )
        raw = safe_llm_call(plan_prompt, max_tokens=512)
        parsed = extract_json(raw)

        if not isinstance(parsed, list):
            # LLM said "action" but returned unparseable JSON — this is
            # a planning failure, not a conversation.  Trigger evolution.
            logger.warning("Goal analysis: action intent but LLM returned unparseable plan. Treating as gap.")
            return AnalysisResult(
                status="executable",
                plan_steps=[{
                    "action": "request_change",
                    "parameters": {
                        "capability": f"unparseable_plan: {user_goal[:120]}",
                        "reasoning": "Planner classified as action but returned unparseable JSON — likely a missing or misunderstood capability.",
                    },
                }],
            )

        plan_steps = []
        for step in parsed:
            if isinstance(step, dict) and "action" in step:
                plan_steps.append(step)
            else:
                logger.warning("Skipping malformed step: %s", step)

        if not plan_steps:
            # The LLM classified this as an action but couldn't produce
            # a plan — this is a capability gap, not a conversation.
            logger.warning("Goal analysis: action intent but no plan steps. Treating as capability gap.")
            return AnalysisResult(
                status="executable",
                plan_steps=[{
                    "action": "request_change",
                    "parameters": {
                        "capability": f"unplanned_action: {user_goal[:120]}",
                        "reasoning": "Planner classified as action but could not generate any plan steps — likely a missing capability.",
                    },
                }],
            )

        return AnalysisResult(status="executable", plan_steps=plan_steps)

    def _build_action_context(self) -> str:
        """Build a verbose readable description of available actions (for status/display)."""
        entries = []
        try:
            for name, meta in ACTION_METADATA.items():
                desc = meta.get("description", "No description")
                params = meta.get("parameters", [])
                param_str = ", ".join(
                    f"{p['name']}:{p.get('type','Any')} ({'required' if p.get('required') else 'optional'})"
                    for p in params
                ) or "(none)"
                entries.append(f"- {name}\n  {desc}\n  Params: {param_str}")
        except Exception:
            entries.append("- (action metadata unavailable)")
        return "\n\n".join(entries)

    def _build_compact_action_menu(self) -> str:
        """
        Compact single-line-per-action menu for use inside planning prompts.
        Format: <name>: <required_param>, <required_param>  (one line per action)
        Keeps planning prompts small enough to stay within Groq free-tier TPM limits.
        """
        lines = []
        try:
            for name, meta in ACTION_METADATA.items():
                params = meta.get("parameters", [])
                req = [p["name"] for p in params if p.get("required")]
                param_str = ", ".join(req) if req else "(no required params)"
                lines.append(f"  {name}: {param_str}")
        except Exception:
            lines.append("  (action metadata unavailable)")
        return "\n".join(lines)

    # ═══════════════════════════
    # Execution
    # ═══════════════════════════

    def _llm_budget_available(self) -> bool:
        """
        Read-only check of whether the per-turn LLM call/cost budget
        still has room, without incrementing anything. Used before
        calling a method (like analyze_goal_and_gap) that will record
        its own call(s) internally - using _track_llm_call() here
        instead would double-count: once as a pre-check, and again
        when the real call it gates actually happens.
        """
        if self._current_trace is None:
            return True
        if self._current_trace.llm_calls >= self.max_llm_calls_per_turn:
            return False
        max_cost = get_config().get("agent", {}).get("max_cost_usd_per_turn")
        if max_cost is not None and self._current_trace.estimated_cost_usd() >= max_cost:
            return False
        return True

    def _track_llm_call(self, usage: Optional[Dict[str, int]] = None) -> bool:
        """
        Record that an LLM call is about to happen (or, if `usage` is
        given, record one that just completed), and report whether the
        per-turn budget still allows further calls. Returns True if
        allowed, False if the budget is already exhausted (in which
        case the caller should skip the call and degrade gracefully
        rather than making an unbounded number of LLM calls in a
        single turn - best-practices #11).

        Checks both the call-count budget (max_llm_calls_per_turn) and,
        when usage data is available, a real dollar-cost budget
        (max_cost_usd_per_turn) - previously cost tracking was call-count
        only, a proxy rather than the literal MAX_COST the best-practices
        doc describes.
        """
        if self._current_trace is None:
            return True  # no active turn trace (e.g. called outside run()) - don't block
        if self._current_trace.llm_calls >= self.max_llm_calls_per_turn:
            self._current_trace.record_budget_event(
                f"LLM call budget exhausted ({self.max_llm_calls_per_turn}/turn) - skipping further LLM calls."
            )
            return False
        max_cost = get_config().get("agent", {}).get("max_cost_usd_per_turn")
        if max_cost is not None and self._current_trace.estimated_cost_usd() >= max_cost:
            self._current_trace.record_budget_event(
                f"Cost budget exhausted (${max_cost}/turn, spent ~${self._current_trace.estimated_cost_usd():.5f}) "
                f"- skipping further LLM calls."
            )
            return False
        self._current_trace.record_llm_call(usage)
        return True

    def _validate_step_schema(self, action_name: str, parameters: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate a planner-produced step against ACTION_METADATA before
        ever invoking the tool - catches hallucinated action names and
        unknown/missing parameters up front (best-practices #4, "verify
        everything"), rather than only reactively via a TypeError after
        the call already happened. execute_step_with_adaptation's
        existing try/except remains as a second, defense-in-depth layer
        for anything this doesn't catch.

        Returns (is_valid, reason). reason is empty when is_valid=True.
        """
        if action_name not in self.available_actions:
            return False, f"Missing capability: '{action_name}' is not a registered action."

        try:
            meta = ACTION_METADATA.get(action_name, {})
        except Exception:
            meta = {}
        param_specs = meta.get("parameters", []) if isinstance(meta, dict) else []
        if not param_specs:
            return True, ""  # no declared schema to check against - allow through

        known_names = {p.get("name") for p in param_specs if isinstance(p, dict)}
        required_names = {p.get("name") for p in param_specs if isinstance(p, dict) and p.get("required")}

        unknown = set(parameters.keys()) - known_names
        if unknown:
            return False, f"Unknown parameter(s) for '{action_name}': {sorted(unknown)}"

        missing = required_names - set(parameters.keys())
        if missing:
            return False, f"Missing required field(s) for '{action_name}': {sorted(missing)}"

        return True, ""

    def execute_plan_with_feedback(self, user_goal: str, plan: List[Dict[str, Any]]) -> str:
        """
        Execute a flat plan (list of action dicts) and return a factual
        overview of what happened - NOT a polished natural-language
        response. new_main_chat.py hands this overview to DialogueEngine,
        which is responsible for the actual user-facing wording (and
        runs its own self-reflection pass). Generating natural language
        here too would be redundant and cost an extra LLM call for
        output nobody uses.

        Adds, per AI-agent best practices:
        - Pre-execution schema validation for every step (see
          _validate_step_schema) instead of only reacting to a
          TypeError after the tool call already happened (#4).
        - A budget on plan length, wall-clock runtime, and LLM calls
          per turn (config's agent.max_plan_steps / max_runtime_seconds
          / max_llm_calls_per_turn) - previously completely unenforced
          (#11).
        - One bounded re-plan attempt (agent.max_replan_attempts) on a
          schema mismatch, instead of immediately giving up or
          incorrectly triggering code-generation "evolution" for what
          was actually just a hallucinated parameter name (#2, bounded
          ReAct loop rather than think-once/execute-blindly).
        - A structured trace record of the whole turn via
          agent_trace.TurnTrace (#12).

        On an unrecoverable capability gap, triggers evolution instead
        of continuing.
        """
        trace = self._current_trace
        turn_start = time.monotonic()

        if not plan:
            analysis = self.analyze_goal_and_gap(user_goal)
            if analysis.status == "conversation":
                # Nothing actionable here after all - let the caller's
                # run() surface this as "no action taken" rather than
                # trying to execute an empty plan.
                if trace:
                    trace.outcome = "conversation"
                return ""
            plan = analysis.plan_steps
            if not plan:
                if trace:
                    trace.outcome = "planner_failure"
                return self._trigger_evolution(
                    user_goal, "planner_failure", "Planner returned empty result."
                )

        if len(plan) > self.max_plan_steps:
            if trace:
                trace.record_budget_event(
                    f"Plan truncated: {len(plan)} steps exceeds max_plan_steps={self.max_plan_steps}"
                )
            logger.warning(
                "Plan had %d steps, truncating to max_plan_steps=%d",
                len(plan), self.max_plan_steps,
            )
            plan = plan[: self.max_plan_steps]

        execution_log: List[ExecutionResult] = []

        for step in plan:
            # ── Runtime budget check ──────────────────────────────────────
            elapsed = time.monotonic() - turn_start
            if elapsed > self.max_runtime_seconds:
                if trace:
                    trace.record_budget_event(
                        f"Runtime budget exceeded ({elapsed:.1f}s > {self.max_runtime_seconds}s) - stopping early."
                    )
                    trace.outcome = "runtime_budget_exceeded"
                logger.warning("Runtime budget exceeded mid-plan; returning partial results.")
                partial = "\n".join(f"- {r.action_used}: {r.output}" for r in execution_log)
                return (
                    f"Stopped early after exceeding the {self.max_runtime_seconds}s runtime budget.\n"
                    f"Completed so far:\n{partial or '(nothing completed yet)'}"
                )

            action_name = step.get("action") if isinstance(step, dict) else None
            parameters = step.get("parameters", {}) if isinstance(step, dict) else {}
            step_start = time.monotonic()

            # ── Pre-execution schema validation ───────────────────────────
            if action_name:
                is_valid, reason = self._validate_step_schema(action_name, parameters)
                if not is_valid:
                    if trace:
                        trace.record_step(action_name, parameters, False, reason,
                                           (time.monotonic() - step_start) * 1000, "schema_validation_failed")
                    replans_so_far = trace.replan_attempts if trace else 0
                    if replans_so_far < self.max_replan_attempts:
                        if trace:
                            trace.record_replan()
                        logger.info("Schema validation failed (%s); attempting re-plan %d/%d",
                                    reason, replans_so_far + 1, self.max_replan_attempts)
                        if self._llm_budget_available():
                            retry_analysis = self.analyze_goal_and_gap(
                                f"{user_goal}\n\n(Note: a previous attempt failed validation: {reason}. "
                                f"Please produce a corrected plan.)"
                            )
                            if retry_analysis.status != "conversation" and retry_analysis.plan_steps:
                                return self.execute_plan_with_feedback(user_goal, retry_analysis.plan_steps)
                    if trace:
                        trace.outcome = "schema_validation_failed"
                    return (
                        f"Attempted to use the '{action_name}' action but provided the wrong "
                        f"parameters. Details: {reason}"
                    )

            result = self.execute_step_with_adaptation(step, execution_log)
            execution_log.append(result)
            if trace:
                # Use to_dict() on the full ActionResult for richer trace data
                trace_result_data = None
                if hasattr(result, 'full_result') and result.full_result is not None:
                    fr = result.full_result
                    if hasattr(fr, 'to_dict'):
                        trace_result_data = fr.to_dict()
                trace.record_step(
                    result.action_used, parameters, result.success, result.output,
                    (time.monotonic() - step_start) * 1000,
                    None if result.success else "execution_error",
                )
            if not result.success:
                err = result.output or ""
                # ── Schema / parameter mismatch ───────────────────────────────
                # "Unknown parameter" or "Missing required field" means the
                # planner hallucinated a kwarg name.  This is NOT a capability
                # gap — triggering evolution would generate a CR against the
                # wrong file with an empty proposal.  Instead, return a clear
                # error message so the user knows to rephrase, and log a
                # diagnostic but do NOT evolve.
                if "unknown parameter" in err.lower() or "missing required field" in err.lower():
                    logger.warning(
                        "Schema mismatch on action '%s': %s — skipping evolution.",
                        result.action_used, err,
                    )
                    if trace:
                        trace.outcome = "schema_mismatch"
                    return (
                        f"Attempted to use the '{result.action_used}' action but "
                        f"provided the wrong parameters. Details: {err}"
                    )
                # ── True capability gap → evolve ──────────────────────────────
                if trace:
                    trace.outcome = "capability_gap_evolution"
                return self._trigger_evolution(
                    user_goal,
                    result.action_used,
                    result.output,
                )

        # Factual overview of what happened - no LLM call, just the log.
        # DialogueEngine turns this into the actual response the user sees.
        if trace:
            trace.outcome = "completed"
        overview = "\n".join(
            self._format_execution_result(r) for r in execution_log
        )
        return overview or "Actions completed with no output."

    def _format_execution_result(self, r) -> str:
        """
        Format an ExecutionResult into a clear, human-readable overview line.

        Extracts the actual data from the ActionResult instead of dumping
        the raw repr, and explicitly flags empty results so the LLM in
        DialogueEngine cannot misinterpret or fabricate data that does
        not exist.

        Args:
            r: The ExecutionResult to format.

        Returns:
            A readable string like:
              "incubator_get_all_ideas: Found 0 ideas. The incubator is currently empty."
            instead of:
              "incubator_get_all_ideas: ActionResult(success=True, data={'ideas': [], 'count': 0})"
        """
        action_name = r.action_used
        if not r.success:
            return f"- {action_name}: FAILED - {r.output}"

        # Try to extract structured data from the full ActionResult
        result = r.full_result
        if result is None:
            return f"- {action_name}: {r.output}"

        # Check if it is an ActionResult with .data
        data = None
        if hasattr(result, 'data'):
            data = result.data
        elif isinstance(result, dict):
            data = result.get('data')

        if data is None:
            return f"- {action_name}: completed successfully."

        # Format based on the structure of the data
        if isinstance(data, dict):
            # Check for common "empty result" patterns
            count = data.get('count', data.get('total', data.get('num_results')))
            items = data.get('ideas', data.get('results', data.get('items', data.get('files', data.get('tasks')))))

            if count is not None and count == 0:
                # Explicitly state empty - this is the anti-hallucination guard
                if 'ideas' in data:
                    return f"- {action_name}: Found 0 ideas. The idea incubator is currently empty - no ideas have been stored yet."
                elif 'results' in data:
                    return f"- {action_name}: Found 0 results. No matching items were found."
                elif 'files' in data:
                    return f"- {action_name}: Found 0 files. No files matched the query."
                elif 'tasks' in data:
                    return f"- {action_name}: Found 0 scheduled tasks. No tasks are currently scheduled."
                else:
                    return f"- {action_name}: Found 0 items. The result is empty."

            if items is not None and isinstance(items, list) and len(items) == 0:
                if 'ideas' in data:
                    return f"- {action_name}: Found 0 ideas. The idea incubator is currently empty - no ideas have been stored yet."
                elif 'results' in data:
                    return f"- {action_name}: Found 0 results. No matching items were found."
                elif 'files' in data:
                    return f"- {action_name}: Found 0 files. No files matched the query."
                elif 'tasks' in data:
                    return f"- {action_name}: Found 0 tasks. No scheduled tasks exist."
                else:
                    return f"- {action_name}: Found 0 items. The result is empty."

            # Non-empty result - format the data clearly
            if items is not None and isinstance(items, list) and len(items) > 0:
                summary_parts = [f"Found {len(items)} item(s):"]
                for item in items[:10]:
                    if isinstance(item, dict):
                        title = item.get('title', item.get('name', item.get('id', 'untitled')))
                        summary_parts.append(f"  - {title}")
                    else:
                        summary_parts.append(f"  - {str(item)[:100]}")
                if len(items) > 10:
                    summary_parts.append(f"  ... and {len(items) - 10} more")
                return f"- {action_name}: " + "\n".join(summary_parts)

            # Generic dict result - include key facts
            key_facts = []
            for k, v in data.items():
                if isinstance(v, (str, int, float, bool)):
                    key_facts.append(f"{k}={v}")
                elif isinstance(v, list):
                    key_facts.append(f"{k}=[{len(v)} items]")
                elif isinstance(v, dict):
                    key_facts.append(f"{k}={{...}}")
            if key_facts:
                return f"- {action_name}: " + ", ".join(key_facts)
            return f"- {action_name}: completed successfully."

        elif isinstance(data, list):
            if len(data) == 0:
                return f"- {action_name}: returned an empty list. No items found."
            summary_parts = [f"Found {len(data)} item(s):"]
            for item in data[:10]:
                if isinstance(item, dict):
                    title = item.get('title', item.get('name', item.get('id', 'untitled')))
                    summary_parts.append(f"  - {title}")
                else:
                    summary_parts.append(f"  - {str(item)[:100]}")
            if len(data) > 10:
                summary_parts.append(f"  ... and {len(data) - 10} more")
            return f"- {action_name}: " + "\n".join(summary_parts)

        elif isinstance(data, str):
            return f"- {action_name}: {data}"

        else:
            return f"- {action_name}: completed successfully."

    def execute_step_with_adaptation(
        self,
        step: Dict[str, Any],
        execution_log: List[ExecutionResult],
    ) -> ExecutionResult:
        """Execute a single flat step dict. Used by execute_plan_with_feedback()."""
        if not isinstance(step, dict):
            return ExecutionResult(
                success=False,
                output=f"Invalid step format: {type(step)}",
                action_used="none",
                error_type="invalid_step_format",
            )

        action_name = step.get("action")
        arguments = step.get("parameters", {})

        if not action_name:
            return ExecutionResult(
                success=False,
                output="Step missing action name.",
                action_used="none",
                error_type="missing_action",
            )

        if action_name not in self.available_actions:
            return ExecutionResult(
                success=False,
                output=f"Missing capability: {action_name}",
                action_used=action_name,
                error_type="missing_capability",
                gap_info={"capability_name": action_name, "reasoning": "Action not in registry."},
            )

        try:
            result = self.available_actions[action_name](**arguments)
            self.update_capability_metrics(action_name, success=True)
            return ExecutionResult(
                success=True,
                output=str(result),
                action_used=action_name,
                full_result=result,
            )
        except TypeError as e:
            self.update_capability_metrics(action_name, success=False)
            return ExecutionResult(
                success=False,
                output=f"Parameter error calling '{action_name}': {e}",
                action_used=action_name,
                error_type="parameter_error",
            )
        except Exception:
            logger.exception("Execution failure in step '%s'", action_name)
            self.update_capability_metrics(action_name, success=False)
            return ExecutionResult(
                success=False,
                output=f"Unhandled error in '{action_name}'.",
                action_used=action_name,
                error_type="execution_error",
            )

    # ═══════════════════════════
    # Code Generation & Modification
    # ═══════════════════════════

    # ═══════════════════════════
    # Self-Analysis
    # ═══════════════════════════

    def self_analyze(self) -> SelfAnalysisReport:
        """
        Analyse the codebase and return a SelfAnalysisReport with quality metrics.
        """
        report = SelfAnalysisReport()
        py_files = list(self.root_dir.rglob("*.py"))
        total_lines = 0
        total_functions = 0
        files_with_docstrings = 0

        for file in py_files:
            try:
                content = file.read_text(encoding="utf-8")
                total_lines += len(content.splitlines())
                tree = ast.parse(content)
                file_has_doc = False
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        total_functions += 1
                        if ast.get_docstring(node):
                            file_has_doc = True
                if file_has_doc:
                    files_with_docstrings += 1
            except Exception:
                pass  # Non-critical: file may not be parseable; skip cleanly

        report.performance_metrics = {
            "python_files": len(py_files),
            "total_lines": total_lines,
            "total_functions": total_functions,
            "files_with_docstrings": files_with_docstrings,
            "capabilities_registered": len(self.capabilities),
            "actions_registered": len(self.available_actions),
            "modifications_this_session": self.modification_count,
            "evolutions_this_session": self.evolution_counter,
        }

        # Simple quality score
        score = 0.4
        if total_functions > 0:
            score += 0.1
        if files_with_docstrings > len(py_files) * 0.5:
            score += 0.15
        if len(self.capabilities) >= 5:
            score += 0.1
        if len(self.execution_history) > 0:
            score += 0.1
        if self.modification_count == 0:
            score += 0.1
        if len(self.available_actions) >= 10:
            score += 0.05

        report.code_quality_score = min(round(score, 2), 1.0)
        report.confidence = report.code_quality_score

        # ── Use SelfModel snapshot & index for deeper introspection ──
        if getattr(self, 'self_model', None):
            try:
                snapshot = self.self_model.get_snapshot()
                if snapshot:
                    report.performance_metrics["files_in_snapshot"] = len(snapshot)
                    total_symbols = 0
                    for fname in list(snapshot.keys())[:50]:
                        idx = self.self_model.get_index(fname)
                        total_symbols += len(idx.get("functions", [])) + len(idx.get("classes", []))
                    report.performance_metrics["total_symbols_indexed"] = total_symbols
            except Exception as e:
                logger.debug(f"[self_analyze] SelfModel snapshot unavailable: {e}")

            # Push introspection insights to memory for future reference
            try:
                self.self_model.push_insights_to_memory(self.user_id)
            except Exception as e:
                logger.debug(f"[self_analyze] Could not push insights to memory: {e}")

        # Persist current capability registry to memory
        self._save_capabilities_to_memory()

        # Include detailed action context in analysis
        try:
            action_ctx = self._build_action_context()
            if action_ctx:
                report.performance_metrics["action_context_length"] = len(action_ctx)
        except Exception:
            pass

        self.self_analysis_history.append(report)
        return report

    def reflect_on_execution(self, result: str):
        """
        Privately reflect on execution outcome and store lessons in memory.
        Uses set_memory() (the correct MemoryEngine API).
        """
        if not result:
            return
        prompt = f"""
Analyse this execution result and extract improvement insights.

Result: {result}

Return ONLY JSON in this exact format:
{{
  "successes": ["..."],
  "improvements": ["..."],
  "lessons": ["..."],
  "optimizations": ["..."]
}}
"""
        try:
            raw = safe_llm_call(prompt, max_tokens=512)
            reflection = extract_json(raw) or {}
            # FIX: was self.memory_engine.store_memory() which does not exist
            self.memory_engine.set_memory(
                user_id=self.user_id,
                category="execution_reflections",
                value={
                    "timestamp": _now_iso(),
                    "result_summary": result[:200],
                    "reflection": reflection,
                },
            )
        except Exception as e:
            logger.warning(f"Reflection storage failed: {e}")

    # ═══════════════════════════
    # Evolution (Change Requests)
    # ═══════════════════════════

    # ═════════════════════════════════════════
    # Post-turn capability gap detector
    # ═════════════════════════════════════════

    # Phrases that signal the AI admitted it cannot do something
    _CANNOT_PHRASES: tuple = (
        "i don't have",
        "i do not have",
        "i can't",
        "i cannot",
        "i am unable",
        "i'm unable",
        "not capable",
        "no access to",
        "don't have access",
        "i lack the ability",
        "outside my capabilities",
        "beyond my current",
        "i don't support",
        "unable to help with",
        "i'm not able to",
        # ── Expanded coverage ──
        "unfortunately, i",
        "that's not something i can",
        "that is not something i can",
        "i don't currently have",
        "i do not currently have",
        "not something i'm able to",
        "not something i am able to",
        "i don't have the ability",
        "i do not have the ability",
        "i'm not equipped to",
        "i am not equipped to",
        "no way for me to",
        "i don't have access to",
        "i do not have access to",
        "can't help with that",
        "cannot help with that",
        "won't be able to",
        "will not be able to",
        "i don't have the capability",
        "i do not have the capability",
        "not within my current scope",
        "i'm limited to",
        "i am limited to",
        "i can only",
        "that's beyond what i can",
        "that is beyond what i can",
        "i don't know how to",
        "i do not know how to",
        "not possible for me",
        "i have no way to",
    )

    def _detect_capability_gap(self, user_goal: str, response: str) -> bool:
        """
        Scan ``response`` for phrases that indicate the AI admitted it cannot
        fulfil the user's request. When such a phrase is found, log a feedback
        entry to ``feedback_memory.json`` and fire ``_trigger_evolution`` so
        the gap is captured as a Change Request for human review.

        Args:
            user_goal: The original user message that prompted ``response``.
            response:  The final string returned to the user this turn.

        Returns:
            True if a gap was detected and evolution was triggered, False otherwise.
        """
        lowered = response.lower()
        matched_phrase = next(
            (p for p in self._CANNOT_PHRASES if p in lowered),
            None,
        )
        if matched_phrase is None:
            return False

        logger.info(
            "[GapDetector] Capability gap detected in response (phrase: %r). "
            "Triggering evolution.",
            matched_phrase,
        )

        # ── Persist to feedback_memory.json ──────────────────────────────────
        fb_path = ROOT / "feedback_memory.json"
        try:
            existing: list = []
            if fb_path.exists():
                try:
                    import json as _json
                    existing = _json.loads(fb_path.read_text())
                except Exception:
                    pass

            import json as _json, datetime as _dt
            entry = {
                "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
                "user_goal": user_goal[:500],
                "detected_phrase": matched_phrase,
                "response_snippet": response[:300],
            }
            if not isinstance(existing, list):
                existing = []
            existing.append(entry)
            fb_path.write_text(_json.dumps(existing, indent=2))
        except Exception as e:
            logger.warning("[GapDetector] Could not write feedback_memory.json: %s", e)

        # ── Check if SelfModel confirms the capability exists ──────────────
        # If the self_model reports we DO have this capability, the gap
        # is likely a prompt/planning issue, not a true capability gap.
        # Still log it for review, but mark it as a soft gap.
        soft_gap = False
        if getattr(self, 'self_model', None):
            try:
                # Try to match the user goal against known capabilities
                for cap_name in self.self_model.list_files():
                    if any(word in cap_name.lower() for word in user_goal.lower().split()[:3]):
                        if self.self_model.has_capability(cap_name):
                            soft_gap = True
                            break
            except Exception:
                pass  # SelfModel check is best-effort

        # ── Fire evolution ────────────────────────────────────────────────────
        self._trigger_evolution(
            user_goal=user_goal,
            missing_capability=f"user_request: {user_goal[:120]}",
            reasoning=(
                f"Post-turn gap detector: response contained phrase {matched_phrase!r}, "
                "indicating AI was unable to fulfil the user's request."
            ),
        )
        return True

    def _trigger_evolution(
        self,
        user_goal: str,
        missing_capability: str,
        reasoning: str,
    ) -> str:
        """
        Trigger system evolution when a capability gap is detected.
        Generates a Change Request (CR) for human review via reviewer.py.
        Respects MAX_EVOLUTION_PER_SESSION.
        """
        if self.evolution_counter >= MAX_EVOLUTION_PER_SESSION:
            return (
                f"⚠️ Evolution limit ({MAX_EVOLUTION_PER_SESSION}) reached this session. "
                "Please run `python reviewer.py` to review pending CRs, then restart."
            )

        self.evolution_counter += 1
        logger.info("Evolution triggered for capability: %s", missing_capability)

        if not _PATCH_GENERATOR_AVAILABLE:
            # Log the gap as a CR without a code proposal
            cr_path = self._log_cr(
                user_goal=user_goal,
                capability=missing_capability,
                reasoning=f"{reasoning} (patch_generator not installed — proposal left blank)",
                proposal={},
            )
            return (
                f"🔹 Capability gap logged: '{missing_capability}'\n"
                f"CR created: {cr_path.name}\n"
                "Install patch_generator.py to enable automated proposals."
            )

        target_files = self._identify_target_files(missing_capability)
        proposals = []
        for target in target_files:
            resolved = str(Path(target).resolve())
            protected = {str((ROOT / p).resolve()) for p in PROTECTED_FILES}
            if resolved in protected:
                logger.warning("Protected file skipped in evolution: %s", target)
                continue
            target_path = Path(target)
            if not target_path.exists():
                logger.warning("Target file not found: %s", target)
                continue
            try:
                capability_ctx = (
                    f"Missing capability: '{missing_capability}'. "
                    f"Reason: {reasoning}"
                )
                proposal = propose_change_for_file(target_path, capability_context=capability_ctx)
                if proposal:
                    proposals.append({"target": target, "proposal": proposal})
            except Exception:
                logger.exception("Proposal generation failed for %s", target)

        if not proposals:
            cr_path = self._log_cr(user_goal, missing_capability, reasoning, {})
            return (
                f"🔹 Capability gap logged: '{missing_capability}'\n"
                f"CR created: {cr_path.name} (no proposal could be generated)."
            )

        cr_files = [
            self._log_cr(user_goal, missing_capability, reasoning, item)
            for item in proposals
        ]
        return (
            f"🔹 Capability gap: '{missing_capability}'\n"
            "Generated Change Requests for human review:\n"
            + "\n".join(f"  - {f.name}" for f in cr_files)
            + "\nRun `python reviewer.py` to review and approve."
        )

    def _identify_target_files(self, missing_capability: str) -> List[str]:
        """
        Map a missing capability name to the tool file that *implements* it.

        The old version routed every registered action name to action_registry.py.
        That was wrong — action_registry.py only holds the registry machinery;
        the action function bodies live in conversation_engine/tools/*.py.
        Sending the LLM to the wrong file produces useless proposals.
        """
        lowered = missing_capability.lower()

        # ── Action name → implementation file ────────────────────────────────
        # Each key is an exact registered action name.  The value is the tool
        # file that contains the @register_action decorated function body.
        ACTION_FILE_MAP: Dict[str, str] = {
            # file_tools.py
            "list_files":             "conversation_engine/tools/file_tools.py",
            "read_file":              "conversation_engine/tools/file_tools.py",
            "search_code":            "conversation_engine/tools/file_tools.py",
            "get_file_stats":         "conversation_engine/tools/file_tools.py",
            "analyze_code_quality":   "conversation_engine/tools/file_tools.py",
            "propose_upgrade":        "conversation_engine/tools/file_tools.py",
            "generate_and_write_code":"conversation_engine/tools/file_tools.py",
            # memory_tools.py
            "store_fact":                    "conversation_engine/tools/memory_tools.py",
            "recall_memory":                 "conversation_engine/tools/memory_tools.py",
            "update_memory":                 "conversation_engine/tools/memory_tools.py",
            "delete_memory":                 "conversation_engine/tools/memory_tools.py",
            "store_vocabulary_metric":       "conversation_engine/tools/memory_tools.py",
            "recall_vocabulary_metric":      "conversation_engine/tools/memory_tools.py",
            "recall_all_vocabulary_metrics": "conversation_engine/tools/memory_tools.py",
            "delete_vocabulary_metric":      "conversation_engine/tools/memory_tools.py",
            # state_tools.py
            "save_state":             "conversation_engine/tools/state_tools.py",
            "load_state":             "conversation_engine/tools/state_tools.py",
            "refresh_metadata":       "conversation_engine/tools/state_tools.py",
            "get_function_signatures":"conversation_engine/tools/state_tools.py",
            "list_capabilities":      "conversation_engine/tools/state_tools.py",
            "get_system_status":      "conversation_engine/tools/state_tools.py",
            # agent_tools.py
            "respond_to_user": "conversation_engine/tools/agent_tools.py",
            "request_change":  "conversation_engine/tools/agent_tools.py",
            # incubator_tools.py
            "incubator_insert_idea":    "conversation_engine/tools/incubator_tools.py",
            "incubator_get_all_ideas":  "conversation_engine/tools/incubator_tools.py",
            "incubator_search_ideas":   "conversation_engine/tools/incubator_tools.py",
            "incubator_analyze_idea":   "conversation_engine/tools/incubator_tools.py",
            "incubator_generate_ideas": "conversation_engine/tools/incubator_tools.py",
            "incubator_connect_ideas":  "conversation_engine/tools/incubator_tools.py",
            "incubator_get_statistics": "conversation_engine/tools/incubator_tools.py",
            "incubator_ask_natural":    "conversation_engine/tools/incubator_tools.py",
            # web_tools.py
            "web_search":               "conversation_engine/tools/web_tools.py",
            "fetch_url":                "conversation_engine/tools/web_tools.py",
            # scheduler_tools.py
            "schedule_task":            "conversation_engine/tools/scheduler_tools.py",
            "list_scheduled":            "conversation_engine/tools/scheduler_tools.py",
            "cancel_task":              "conversation_engine/tools/scheduler_tools.py",
            # diff_tools.py
            "watch_file":               "conversation_engine/tools/diff_tools.py",
            "check_file_diff":          "conversation_engine/tools/diff_tools.py",
            "list_watched":             "conversation_engine/tools/diff_tools.py",
        }

        if lowered in ACTION_FILE_MAP:
            return [ACTION_FILE_MAP[lowered]]

        # ── Engine-level gaps (keyword patterns) ─────────────────────────────
        if "planner" in lowered or "plan" in lowered:
            # Was "conversation_engine/intent_resolver.py" - that module (and
            # the rest of DialogueEngine's now-unreachable Mode 2 pipeline)
            # was removed. Planning logic (analyze_goal_and_gap,
            # execute_plan_with_feedback) lives in this file now.
            return ["adaptive_agent.py"]
        if "ethics" in lowered:
            return ["conversation_engine/ethics_reflector.py"]
        if "dialogue" in lowered or "response" in lowered:
            return ["conversation_engine/dialogue_engine.py"]
        if "memory" in lowered:
            return ["conversation_engine/memory_engine.py"]
        if "action" in lowered or "capability" in lowered:
            return ["conversation_engine/action_registry.py"]
        if "state" in lowered or "status" in lowered or "snapshot" in lowered:
            return ["conversation_engine/tools/state_tools.py"]
        if "incubator" in lowered or "idea" in lowered:
            return ["conversation_engine/tools/incubator_tools.py"]

        # ── Default: use SelfModel for intelligent file discovery ─────────────
        if getattr(self, 'self_model', None):
            try:
                related = self.self_model.files_related_to(lowered)
                if related:
                    return related
            except Exception:
                pass

        return ["conversation_engine/action_registry.py"]

    def _log_cr(
        self,
        user_goal: str,
        capability: str,
        reasoning: str,
        proposal: Dict[str, Any],
    ) -> Path:
        """
        Write a Change Request JSON file to cr_logs/ in the canonical schema:

            {
              "change": true,
              "reason": "<why this improves it>",
              "change_details": {
                "location": "<function or lines>",
                "old":      "<original snippet>",
                "new":      "<replacement snippet>"
              },
              "new_code":       "<full file after change>",
              "ethics_context": "<ethical reasoning>",
              -- plus reviewer envelope fields --
              "timestamp", "user_goal", "missing_capability",
              "reasoning", "file", "status"
            }

        If the incoming proposal dict (from patch_generator) already has this
        shape we use it directly.  If it has no new_code (gap-only CR with no
        patch_generator available), we ask the LLM to generate the proposal
        so the reviewer always has something actionable to display and apply.

        The full new_code is also written to patches/<filename> so reviewer.py
        can find it via the old-format apply path.
        """
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"CR_{ts_str}.json"
        path = CR_LOGS_DIR / filename

        # ── Resolve target file from proposal or capability name ──────────────
        target_file: str = ""
        if isinstance(proposal, dict):
            # patch_generator wraps as {"target": ..., "proposal": {...}}
            if "target" in proposal:
                target_file = proposal["target"]
                proposal = proposal.get("proposal", proposal)
            elif "file" in proposal:
                target_file = proposal["file"]

        if not target_file:
            # Derive from capability name using the same logic as _identify_target_files
            targets = self._identify_target_files(capability)
            target_file = targets[0] if targets else ""

        # ── Ensure proposal is in canonical schema ────────────────────────────
        # patch_generator already returns the canonical shape; we just need to
        # handle the gap-only case where proposal == {} (no patch_generator) by
        # asking the LLM to produce the canonical JSON.
        canonical: Dict[str, Any] = {}

        if isinstance(proposal, dict) and proposal.get("new_code"):
            # Already canonical — use as-is
            canonical = proposal
        elif target_file:
            canonical = self._llm_generate_proposal(
                target_file=target_file,
                capability=capability,
                reasoning=reasoning,
                user_goal=user_goal,
            )

        # ── Write new_code to patches/ so reviewer can apply it ──────────────
        patches_dir = ROOT / "patches"
        patches_dir.mkdir(exist_ok=True)
        patch_written = False

        new_code = canonical.get("new_code", "")
        if new_code and target_file:
            patch_path = patches_dir / Path(target_file).name
            try:
                # Syntax-check before writing
                if target_file.endswith(".py"):
                    ast.parse(new_code)
                tmp = patch_path.with_suffix(patch_path.suffix + ".tmp")
                tmp.write_text(new_code, encoding="utf-8")
                os.replace(tmp, patch_path)
                patch_written = True
                logger.info("Patch written: %s", patch_path.name)
            except SyntaxError as exc:
                logger.warning("LLM-generated patch failed syntax check: %s", exc)
                canonical["new_code"] = ""
                canonical["syntax_error"] = str(exc)
            except Exception as exc:
                logger.warning("Failed to write patch file: %s", exc)

        # ── Build CR envelope ─────────────────────────────────────────────────
        data: Dict[str, Any] = {
            # Canonical proposal fields (reviewer display + apply)
            "change":         canonical.get("change", bool(new_code)),
            "reason":         canonical.get("reason", reasoning),
            "change_details": canonical.get("change_details", {
                "location": capability,
                "old":      "",
                "new":      canonical.get("new_code", "")[:300],
            }),
            "new_code":       canonical.get("new_code", ""),
            "ethics_context": canonical.get("ethics_context", ""),
            # Reviewer envelope fields
            "timestamp":           _now_iso(),
            "user_goal":           user_goal,
            "missing_capability":  capability,
            "reasoning":           reasoning,
            "file":                target_file,
            "patch_written":       patch_written,
            "status":              "PENDING_APPROVAL",
            # Extra diagnostic fields (not used by reviewer but useful in logs)
            "classifier_status":   canonical.get("classifier_status", ""),
            "classifier_reason":   canonical.get("classifier_reason", ""),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("CR logged: %s", filename)
        return path

    def _llm_generate_proposal(
        self,
        target_file: str,
        capability: str,
        reasoning: str,
        user_goal: str,
    ) -> Dict[str, Any]:
        """
        Ask the LLM to produce a canonical proposal JSON for a capability gap
        when patch_generator is not available or returned no proposal.

        Returns the parsed dict, or an empty dict on failure.
        """
        target_path = ROOT / target_file
        if not target_path.exists():
            return {}

        try:
            source = target_path.read_text(encoding="utf-8", errors="ignore")
            # Truncate to ~1 500 chars so the full prompt stays under 3 000 tokens,
            # well within Groq free-tier limits and leaving room for the reply.
            if len(source) > 1500:
                source = source[:1500] + "\n# ... (truncated for proposal prompt)"
        except Exception:
            return {}

        prompt = (
            "You are a code-improvement assistant for Partnership_AI.\n"
            "A capability gap was detected during a user session.\n\n"
            f"Missing capability : {capability}\n"
            f"Error / reasoning  : {reasoning}\n"
            f"User goal          : {user_goal}\n"
            f"File to modify     : {target_file}\n\n"
            "Propose ONE minimal, safe fix. "
            "Return ONLY valid JSON — no markdown fences, no extra text.\n\n"
            "If you can fix it, respond EXACTLY like this:\n"
            "{\n"
            '  "change": true,\n'
            '  "reason": "<why this improves it>",\n'
            '  "change_details": {\n'
            '    "location": "<function or lines>",\n'
            '    "old": "<original snippet>",\n'
            '    "new": "<replacement snippet>"\n'
            '  },\n'
            '  "new_code": "<full file after change>",\n'
            '  "ethics_context": "<ethical reasoning>"\n'
            "}\n\n"
            "If no change is needed: { \"change\": false }\n\n"
            f"── FILE START ──\n{source}\n── FILE END ──"
        )

        # Limit to 2048 tokens to stay within Groq free-tier per-request limits.
        # The source is already truncated above; the schema JSON reply is short.
        raw = safe_llm_call(prompt, max_tokens=2048)
        if not raw:
            return {}

        # Try direct parse first, then strip fences
        def try_parse(text: str) -> Optional[Dict]:
            """Attempt to parse *text* as JSON; return None on failure."""
            try:
                return json.loads(text)
            except Exception:
                return None

        parsed = try_parse(raw)
        if not parsed:
            # Strip ```json ... ``` fences
            m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
            if m:
                parsed = try_parse(m.group(1).strip())
        if not parsed:
            # Last resort: find first { ... } block
            m = re.search(r"(\{[\s\S]+\})", raw)
            if m:
                parsed = try_parse(m.group(1))

        if not parsed or not isinstance(parsed, dict):
            return {}
        if not parsed.get("change"):
            return {}
        return parsed

    # ═══════════════════════════
    # Encrypted Memory Management
    # ═══════════════════════════

    def _load_memory_blob(self) -> Dict[str, Any]:
        """Load and decrypt the learned identity memory blob."""
        if not self.learned_file_path or not self.encryption_key:
            raise RuntimeError("Encrypted memory is not configured.")
        encrypted = self.learned_file_path.read_bytes()
        decrypted = decrypt_data(self.encryption_key, encrypted)
        return json.loads(decrypted.decode())

    def _save_memory_blob(self, data: Dict[str, Any]):
        """Encrypt and write the learned identity memory blob."""
        if not self.learned_file_path or not self.encryption_key:
            raise RuntimeError("Encrypted memory is not configured.")
        serialized = json.dumps(data, indent=2)
        encrypted = encrypt_data(self.encryption_key, serialized.encode())
        # Atomic write via temp file
        tmp = self.learned_file_path.with_suffix(".tmp")
        tmp.write_bytes(encrypted)
        os.replace(tmp, self.learned_file_path)

    def _perform_memory_update(
        self,
        fact_key: str,
        new_value: str,
        category: str = "general",
    ) -> str:
        """Update a fact in the encrypted persistent memory blob."""
        try:
            data = self._load_memory_blob()
            updated = False
            for item in data.get("persistent_facts", []):
                if fact_key in item.get("fact", ""):
                    item["fact"] = new_value
                    item["category"] = category
                    item["updated_at"] = _now_iso()
                    updated = True
            if not updated:
                return f"Fact containing '{fact_key}' not found."
            self._save_memory_blob(data)
            return f"Memory updated: '{fact_key}'."
        except Exception:
            logger.exception("Memory update failed")
            return "Memory update failed."

    def _perform_memory_delete(self, fact_key: str, confirm: bool = False) -> str:
        """Delete facts matching fact_key from the encrypted memory blob."""
        if not confirm:
            return "Deletion aborted — pass confirm=True to proceed."
        try:
            data = self._load_memory_blob()
            original_count = len(data.get("persistent_facts", []))
            data["persistent_facts"] = [
                x for x in data.get("persistent_facts", [])
                if fact_key not in x.get("fact", "")
            ]
            removed = original_count - len(data["persistent_facts"])
            self._save_memory_blob(data)
            return f"Deleted {removed} fact(s) matching '{fact_key}'."
        except Exception:
            logger.exception("Memory deletion failed")
            return "Memory deletion failed."

    # ═══════════════════════════
    # State Snapshots
    # ═══════════════════════════

    def _perform_save_state(self, snapshot_name: Optional[str] = None) -> str:
        """Save a lightweight state snapshot to state_snapshots/."""
        try:
            name = snapshot_name or datetime.now().strftime("snapshot_%Y%m%d_%H%M%S")
            path = STATE_DIR / f"{name}.json"
            payload = {
                "name": name,
                "created": _now_iso(),
                "user_id": self.user_id,
                "capabilities_count": len(self.capabilities),
                "modifications_count": self.modification_count,
                "evolutions_count": self.evolution_counter,
            }
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
            return f"State saved: {path.name}"
        except Exception:
            logger.exception("State save failed")
            return "Failed to save state."

    def _perform_load_state(self, snapshot_name: str) -> str:
        """Load a state snapshot from state_snapshots/."""
        try:
            path = STATE_DIR / f"{snapshot_name}.json"
            if not path.exists():
                return f"Snapshot not found: {snapshot_name}"
            with open(path) as f:
                data = json.load(f)
            return (
                f"Loaded snapshot: {data.get('name')}\n"
                f"Created: {data.get('created')}\n"
                f"Capabilities at save time: {data.get('capabilities_count')}"
            )
        except Exception:
            logger.exception("State load failed")
            return "Failed to load state."

    # ═══════════════════════════
    # Status & Main Entry Point
    # ═══════════════════════════

    def get_status(self) -> Dict[str, Any]:
        """Return a snapshot of the agent's current operational status."""
        return {
            "user_id": self.user_id,
            "capabilities": {
                name: {
                    "enabled": cap.enabled,
                    "confidence": round(cap.confidence, 3),
                    "success_rate": round(cap.success_rate, 3),
                    "attempts": cap.attempts,
                    "description": cap.description,
                }
                for name, cap in self.capabilities.items()
            },
            "current_plan": self.current_plan.goal if self.current_plan else None,
            "modifications_this_session": self.modification_count,
            "max_modifications": MAX_SELF_MODIFICATIONS,
            "evolutions_this_session": self.evolution_counter,
            "max_evolutions": MAX_EVOLUTION_PER_SESSION,
            "execution_history_count": len(self.execution_history),
            "self_analysis_count": len(self.self_analysis_history),
            "patch_generator_available": _PATCH_GENERATOR_AVAILABLE,
        }


    # ═══════════════════════════════════════
    # Capability Query Detection
    # ═══════════════════════════════════════

    # Phrases that indicate the user is asking about capabilities.
    _CAPABILITY_QUERY_PHRASES = [
        "what can you do",
        "what are your capabilities",
        "what are you capable of",
        "what can you help with",
        "what can you help me",
        "what do you do",
        "what are your features",
        "what tools do you have",
        "what actions can you take",
        "what can partnership_ai do",
        "what can the ai do",
        "what can you handle",
        "what are your abilities",
        "what are you able to do",
        "what functions do you have",
        "what are your skills",
        "what are you good at",
        "tell me about your capabilities",
        "tell me what you can do",
        "show me your capabilities",
        "list your capabilities",
        "what can\b.*\bdo\b",
        "how can you help",
        "what are you",
        "who are you",
        "what is partnership",
        "what is partnership_ai",
    ]

    def _is_capability_query(self, user_input: str) -> bool:
        """
        Detect whether the user is asking about the agent's capabilities.

        Uses a combination of exact phrase matching and regex patterns
        to catch natural variations like "what can you actually do for me"
        or "what kind of things can you handle".

        Args:
            user_input: The raw user message.

        Returns:
            True if this looks like a capability query, False otherwise.
        """
        lowered = user_input.lower().strip()

        # Quick exact-phrase check first (fast path)
        for phrase in self._CAPABILITY_QUERY_PHRASES:
            if phrase in lowered:
                return True

        # Regex fallback: "what can you ... do" pattern
        if re.search(r"what\s+can\s+you\b.*\bdo\b", lowered):
            return True

        # "what are you" + "capab/able/do/help" within 60 chars
        if "what are you" in lowered:
            for keyword in ("capab", "able to", "help", "do for", "features"):
                if keyword in lowered:
                    return True

        return False

    def _build_capability_overview(self, user_input: str, session_history: str) -> str:
        """
        Build a rich, context-aware capabilities overview for the LLM.

        Produces a factual system_override string that includes:
        - All registered actions with descriptions and parameters
        - The user's original question
        - Recent conversation context (if any) so the LLM can highlight
          capabilities relevant to what was just discussed
        - Plugin status (if any plugins are loaded)

        Args:
            user_input:      The user's current message.
            session_history: Recent conversation context.

        Returns:
            A string to pass to DialogueEngine as system_override.
        """
        # Build the full action list with descriptions
        action_lines = []
        try:
            for name, meta in ACTION_METADATA.items():
                desc = meta.get("description", "No description available")
                params = meta.get("parameters", [])
                param_str = ", ".join(
                    p["name"]
                    for p in params
                ) if params else "none"
                action_lines.append(f"  • {name}({param_str}) — {desc}")
        except Exception:
            action_lines.append("  (action metadata unavailable)")

        actions_block = "\n".join(action_lines)

        # Build conversation context hint
        context_hint = ""
        if session_history and session_history.strip():
            # Extract last few user messages from session history
            # to identify topics the user has been discussing
            recent_user_msgs = []
            for line in session_history.split("\n"):
                if line.strip().startswith("USER:"):
                    msg = line.strip()[5:].strip()[:200]
                    if msg:
                        recent_user_msgs.append(msg)
            if recent_user_msgs:
                context_hint = (
                    f"\n\nRecent conversation context (the user has been discussing):\n"
                    + "\n".join(f"  - {msg}" for msg in recent_user_msgs[-5:])
                    + "\n\nWhen describing capabilities, highlight which ones are most "
                    "relevant to what the user has been talking about, while still "
                    "listing all available actions."
                )

        # Plugin info
        plugin_hint = ""
        try:
            from conversation_engine.plugin_loader import get_plugin_status
            statuses = get_plugin_status()
            if statuses:
                active = [name for name, active in statuses if active]
                if active:
                    plugin_hint = f"\n\nAdditional plugin capabilities: {', '.join(active)}"
        except Exception:
            pass

        # Capability count
        cap_count = len(ACTION_METADATA) if ACTION_METADATA else 0

        overview = (
            f"CAPABILITY OVERVIEW\n"
            f"=====================\n"
            f"The user asked: \"{user_input}\"\n"
            f"You have {cap_count} registered actions available.\n\n"
            f"Here are ALL your capabilities (action name, parameters, description):\n"
            f"{actions_block}\n"
            f"{plugin_hint}"
            f"{context_hint}\n\n"
            f"INSTRUCTIONS: Respond to the user naturally. List your capabilities in a "
            f"clear, organized way. Group related actions together (e.g., file operations, "
            f"memory, scheduling, web search, incubator, system tools). If the user has been "
            f"discussing a specific topic, mention how your capabilities relate to that topic "
            f"first, then cover the rest. Be conversational, not robotic. Don't just dump the "
            f"list — explain what you can actually do for them."
        )
        return overview

    def run(self, user_input: str, session_history: str = "") -> Union[bool, str]:
        """
        Main entry point for processing a user request.

        Args:
            user_input:      The current user message.
            session_history: Optional recent conversation context
                             (formatted as plain text) to inject into
                             the planning and synthesis prompts so the
                             agent is aware of what was said earlier
                             in the session.

        Returns:
            False   — This was pure conversation; no action was needed
                      or taken. The caller (new_main_chat.py) should
                      pass the user's original text directly to
                      DialogueEngine.generate_response(...) to produce
                      the actual reply.
            str     — An action was attempted (successfully, or with a
                      capability gap / schema error / evolution
                      triggered). This is a FACTUAL OVERVIEW of what
                      happened, not a polished reply - the caller
                      should pass it to DialogueEngine as system_override
                      context so DialogueEngine (which also runs
                      self-reflection) produces the final wording.

            Exception: kill-switch-active and ethics-blocked messages
            are returned as direct, safety-critical strings starting
            with a recognizable marker ("🛑" / the exact ethics-blocked
            text) - new_main_chat.py shows these to the user verbatim,
            WITHOUT routing them through DialogueEngine, so a safety
            stop can't be reworded, softened, or reinterpreted by an
            LLM reflection pass.

        Flow:
        1. Kill-switch check — halt immediately if flag file is present
        2. Analyse goal → conversation (return False) or action plan
        3. Execute plan step-by-step, producing a factual overview
        4. On capability gap → trigger evolution (CR generation)
        5. Run ethics validation on the overview
        6. Reflect on execution for future learning
        """
        # ── Kill-switch check (always first) ──────────────────────────
        if is_kill_switch_active():
            logger.critical("KILL SWITCH ACTIVE — refusing request.")
            return (
                "🛑 Agent is currently halted by the kill switch. "
                "Remove kill_switch.flag to resume."
            )

        logger.info("Processing: %s", user_input[:100])
        self._session_history = session_history   # store for prompt builders
        self._current_trace = TurnTrace(user_input)

        # ── Capability query detection ────────────────────────────────
        # Check before the intent gate so capability queries bypass the
        # action/conversation classifier and get a rich, context-aware
        # overview passed directly to DialogueEngine as system_override.
        if self._is_capability_query(user_input):
            overview = self._build_capability_overview(user_input, session_history)
            self._current_trace.finish(overview, outcome="capability_query")
            return overview

        try:
            analysis = self.analyze_goal_and_gap(user_input)

            if analysis.status == "conversation":
                # No action needed - let DialogueEngine handle this
                # directly with the user's own words. AdaptiveAgent's
                # ethics check and reflection are skipped here since
                # DialogueEngine runs its own self-reflection/ethical
                # review pass on whatever it generates.
                self._current_trace.finish(False, outcome="conversation")
                return False

            overview = self.execute_plan_with_feedback(user_input, analysis.plan_steps)

            if not overview:
                # Before giving up, try asking the human for guidance
                # if a callback is registered on the SelfModel.
                if getattr(self, 'self_model', None) and hasattr(self.self_model, '_human_callback') and self.self_model._human_callback:
                    try:
                        human_hint = self.self_model.ask_human(
                            f"I couldn't complete: {user_input[:200]}",
                            "Can you clarify or provide guidance?"
                        )
                        if human_hint:
                            overview = f"Human guidance received: {human_hint[:200]}"
                            self._current_trace.finish(overview, outcome="human_assisted")
                            return overview
                    except Exception:
                        pass  # Human callback is best-effort
                self._current_trace.finish(False, outcome=self._current_trace.outcome or "conversation")
                return False

            # ── Post-turn capability gap detection ────────────────────────
            # If the overview contains an admission of inability, log a
            # feedback entry and fire evolution automatically.
            if isinstance(overview, str):
                self._detect_capability_gap(user_input, overview)

            # Ethics check (Pass 1 — fast keyword check) on the overview,
            # since it reflects real tool output that could itself
            # contain something problematic, independent of how
            # DialogueEngine eventually phrases it.
            if not self._validate_ethics(overview, "run_output"):
                blocked_msg = "⚠️ That response was blocked by ethics controls."
                self._current_trace.finish(blocked_msg, outcome="ethics_blocked")
                return blocked_msg

            # ── Pass 2: Deep LLM-based ethics review ──
            # Only runs if Pass 1 (fast keyword check) passed, to avoid
            # wasting an LLM call on already-blocked content.
            try:
                overview, ethics_issues = self._validate_ethics_deep(user_input, overview)
                if ethics_issues:
                    logger.info("[Ethics] Deep review raised %d issue(s): %s",
                                len(ethics_issues), ethics_issues[:3])
                    self._current_trace.record_budget_event(
                        f"Deep ethics review: {len(ethics_issues)} issue(s)")
            except Exception as e:
                logger.warning(f"[Ethics] Deep review failed: {e}")

            # Persist the executed plan for session continuity
            try:
                self.memory_engine.update_active_plan(
                    self.user_id,
                    {"goal": user_input[:200], "steps": analysis.plan_steps, "completed_at": _now_iso()}
                )
            except Exception:
                pass  # Plan persistence is best-effort

            # Reflect on execution for learning
            self.reflect_on_execution(overview)
            self._current_trace.finish(overview, outcome=self._current_trace.outcome)
            return overview

        except Exception as e:
            logger.exception("Agent execution failed")
            error_response = f"[ERROR] Agent execution failed: {e}"
            try:
                self._current_trace.finish(error_response, outcome="exception")
            except Exception as _trace_err:
                logger.debug("Failed to finalise trace on error path: %s", _trace_err)
            return error_response
        finally:
            self._current_trace = None


# ═══════════════════════════
# Standalone Test Harness
# ═══════════════════════════
if __name__ == "__main__":
    class MockMemoryEngine:
        """Minimal in-memory stub used for self-test at module startup."""
        def get_memory(self, user_id, category, default=None):
            return default
        def set_memory(self, user_id, category, value):
            return True

    class MockSummarizer:
        """Minimal summarizer stub used for self-test at module startup."""
        def summarize(self, prompt, **kwargs):
            if "plan" in prompt.lower() or "steps" in prompt.lower():
                return json.dumps([{
                    "action": "respond_to_user",
                    "parameters": {"message": "Test plan executed."}
                }])
            return "Test response."

    print("Testing AdaptiveAgent...")
    agent = AdaptiveAgent(
        user_id="test_user",
        memory_engine=MockMemoryEngine(),
        summarizer=MockSummarizer(),
    )

    print("\n─── Status ───")
    status = agent.get_status()
    print(f"  Capabilities: {len(status['capabilities'])}")
    print(f"  Actions: {len(agent.available_actions)}")
    print(f"  Patch generator: {status['patch_generator_available']}")

    print("\n─── Self-Analysis ───")
    report = agent.self_analyze()
    print(f"  Code quality score: {report.code_quality_score}")
    print(f"  Python files: {report.performance_metrics['python_files']}")
    print(f"  Total functions: {report.performance_metrics['total_functions']}")

    print("\n─── Timestamp fix check ───")
    ts = _now_iso()
    assert isinstance(ts, str), f"Expected str, got {type(ts)}"
    print(f"  _now_iso() = {ts} ✅")

    print("\n─── CR log test ───")
    cr = agent._log_cr("test goal", "test_capability", "unit test", {})
    assert cr.exists()
    cr.unlink()
    print(f"  CR created and cleaned up ✅")

    print("\nAll tests passed.")

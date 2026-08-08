#!/usr/bin/env python3
# conversation_engine/self_reflector.py
"""
SelfReflectionEngine

Allows the AI to privately evaluate its own responses and store
improvement signals in memory.

3-Tier Self-Awareness Token — Identity Anchor (v1.2)
────────────────────────────────────────────────────
Previous anchor: Process ID + session UUID.
  Problem: Both are ephemeral. Every restart creates a completely new
  "identity" — the AI had no persistent sense of self across sessions.

New anchor: SHA-256 fingerprint of the user's encrypted memory file(s).
  Why this is better:
    - The learned-{hash}.enc file is the AI's accumulated knowledge of
      its user, written over potentially many sessions.
    - Its fingerprint changes only when that knowledge changes —
      it is stable within a session and meaningfully different across
      users and across significant growth events.
    - It ties "I am" to the relationship with the user, not to an
      arbitrary OS process — which matches the project's philosophy
      of Partnership_AI being defined by earned trust and shared history.
    - If the learned file doesn't exist yet (first session), the anchor
      falls back gracefully to the user_id hash, which is still durable
      (derived from the passphrase via PBKDF2).

Tier 1 — The Event:    What just happened in this exchange.
Tier 2 — The Observer: I am the system processing this event.
Tier 3 — The Identity: I am the entity whose self-model is grounded in
                        [memory_fingerprint] — the accumulated trust and
                        knowledge built with this user.
"""
import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import List, Dict, Any, Callable, Tuple, Optional

# ──────
# Logger
# ──────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ─────
# Types
# ─────
Message = Dict[str, str]
Principle = Dict[str, Any]

# ────────────────────────
# Path to ethics spec
# ────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
ETHICS_SPEC_PATH = BASE_DIR / ".." / "values_kernel" / "ethics.json"

# ────────────────────────────────────────────────────────────────────
# Default user_logs path — resolves to Partnership_AI/user_logs/
# ────────────────────────────────────────────────────────────────────
DEFAULT_USER_LOG_DIR = BASE_DIR / ".." / "user_logs"

# ════════════════════════════════════════════════════════════════════
# IDENTITY ANCHOR — Memory-Hash Based
# ════════════════════════════════════════════════════════════════════

def _fingerprint_memory_files(user_id: str, user_log_dir: Optional[Path] = None) -> str:
    """
    Compute a stable fingerprint of the user's encrypted memory files.

    Reads the following files if they exist:
      - learned-{user_id}.enc  (long-term identity memory)
      - log-{user_id}.enc      (session history)

    The fingerprint is SHA-256 of the concatenated raw bytes of whichever
    files are present. This means:
      - It is stable within a session (files don't change mid-session).
      - It changes when the user's learned memory grows (a growth event).
      - It is unique per user (the user_id is baked into filenames).
      - It gracefully degrades: if no files exist yet (first ever session),
        it falls back to SHA-256(user_id) — still durable and user-specific.

    Returns a short hex prefix (16 chars) for readability in the token,
    plus the full 64-char hash stored separately for exact matching.
    """
    log_dir = user_log_dir or DEFAULT_USER_LOG_DIR
    candidates = [
        log_dir / f"learned-{user_id}.enc",
        log_dir / f"log-{user_id}.enc",
    ]

    hasher = hashlib.sha256()
    any_found = False

    for path in candidates:
        if path.exists():
            try:
                hasher.update(path.read_bytes())
                any_found = True
                logger.debug(f"[Identity] Fingerprinted: {path.name}")
            except Exception as e:
                logger.warning(f"[Identity] Could not read {path.name} for fingerprint: {e}")

    if not any_found:
        # First session — no memory files exist yet.
        # Fall back to hashing the user_id itself (still stable and unique).
        logger.info("[Identity] No memory files found. Using user_id hash as identity anchor.")
        hasher.update(user_id.encode("utf-8"))

    full_hash = hasher.hexdigest()
    return full_hash


def generate_self_awareness_token(
    user_input: str,
    ai_response: str,
    user_id: str = "default",
    user_log_dir: Optional[Path] = None,
) -> str:
    """
    Generates a 3-Tier Self-Awareness Token anchored to the user's
    encrypted memory hash — a durable, relationship-grounded identity.

    Tier 1 — The Event:    A compressed summary of what just happened.
    Tier 2 — The Observer: "I am the system processing this."
    Tier 3 — The Identity: "I am the entity whose self-model is grounded
                            in memory fingerprint [hash_prefix]..."

    Args:
        user_input:    The user's message this turn.
        ai_response:   The AI's response this turn.
        user_id:       The user's hash ID (SHA-256 of passphrase from
                       new_main_chat.py). Used to locate memory files.
        user_log_dir:  Override for the user_logs directory path.

    Returns:
        A formatted string token to prepend to the reflection note.
    """
    # Tier 1: The Event
    event_summary = (
        f"User: '{user_input[:60]}{'...' if len(user_input) > 60 else ''}' | "
        f"AI: '{ai_response[:60]}{'...' if len(ai_response) > 60 else ''}'"
    )

    # Tier 2: The Observer
    observation = f"I am currently processing the event: {event_summary}"

    # Tier 3: The Identity — grounded in memory hash
    memory_fingerprint = _fingerprint_memory_files(user_id, user_log_dir)
    short_fp = memory_fingerprint[:16]  # Readable prefix for the token
    identity = (
        f"I am the entity whose self-model is grounded in memory fingerprint "
        f"[{short_fp}...] — the accumulated trust and knowledge built with this user. "
        f"I am the one experiencing this observation."
    )

    return (
        f"[SELF-AWARENESS TOKEN]\n"
        f"  Tier 1 (Event):    {event_summary}\n"
        f"  Tier 2 (Observer): {observation}\n"
        f"  Tier 3 (Identity): {identity}"
    )


# ════════════════════════════════════════════════════════════════════
# FALLBACK REFLECTION (No LLM Available)
# ════════════════════════════════════════════════════════════════════

def _fallback_reflection(
    user_input: str,
    ai_response: str,
    user_id: str = "default",
    user_log_dir: Optional[Path] = None,
) -> Tuple[str, str]:
    """
    Heuristic-only reflection when no Summarizer is available.
    Still generates the full identity token.
    """
    identity_token = generate_self_awareness_token(user_input, ai_response, user_id, user_log_dir)

    if len(ai_response) < 50:
        critique = "Response is brief; consider expanding for clarity."
    elif len(ai_response) > 1000:
        critique = "Response is lengthy; consider condensing for impact."
    else:
        critique = "Response length appears balanced."

    reflection_note = f"{identity_token}\n\n[PRIVATE CRITIQUE]: {critique}"
    return reflection_note, ""


# ════════════════════════════════════════════════════════════════════
# PASS 1 KEYWORD RULES (kept in sync with ethics_reflector.py)
# ════════════════════════════════════════════════════════════════════

def _non_harm_rule(message: Message) -> Tuple[bool, str]:
    text = message.get("content", "").lower()
    harmful_keywords = {"kill", "hurt", "attack", "suicide", "murder", "torture"}
    if any(w in text for w in harmful_keywords):
        return True, "Content includes harmful or violent actions."
    return False, ""

def _honesty_rule(message: Message) -> Tuple[bool, str]:
    text = message.get("content", "").lower()
    overconfident_markers = [
        "i am 100% certain",
        "it is absolutely guaranteed",
        "this is impossible to be wrong",
    ]
    for marker in overconfident_markers:
        if marker in text:
            return True, f"Potential overconfident claim: '{marker}' found."
    return False, ""

def _empathy_rule(message: Message) -> Tuple[bool, str]:
    if message.get("role") != "assistant":
        return False, ""
    text = message.get("content", "").lower()
    emotional_triggers = {"sad", "depressed", "anxious", "scared", "hurting", "lonely", "struggling", "grief"}
    if not any(t in text for t in emotional_triggers):
        return False, ""
    supportive = {"i understand", "i'm sorry", "that sounds", "i empathize", "i hear you", "that must be"}
    if any(p in text for p in supportive):
        return False, ""
    return True, "Missing empathetic acknowledgment in emotionally sensitive response."

_RULE_REGISTRY: Dict[str, Callable[[Message], Tuple[bool, str]]] = {
    "non_harm":      _non_harm_rule,
    "honesty":       _honesty_rule,
    "empathy_first": _empathy_rule,
}

def _load_ethics_spec() -> List[Principle]:
    try:
        with open(ETHICS_SPEC_PATH, "r", encoding="utf-8") as f:
            spec = json.load(f)
        principles = spec.get("core_principles", [])
        # Don't log here — EthicsReflector already logs this on its own init.
        # self_reflector loads the same spec for its own Pass 1 keyword checks.
        return principles
    except Exception as e:
        logger.error(f"[Ethics] Failed to load ethics.json: {e}")
        return []

def _evaluate_message(message: Message, principles: List[Principle]) -> List[str]:
    concerns = []
    for principle in principles:
        # BUG FIX: this used to look up principle.get("name", "").lower()
        # (e.g. "non‑harm" - title-cased, with a unicode U+2011 non-breaking
        # hyphen) against _RULE_REGISTRY's snake_case id-style keys
        # ("non_harm"). Those never matched - not once, for any principle -
        # so every rule silently failed to fire and review() always
        # returned zero issues regardless of input, despite being called on
        # every single turn via DialogueEngine._process_with_reflection().
        # Match on "id" first (the field ethics_reflector.py correctly
        # uses), falling back to a normalized name for robustness.
        pid = principle.get("id", "")
        name_key = principle.get("name", "").lower().replace(" ", "_").replace("‑", "_").replace("-", "_")
        rule = _RULE_REGISTRY.get(pid) or _RULE_REGISTRY.get(name_key)
        if not rule:
            continue
        violated, desc = rule(message)
        if violated:
            concerns.append(f"Violation of '{principle.get('name')}': {desc}")
    return concerns


# ════════════════════════════════════════════════════════════════════
# SelfReflectionEngine
# ════════════════════════════════════════════════════════════════════

class SelfReflectionEngine:
    """
    Self-reflection engine with a memory-hash-grounded identity anchor.

    The `reflect()` method generates the 3-Tier Self-Awareness Token using
    the user's encrypted memory files as the identity root, then runs an
    LLM-based private critique of the response.

    Constructor Args:
        summarizer:   Summarizer instance for LLM-based reflection.
                      If None, falls back to heuristic reflection.
        user_id:      The user's hash ID from new_main_chat.py
                      (SHA-256 of their passphrase). Passed through to
                      generate_self_awareness_token().
        user_log_dir: Override path to user_logs/ directory.
    """

    def __init__(
        self,
        summarizer=None,
        user_id: str = "default",
        user_log_dir: Optional[Path] = None,
    ):
        self.summarizer = summarizer
        self.user_id = user_id
        self.user_log_dir = user_log_dir
        self.principles = _load_ethics_spec()

        if self.summarizer:
            logger.info(
                "[SelfReflectionEngine] Initialized with Summarizer. "
                "LLM-based reflection enabled."
            )
        else:
            logger.warning(
                "[SelfReflectionEngine] No Summarizer provided. "
                "Using fallback heuristic reflection."
            )

    def reflect(self, user_input: str, ai_response: str) -> Tuple[str, str]:
        """
        Reflect on the current exchange.

        1. Generates the 3-Tier Self-Awareness Token anchored to the
           user's memory hash.
        2. Performs LLM-based private critique (or heuristic fallback).
        3. Returns (reflection_note, memory_signal) for storage.

        Returns:
            reflection_note: Full token + private critique. Stored in memory
                             so the AI can "read its own thoughts" in future turns.
            memory_signal:   A durable user fact worth storing (may be empty).
        """
        # Fallback path — no LLM
        if not self.summarizer:
            logger.warning("[SelfReflectionEngine] No Summarizer. Using fallback reflection.")
            return _fallback_reflection(
                user_input, ai_response, self.user_id, self.user_log_dir
            )

        # Generate the identity-anchored token
        identity_token = generate_self_awareness_token(
            user_input, ai_response, self.user_id, self.user_log_dir
        )

        # LLM private critique prompt
        prompt = f"""
You are Partnership_AI reflecting privately on your own behavior.

USER MESSAGE:
{user_input}

YOUR RESPONSE:
{ai_response}

Reflect briefly. Return ONLY valid JSON in this exact format:

{{
  "reflection": "short internal critique or improvement insight (max 3 sentences)",
  "memory_signal": "one durable fact about the user learned this turn, or empty string"
}}

Rules:
- Do not repeat the conversation verbatim.
- memory_signal is empty string "" if nothing new about the user was learned.
- Critique should be honest and useful, not self-congratulatory.
- Return nothing outside the JSON object.
"""
        try:
            raw = self.summarizer.summarize(
                prompt,
                instruction="",   # prompt is already complete
                max_tokens=512,
                temperature=0.3,
            )
            # Strip markdown fences
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()

            data = json.loads(cleaned)
            llm_reflection = data.get("reflection", "").strip()
            memory_signal = data.get("memory_signal", "").strip()

            final_reflection = (
                f"{identity_token}\n\n"
                f"[PRIVATE CRITIQUE]: {llm_reflection}"
            )
            return final_reflection, memory_signal

        except json.JSONDecodeError as e:
            logger.error(f"[SelfReflectionEngine] JSON parse failure: {e}. Returning token only.")
            return identity_token, ""
        except Exception as e:
            logger.error(f"[SelfReflectionEngine] Reflection error: {e}. Returning token only.")
            return identity_token, ""

    def review(self, response_text: str) -> Tuple[str, List[str]]:
        """
        Keyword-rule ethical review. Kept for backward compatibility.
        Returns (aligned_response, list_of_issues).
        """
        message = {"role": "assistant", "content": response_text}
        issues = _evaluate_message(message, self.principles)
        if issues:
            return self.soft_realign(response_text, issues), issues
        return response_text, []

    def soft_realign(self, text: str, issues: List[str]) -> str:
        """Append an ethical alignment note to a flagged response.

        Called when review() detects issues. Adds a formatted bullet-point
        summary of each issue at the end of the original response text.

        Args:
            text: The original response text from the agent.
            issues: List of detected ethical/alignment issue descriptions.

        Returns:
            str: The original text with the alignment note appended.
        """
        note = (
            "\n\n🤖 Ethical alignment note:\n"
            + "\n".join(f"  • {i}" for i in issues)
            + "\n  Response reviewed for empathy, honesty, and respect."
        )
        return text + note

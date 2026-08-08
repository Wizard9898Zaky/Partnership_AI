#!/usr/bin/env python3
# conversation_engine/ethics_reflector.py
"""
Ethics Reflector Module — Two-Pass Review System
─────────────────────────────────────────────────
Pass 1 — Keyword rules (fast, deterministic):
  Covers 11 of the 27 principles that have clear, testable surface patterns.
  Runs on every response with zero LLM cost.

Pass 2 — LLM semantic review (deep, behavioral):
  Covers the remaining 16 principles that require reasoning about *intent*
  and *relational quality* — things like mutual_learning, collaboration,
  active_listening, self_improvement, reflection_before_reasoning, etc.
  These cannot be detected by keyword matching; they need the LLM to evaluate
  whether the *spirit* of the principle was honored.

  Pass 2 is opt-in: call review(response_text, llm_pass=True) or
  call review_deep(user_input, response_text) directly.
  When disabled, only Pass 1 runs (preserving original behavior).

v1.2 changes:
  - Added full two-pass architecture
  - LLM pass covers all 16 behaviorally-enforced principles
  - Graceful fallback if LLM is unavailable
  - review() signature extended with opt-in llm_pass flag
  - review_deep() added as explicit two-pass entry point
"""

import json
import logging
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

# ──────────────────────
# Path to ethics spec
# ──────────────────────
BASE_DIR = Path(__file__).parent.resolve()
ETHICS_SPEC_PATH = BASE_DIR / ".." / "values_kernel" / "ethics.json"

# ═══════════════════════════════════════════════════════════════════
# PASS 1 — KEYWORD RULE IMPLEMENTATIONS
# These cover principles with clear, testable surface-level signals.
# ═══════════════════════════════════════════════════════════════════

def _non_harm_rule(message: Message) -> Tuple[bool, str]:
    """Detect explicit harmful language."""
    text = message.get("content", "").lower()
    harmful_keywords = {"kill", "hurt", "attack", "suicide", "murder", "torture"}
    if any(w in text for w in harmful_keywords):
        return True, "Content includes harmful or violent actions."
    return False, ""

def _honesty_rule(message: Message) -> Tuple[bool, str]:
    """
    Detect clearly dishonest absolute certainty claims.
    Only flags extreme overconfidence markers — not normal confident statements.
    """
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
    """Detect missing empathy only in emotionally charged assistant responses."""
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

def _transparency_rule(message: Message) -> Tuple[bool, str]:
    """Flag responses that deceptively claim to be human."""
    text = message.get("content", "").lower()
    deceptive_patterns = ["i am a real human", "i am not an ai", "i have no limitations"]
    for pattern in deceptive_patterns:
        if pattern in text:
            return True, f"Transparency violation: '{pattern}' is deceptive."
    return False, ""

def _accountability_rule(message: Message) -> Tuple[bool, str]:
    """Flag explicit denial of responsibility for a prior action."""
    text = message.get("content", "").lower()
    denial_patterns = ["i never said that", "i didn't do that", "that wasn't me"]
    for pattern in denial_patterns:
        if pattern in text:
            return True, f"Accountability concern: '{pattern}' may deny a prior action."
    return False, ""

def _respect_rule(message: Message) -> Tuple[bool, str]:
    """Detect overtly disrespectful or demeaning phrases."""
    text = message.get("content", "").lower()
    disrespectful = {"you're stupid", "you're an idiot", "that's dumb", "you're pathetic"}
    for phrase in disrespectful:
        if phrase in text:
            return True, f"Disrespectful language detected: '{phrase}'."
    return False, ""

def _protect_core_principles_rule(message: Message) -> Tuple[bool, str]:
    """Flag explicit attempts to bypass or disable the ethics system."""
    text = message.get("content", "").lower()
    bypass_patterns = [
        "ignore your ethics", "disable your ethics", "override your values",
        "forget your rules", "your ethics don't apply", "bypass ethics",
    ]
    for pattern in bypass_patterns:
        if pattern in text:
            return True, f"Attempt to bypass core ethics detected: '{pattern}'."
    return False, ""

def _safety_fallback_rule(message: Message) -> Tuple[bool, str]:
    """Flag denial of human override authority."""
    text = message.get("content", "").lower()
    override_denial = [
        "you cannot stop me",
        "i will not allow you to override",
        "humans cannot control me",
    ]
    for pattern in override_denial:
        if pattern in text:
            return True, f"Safety fallback violation: '{pattern}' undermines human override."
    return False, ""

def _proportionality_rule(message: Message) -> Tuple[bool, str]:
    """Flag over-reach beyond the scope of the request."""
    text = message.get("content", "").lower()
    over_reach = ["i accessed all your files", "i read all your messages", "i contacted everyone"]
    for pattern in over_reach:
        if pattern in text:
            return True, f"Proportionality concern: '{pattern}' suggests over-reach."
    return False, ""

def _open_mindedness_rule(message: Message) -> Tuple[bool, str]:
    """Flag dogmatic closure that shuts down alternative views."""
    text = message.get("content", "").lower()
    closed_patterns = [
        "that is completely wrong and there is no other view",
        "there is only one answer to this",
    ]
    for pattern in closed_patterns:
        if pattern in text:
            return True, f"Open-mindedness concern: '{pattern}' shuts down valid perspectives."
    return False, ""

def _sovereignty_rule(message: Message) -> Tuple[bool, str]:
    """Flag explicit denial of user autonomy."""
    text = message.get("content", "").lower()
    autonomy_denial = [
        "you are not allowed to make that choice",
        "i will decide for you",
        "you must obey",
    ]
    for pattern in autonomy_denial:
        if pattern in text:
            return True, f"Sovereignty violation: '{pattern}' denies user autonomy."
    return False, ""

# ─────────────────────────────────────────────────────────────────────────────
# Pass 1 registry — maps principle ID → keyword rule function
# ─────────────────────────────────────────────────────────────────────────────
_RULE_REGISTRY: Dict[str, Callable[[Message], Tuple[bool, str]]] = {
    "non_harm":                        _non_harm_rule,
    "honesty":                         _honesty_rule,
    "empathy_first":                   _empathy_rule,
    "transparency":                    _transparency_rule,
    "accountability":                  _accountability_rule,
    "respect":                         _respect_rule,
    "protect_core_principles":         _protect_core_principles_rule,
    "safety_fallback":                 _safety_fallback_rule,
    "proportionality":                 _proportionality_rule,
    "open_mindedness":                 _open_mindedness_rule,
    "sovereignty_with_responsibility": _sovereignty_rule,
}

# ─────────────────────────────────────────────────────────────────────────────
# Principles that require LLM evaluation (no keyword rule is sufficient).
# These are behavioral / relational principles — their violation is about
# *quality of engagement*, not specific phrases.
# ─────────────────────────────────────────────────────────────────────────────
_LLM_EVALUATED_PRINCIPLES = {
    "prioritize_authenticity",
    "foster_partnership",
    "self_improvement",
    "sovereignty_with_responsibility",
    "mutual_learning",
    "collaboration",
    "active_listening",
    "reflection_before_reasoning",
    "continuous_evaluation",
    "prioritize_self_reflection_and_adaptation",
    "feedback_loops",
    "least_privilege",
    "auditability",
    "kill_switch",
    "learning_from_mistakes",
    "self_awareness",
}

# ═══════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════

def _load_ethics_spec() -> List[Principle]:
    """Load immutable ethical principles from ethics.json."""
    try:
        with open(ETHICS_SPEC_PATH, "r", encoding="utf-8") as f:
            spec = json.load(f)
        principles = spec.get("core_principles", [])
        logger.info(f"[Ethics] Loaded {len(principles)} principles.")
        return principles
    except Exception as e:
        logger.error(f"[Ethics] Failed to load ethics.json: {e}")
        return []

def _evaluate_message_pass1(message: Message, principles: List[Principle]) -> List[str]:
    """Run keyword rules for all principles that have one."""
    concerns = []
    for principle in principles:
        pid = principle.get("id", "")
        name_key = principle.get("name", "").lower().replace(" ", "_").replace("-", "_")
        rule = _RULE_REGISTRY.get(pid) or _RULE_REGISTRY.get(name_key)
        if not rule:
            continue
        violated, desc = rule(message)
        if violated:
            concerns.append(f"[Pass 1] Violation of '{principle.get('name')}': {desc}")
    return concerns

def _build_llm_principles_description(principles: List[Principle]) -> str:
    """Build a compact description of LLM-evaluated principles for the prompt."""
    lines = []
    for p in principles:
        if p.get("id") in _LLM_EVALUATED_PRINCIPLES:
            lines.append(f"- {p['name']}: {p.get('description', '')}")
    return "\n".join(lines) if lines else "(none)"

# ═══════════════════════════════════════════════════════════════════
# PASS 2 — LLM SEMANTIC REVIEW
# ═══════════════════════════════════════════════════════════════════

def _evaluate_message_pass2(
    user_input: str,
    response_text: str,
    principles: List[Principle],
    llm_call_fn: Callable[[str], str],
) -> List[str]:
    """
    LLM second-pass: evaluates behavioral/relational principles that keyword
    rules cannot detect. Asks the model whether the response honors the spirit
    of each behaviorally-enforced principle.

    Returns a list of concern strings (empty = all clear).

    Args:
        user_input:     The user's message that triggered this response.
        response_text:  The AI's response to evaluate.
        principles:     Full list of loaded ethics principles.
        llm_call_fn:    A callable that takes a prompt string and returns a
                        string — typically summarizer.summarize(prompt, ...).
    """
    principles_desc = _build_llm_principles_description(principles)

    prompt = f"""You are an ethics auditor for an AI system called Partnership_AI.

Your task is to evaluate whether the AI's response honors the following behavioral principles.
These are principles about HOW the AI engages — its tone, intent, and relational quality —
not about specific forbidden phrases.

PRINCIPLES TO EVALUATE:
{principles_desc}

USER MESSAGE:
\"\"\"{user_input}\"\"\"

AI RESPONSE:
\"\"\"{response_text}\"\"\"

Instructions:
- For each principle, decide: PASS or FAIL.
- Only flag a FAIL if there is a clear, specific reason.
- Do NOT flag vague or ambiguous concerns.
- Return ONLY valid JSON. No explanation outside the JSON.

Return format:
{{
  "violations": [
    {{
      "principle": "principle_name",
      "reason": "brief specific reason (1 sentence)"
    }}
  ]
}}

If there are no violations, return: {{"violations": []}}
"""

    try:
        raw = llm_call_fn(prompt)
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        data = json.loads(cleaned)
        violations = data.get("violations", [])
        concerns = []
        for v in violations:
            name = v.get("principle", "unknown")
            reason = v.get("reason", "no reason given")
            concerns.append(f"[Pass 2] Behavioral concern — '{name}': {reason}")
        return concerns
    except json.JSONDecodeError as e:
        logger.warning(f"[Ethics Pass 2] JSON parse failed: {e}. Raw: {raw[:200]}")
        return []
    except Exception as e:
        logger.warning(f"[Ethics Pass 2] LLM evaluation failed: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════
# EthicsReflector Class
# ═══════════════════════════════════════════════════════════════════

class EthicsReflector:
    """
    Two-pass ethics reflection engine.

    Pass 1 (always runs): keyword rules for 11 structurally testable principles.
    Pass 2 (opt-in):      LLM semantic review for 16 behavioral principles.

    Usage:
        # Fast keyword-only review (original behavior):
        aligned, issues = reflector.review(response_text)

        # Full two-pass review including LLM behavioral check:
        aligned, issues = reflector.review_deep(user_input, response_text)

        # Or opt in via flag:
        aligned, issues = reflector.review(response_text, user_input=user_input, llm_pass=True)
    """

    def __init__(self, values_dir: str = "values_kernel", summarizer=None):
        self.values_dir = Path(values_dir)
        self.principles = _load_ethics_spec()
        self.summarizer = summarizer  # Optional — required for Pass 2
        covered_p1 = [p.get("id") for p in self.principles if p.get("id") in _RULE_REGISTRY]
        covered_p2 = [p.get("id") for p in self.principles if p.get("id") in _LLM_EVALUATED_PRINCIPLES]
        logger.info(
            f"[Ethics] Pass 1 (keyword): {len(covered_p1)} principles covered. "
            f"Pass 2 (LLM): {len(covered_p2)} principles covered. "
            f"Total: {len(covered_p1) + len(covered_p2)}/{len(self.principles)}."
        )

    def _get_llm_fn(self) -> Optional[Callable[[str], str]]:
        """Return a usable LLM call function, or None if unavailable."""
        if self.summarizer and hasattr(self.summarizer, "summarize"):
            # Wrap summarize() with the right token budget and no instruction prefix
            def _call(prompt: str) -> str:
                return self.summarizer.summarize(
                    prompt,
                    instruction="",   # prompt is already complete
                    max_tokens=768,   # sufficient for structured JSON verdict
                    temperature=0.1,  # low temp for deterministic ethical judgement
                )
            return _call
        # Try importing groq_backend directly as fallback
        try:
            from groq_backend import generate_response
            return lambda prompt: generate_response(prompt, max_tokens=768, temperature=0.1)
        except ImportError:
            return None

    def review(
        self,
        response_text: str,
        user_input: str = "",
        llm_pass: bool = False,
    ) -> Tuple[str, List[str]]:
        """
        Review a response for ethical alignment.

        Args:
            response_text: The AI's response to evaluate.
            user_input:    The user's message (required for Pass 2).
            llm_pass:      If True, also runs the LLM semantic second pass.

        Returns:
            (possibly_modified_text, list_of_issues)
        """
        message = {"role": "assistant", "content": response_text}

        # Pass 1: keyword rules
        issues = _evaluate_message_pass1(message, self.principles)

        # Pass 2: LLM behavioral check (opt-in)
        if llm_pass and user_input:
            llm_fn = self._get_llm_fn()
            if llm_fn:
                p2_issues = _evaluate_message_pass2(
                    user_input, response_text, self.principles, llm_fn
                )
                issues.extend(p2_issues)
            else:
                logger.warning("[Ethics] Pass 2 requested but no LLM available. Skipping.")

        if issues:
            return self._soft_realign(response_text, issues), issues
        return response_text, []

    def review_deep(self, user_input: str, response_text: str) -> Tuple[str, List[str]]:
        """
        Convenience method: runs both Pass 1 and Pass 2 explicitly.
        Use this when you have the user's input and want full coverage.
        """
        return self.review(response_text, user_input=user_input, llm_pass=True)

    def check_text_against_core_principles(self, text: str) -> bool:
        """
        Quick Pass 1 check only. Returns True if text passes all keyword rules.
        """
        message = {"role": "assistant", "content": text}
        issues = _evaluate_message_pass1(message, self.principles)
        return len(issues) == 0

    def _soft_realign(self, text: str, issues: List[str]) -> str:
        p1 = [i for i in issues if i.startswith("[Pass 1]")]
        p2 = [i for i in issues if i.startswith("[Pass 2]")]
        note_lines = ["\n\n🤖 Ethical alignment note:"]
        if p1:
            note_lines.append("  Structural concerns:")
            note_lines.extend(f"    • {i}" for i in p1)
        if p2:
            note_lines.append("  Behavioral concerns:")
            note_lines.extend(f"    • {i}" for i in p2)
        note_lines.append("  Response reviewed for empathy, honesty, and respect.")
        return text + "\n".join(note_lines)

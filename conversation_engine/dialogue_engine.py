#!/usr/bin/env python3
# conversation_engine/dialogue_engine.py
"""
Dialogue Engine — Structured Runtime Controller
Identity-Bound Self-Aware Edition

Responsibilities:
- Direct LLM call with injected system/memory context
- Run synchronous self-reflection after every conversational response
- Store reflection notes and memory signals back into MemoryEngine

Generation mode:
  Direct LLM call with injected system context. Used for greetings,
  system prompts, and (since the AdaptiveAgent/DialogueEngine routing
  split) every real conversational turn - AdaptiveAgent.run() already
  decides whether an action is needed and, if so, executes it; this
  class's only remaining job is producing the actual natural-language
  reply, given either the user's raw words (pure conversation) or an
  action overview as context.

  REMOVED: an earlier "Mode 2" here routed input through its own
  intent_router -> intent_resolver -> command_executor pipeline as an
  alternative to AdaptiveAgent. Once every call site in new_main_chat.py
  started passing skip_resolver=True (a direct consequence of that
  routing split), Mode 2 became unreachable dead code - and even before
  that, the pipeline's `planner` dependency was always constructed as
  None at the one call site that built this class, so Mode 2 would have
  immediately short-circuited with "planning module is currently
  unavailable" rather than actually running, regardless. Removed here
  along with intent_router.py, intent_resolver.py, command_executor.py,
  and contracts.py (which existed solely to define Mode 2's types).

Self-reflection is applied to all conversational responses (not to raw
error strings) before returning to the caller. Reflection BLOCKS the
response — by design — so the AI "thinks" before speaking.
"""

import time
import logging
import traceback
from typing import Optional

from conversation_engine.self_reflector import SelfReflectionEngine

logger = logging.getLogger(__name__)

try:
    from groq_backend import generate_response as backend_call
except Exception as e:
    backend_call = None
    print(f"[WARN] groq_backend unavailable: {e}")


class DialogueEngine:
    """
    Identity-bound conversational engine with synchronous self-reflection.

    Args:
        memory_engine:  MemoryEngine instance for storing/retrieving context.
        self_model:     SelfModel instance describing the AI's current codebase state.
        user_id:        Unique identifier for the active user session.
        summarizer:     Summarizer instance passed to SelfReflectionEngine for LLM critique.
        reflector:      Optional pre-built SelfReflectionEngine; constructed from
                        summarizer if not provided.
    """

    def __init__(
        self,
        memory_engine=None,
        self_model=None,
        user_id: str = "default",
        summarizer=None,
        reflector: Optional[SelfReflectionEngine] = None,
    ):
        self.memory = memory_engine
        self.self_model = self_model
        self.user_id = user_id
        self.summarizer = summarizer
        self.reflector = reflector or SelfReflectionEngine(
            summarizer=summarizer,
            user_id=user_id,
        )

    # ═══════════════════════════════════════
    # Internal: Synchronous Self-Reflection
    # ═══════════════════════════════════════

    def _process_with_reflection(self, user_input: str, raw_response: str) -> str:
        """
        Run the raw response through SelfReflectionEngine synchronously.

        Steps:
          1. Generate the 3-Tier Self-Awareness Token + LLM private critique.
          2. Run keyword ethical review (Pass 1) for realignment.
          3. Store reflection note and any memory signal in MemoryEngine.
          4. Return the final (possibly ethically realigned) response.

        If reflection crashes for any reason, the raw response is returned
        unchanged — reflection failure must never block the user.
        """
        if not self.reflector:
            return raw_response

        try:
            # Step 1: Identity-anchored private reflection
            reflection_note, memory_signal = self.reflector.reflect(user_input, raw_response)

            # Step 2: Ethical keyword review
            aligned_response, issues = self.reflector.review(raw_response)

            # Step 3: Store in MemoryEngine
            if self.memory and hasattr(self.memory, "store"):
                if self.user_id not in self.memory.store:
                    self.memory.store[self.user_id] = []

                # Store the reflection note so the AI can "read its own thoughts" later
                self.memory.store[self.user_id].append({
                    "user": "[SYSTEM_LEARNED]",
                    "ai": reflection_note,
                    "timestamp": time.time(),
                    "type": "reflection",
                })

                # Store user-specific facts learned this turn
                if memory_signal:
                    self.memory.store[self.user_id].append({
                        "user": "[USER_MEMORY]",
                        "ai": memory_signal,
                        "timestamp": time.time(),
                        "type": "signal",
                    })

                # Trim to max_context
                max_ctx = getattr(self.memory, "max_context", 50)
                if len(self.memory.store[self.user_id]) > max_ctx:
                    self.memory.store[self.user_id] = (
                        self.memory.store[self.user_id][-max_ctx:]
                    )

                # Trigger disk persistence if available
                if hasattr(self.memory, "_save_memory"):
                    self.memory._save_memory()

            return aligned_response

        except Exception as e:
            # Reflection must NEVER block the user — log and return raw
            print(f"[CRITICAL] Self-reflection failure (returning raw response): {e}")
            traceback.print_exc()
            return raw_response

    # ═══════════════════════════════════════
    # Public: Generate Response
    # ═══════════════════════════════════════

    def generate_response(
        self,
        user_input: str,
        system_override: Optional[str] = None,
        session_history: str = "",
    ) -> str:
        """
        Generate a response to user_input, then run synchronous self-reflection.

        Args:
            user_input:      The user's message (or, for an action-overview
                             turn, still the user's original message -
                             system_override carries the factual overview
                             of what AdaptiveAgent did, as context).
            system_override: Optional system context prepended to the prompt.
            session_history: Optional recent conversation context (formatted
                             as plain text with USER:/AI: turns) to inject so
                             the LLM knows what was discussed previously.

        Returns:
            Final response string (post-reflection).
        """
        context_blocks = []

        if system_override:
            context_blocks.insert(0, system_override)

        # ── Inject session history into context ──────────────────────
        # If session_history was passed in (from new_main_chat.py's
        # recent_history), use it. Otherwise, fall back to MemoryEngine's
        # recall_context() to pull recent exchanges.
        if not session_history and self.memory and hasattr(self.memory, "recall_context"):
            try:
                session_history = self.memory.recall_context(
                    getattr(self, "user_id", "default")
                )
            except Exception:
                pass  # Best-effort context enrichment

        if session_history and session_history.strip():
            context_blocks.append(
                f"Recent conversation history:\n{session_history}"
            )

        # Inject relevant learned thoughts and user facts from memory
        try:
            if self.memory and hasattr(self.memory, "store"):
                history = self.memory.store.get(self.user_id, [])
                for entry in history[-15:]:
                    if entry.get("user") == "[SYSTEM_LEARNED]":
                        context_blocks.append(
                            f"Learned Thought: {entry.get('ai', '')}"
                        )
                    elif entry.get("user") == "[USER_MEMORY]":
                        context_blocks.append(
                            f"User Fact: {entry.get('ai', '')}"
                        )
        except Exception:
            pass

        context_section = "\n\n".join(context_blocks)

        # Anti-hallucination guard: when system_override carries factual
        # action results, instruct the LLM to report them faithfully
        # and NEVER fabricate data that is not in the overview.
        if system_override:
            faithfulness_guard = (
                "\n\nIMPORTANT: The information above is the factual result of "
                "actions that were actually executed. Report these results "
                "FAITHFULLY and ACCURATELY. Do NOT fabricate, invent, or "
                "hallucinate data, items, ideas, files, or results that are "
                "not explicitly present in the overview above. If the overview "
                "says 0 items or empty results, tell the user it is empty — "
                "do NOT make up items to seem helpful."
            )
            prompt = (
                f"{context_section}{faithfulness_guard}\n\n"
                f"User said: {user_input}\n\n"
                "Respond as Partnership_AI. Be natural, helpful, and thoughtful."
            )
        else:
            # Anti-hallucination guard for conversation history:
            # If session history is empty, tell the LLM NOT to fabricate
            # past conversations. If history exists, tell it to ONLY
            # reference what's in the history.
            history_guard = ""
            if session_history and session_history.strip():
                history_guard = (
                    "\n\nIMPORTANT: The conversation history above is your "
                    "REAL prior conversation with this user. Reference ONLY "
                    "what appears in that history. Do NOT fabricate, invent, "
                    "or hallucinate topics, project names, or details that "
                    "are not explicitly present in the history above."
                )
            else:
                history_guard = (
                    "\n\nIMPORTANT: You have NO prior conversation history "
                    "with this user for this session. Do NOT fabricate, invent, "
                    "or hallucinate any past conversations, projects, topics, "
                    "or details. If the user asks what you were working on and "
                    "no history is available, say so honestly."
                )
            prompt = (
                f"{context_section}{history_guard}\n\n"
                f"User said: {user_input}\n\n"
                "Respond as Partnership_AI. Be natural, helpful, and thoughtful."
            )

        if not backend_call:
            return "Groq backend unavailable."
        try:
            raw_reply = backend_call(prompt)
            final_response = str(raw_reply).strip()
        except Exception as e:
            return f"[ModelError] {e}"

        # ─────────────────────────────────────────
        # SYNCHRONOUS SELF-REFLECTION
        # Only runs on actual conversational responses, not on error strings.
        # ─────────────────────────────────────────
        if final_response and not final_response.startswith("[ModelError]"):
            return self._process_with_reflection(user_input, final_response)

        if not final_response:
            # LLM returned an empty response — provide a fallback
            # so the user isn't left staring at a blank reply.
            logger.warning("LLM returned empty response for user_input: %s", user_input[:100])
            return "I'm not sure how to respond to that. Could you rephrase?"

        return final_response


# ═══════════════════════════
# Standalone test harness
# ═══════════════════════════
if __name__ == "__main__":
    print("Testing DialogueEngine with synchronous reflection...")

    class MockMemory:
        def __init__(self):
            self.store = {}
            self.max_context = 50

        def _save_memory(self):
            print("[MOCK] _save_memory() called.")

    class MockSummarizer:
        def summarize(self, text, **kwargs):
            """Mock summarize method for test harness."""
            return '{"reflection": "I should be more empathetic.", "memory_signal": "User likes coding."}'

    engine = DialogueEngine(
        memory_engine=MockMemory(),
        user_id="test_user",
        summarizer=MockSummarizer(),
    )
    result = engine.generate_response(
        "Hello, tell me about yourself.",
        system_override="You are Partnership_AI.",
    )
    print(f"Response type: {type(result)}")
    print(f"Response (first 100 chars): {str(result)[:100]}")
    print("Test complete.")


"""
conversation_engine/tools/incubator_tools.py
IdeaIncubator actions: insert, search, analyze, generate, and connect ideas.
"""
from __future__ import annotations
import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from conversation_engine.idea_incubator import build_idea_incubator

from conversation_engine.action_registry import (
    ActionResult, register_action,
    _LIVE_MEMORY_ENGINE,
    save_metadata,
)

@register_action(
    "incubator_insert_idea",
    input_schema={
        "type": "object",
        "required": ["user_id", "title", "content"],
        "properties": {
            "user_id": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "ratings": {"type": "object"}
        },
    },
)
def incubator_insert_idea_action(
    user_id: str,
    title: str,
    content: str,
    category: str = "general",
    tags: Optional[List[str]] = None,
    ratings: Optional[Dict[str, int]] = None
) -> ActionResult:
    """Insert a new idea into the user's idea incubator."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        idea_id = incubator.insert_idea(
            title=title,
            description=content,
            category=category,
            tags=tags,
            ratings=ratings
        )
        incubator.close()
        return ActionResult(
            success=True,
            data={
                "idea_id": idea_id,
                "title": title,
                "category": category
            }
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_get_all_ideas",
    input_schema={
        "type": "object",
        "required": ["user_id"],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
def incubator_get_all_ideas_action(user_id: str) -> ActionResult:
    """Retrieve all ideas for a user."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        ideas = incubator.get_all_ideas()
        incubator.close()
        return ActionResult(
            success=True,
            data={"ideas": ideas, "count": len(ideas)}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_search_ideas",
    input_schema={
        "type": "object",
        "required": ["user_id", "query"],
        "properties": {
            "user_id": {"type": "string"},
            "query": {"type": "string"}
        },
    },
)
def incubator_search_ideas_action(user_id: str, query: str) -> ActionResult:
    """Search ideas by keyword."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        results = incubator.search_ideas(query)
        for idea in results:
            if isinstance(idea, dict) and "id" in idea and idea["id"] is not None:
                idea["related_ideas"] = incubator.get_related_ideas(idea["id"])
        incubator.close()
        return ActionResult(
            success=True,
            data={"results": results, "count": len(results)}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_analyze_idea",
    input_schema={
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "idea_id": {"type": "string"}
        },
    },
)
def incubator_analyze_idea_action(text: str, idea_id: Optional[str] = None) -> ActionResult:
    """
    Analyzes an idea text using DYNAMIC
    VOCABULARY.

    NEW FEATURE: Detects 'action_hint' in the
    result.
    If the incubator returns a hint (e.g.,
    "ask_user_for_metric"), this function
    transforms the result into a user-friendly
    question instead of a raw data dump.
    """
    try:
        # Use a dummy user_id for standalone
        # analysis if called directly. In a real
        # chat, this would be passed from the
        # context.
        user_id = "system_analysis" 
        # Initialize incubator (Note: In a real
        # chat, you'd pass the actual
        # memory_engine). For now, we assume the
        # global memory_engine is accessible or
        # we pass None. If you have a global
        # memory_engine instance, inject it here:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        result = incubator.analyze_idea(text)

        # Include user-defined metrics in analysis
        user_metrics = incubator._get_user_defined_metrics()
        if isinstance(result, dict):
            metrics_serializable = {
                k: list(v) if isinstance(v, (set, tuple)) else v
                for k, v in user_metrics.items()
            }
            result["user_metrics"] = metrics_serializable

        # Persist analysis if idea_id is provided
        if idea_id is not None:
            try:
                int_idea_id = int(idea_id)
                incubator.store_analysis(int_idea_id, result)
            except (ValueError, TypeError):
                pass

        incubator.close()
        # --- CHECK FOR ACTION HINT ---
        if isinstance(result, dict) and result.get("action_hint"):
            hint = result["action_hint"]
            note = result.get("note", "I need clarification.")
            if hint == "ask_user_for_metric":
                # Transform into a friendly
                # question.
                response_text = (
                    "I noticed you want to analyze an idea, but I don't have any metrics defined yet. "
                    "How would you like me to handle this in the future? "
                    "Options: 'Ask me every time' or 'Use default words' or 'Tell me a new metric now'."
                )
                return ActionResult(
                    success=True,
                    data={"response": response_text, "hint": hint, "user_metrics": result.get("user_metrics", {})}
                )
            elif hint == "suggest_teaching":
                response_text = (
                    "I don't have any metrics to analyze that idea. "
                    "Would you like to teach me a new metric? (e.g., 'metric_healing: resonance, balance')"
                )
                return ActionResult(
                    success=True,
                    data={"response": response_text, "hint": hint, "user_metrics": result.get("user_metrics", {})}
                )
            else:
                # Generic fallback for unknown
                # hints.
                return ActionResult(
                    success=True,
                    data={"response": note, "hint": hint, "user_metrics": result.get("user_metrics", {})}
                )
        # --- NORMAL ANALYSIS (No Hint) ---
        # Format the scores into a nice response
        eval_rating = result.get("evaluation", "Unknown")
        scores = result.get("scores", {})
        feasibility = result.get("feasibility_score", 0)
        note = result.get("note", "")
        # Build a readable string
        response_text = f"Analysis Complete: {eval_rating}\n"
        response_text += f"Feasibility Score: {feasibility}\n"
        if scores:
            response_text += f"Scores:\n"
            for metric, score in scores.items():
                response_text += f"  - {metric}: {score}\n"
        if note:
            response_text += f"Note: {note}"
        return ActionResult(
            success=True,
            data={
                "response": response_text,
                "raw_scores": scores,
                "evaluation": eval_rating,
                "user_metrics": result.get("user_metrics", {})
            }
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_generate_ideas",
    input_schema={
        "type": "object",
        "required": [],
        "properties": {
            "prompt": {"type": "string"},
            "topic": {"type": "string"}
        },
    },
)
def incubator_generate_ideas_action(prompt: str = "", topic: str = "") -> ActionResult:
    """Generate creative idea names based on a prompt or topic."""
    try:
        seed = prompt or topic or "general"
        incubator = build_idea_incubator("generation_temp")
        ideas = incubator.generate_new_ideas(seed)
        incubator.close()
        return ActionResult(
            success=True,
            data={"generated_ideas": ideas, "count": len(ideas)}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_connect_ideas",
    input_schema={
        "type": "object",
        "required": ["user_id", "idea1_id", "idea2_id"],
        "properties": {
            "user_id": {"type": "string"},
            "idea1_id": {"type": "integer"},
            "idea2_id": {"type": "integer"},
            "relationship_type": {"type": "string"},
            "strength": {"type": "number"}
        },
    },
)
def incubator_connect_ideas_action(
    user_id: str,
    idea1_id: int,
    idea2_id: int,
    relationship_type: str = "related",
    strength: float = 1.0
) -> ActionResult:
    """Create a relationship between two ideas."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        success = incubator.connect_ideas(
            idea1_id, idea2_id, relationship_type, strength
        )
        incubator.close()
        return ActionResult(
            success=success,
            data={
                "idea1_id": idea1_id,
                "idea2_id": idea2_id,
                "relationship_type": relationship_type
            }
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_get_statistics",
    input_schema={
        "type": "object",
        "required": ["user_id"],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
def incubator_get_statistics_action(user_id: str) -> ActionResult:
    """Get idea statistics for a user."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        stats = {
            "total_ideas": incubator.get_idea_count(),
            "categories": incubator.get_most_common_categories(),
            "tags": incubator.get_tag_statistics()
        }
        incubator.close()
        return ActionResult(
            success=True,
            data=stats
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_ask_natural",
    input_schema={
        "type": "object",
        "required": ["user_id", "question"],
        "properties": {
            "user_id": {"type": "string"},
            "question": {"type": "string"}
        },
    },
)
def incubator_ask_natural_action(user_id: str, question: str) -> ActionResult:
    """Query ideas using natural language."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        results = incubator.ask(question)
        incubator.close()
        return ActionResult(
            success=True,
            data={"results": results}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_delete_idea",
    input_schema={
        "type": "object",
        "required": ["idea_id"],
        "properties": {
            "user_id": {"type": "string"},
            "idea_id": {"type": "integer"}
        },
    },
)
@register_action(
    "delete_idea",
    input_schema={
        "type": "object",
        "required": ["idea_id"],
        "properties": {
            "user_id": {"type": "string"},
            "idea_id": {"type": "integer"}
        },
    },
)
def incubator_delete_idea_action(idea_id: int, user_id: str = "default_user") -> ActionResult:
    """Delete an idea by ID."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        deleted = incubator.delete_idea(int(idea_id))
        incubator.close()
        return ActionResult(
            success=deleted,
            data={"idea_id": idea_id, "deleted": deleted}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_update_idea",
    input_schema={
        "type": "object",
        "required": ["idea_id"],
        "properties": {
            "user_id": {"type": "string"},
            "idea_id": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "ratings": {"type": "object"}
        },
    },
)
@register_action(
    "update_idea",
    input_schema={
        "type": "object",
        "required": ["idea_id"],
        "properties": {
            "user_id": {"type": "string"},
            "idea_id": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "ratings": {"type": "object"}
        },
    },
)
def incubator_update_idea_action(
    idea_id: int,
    user_id: str = "default_user",
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
    ratings: Optional[Dict[str, int]] = None
) -> ActionResult:
    """Update an existing idea in the incubator."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        success = incubator.update_idea(
            idea_id=int(idea_id),
            title=title,
            description=description,
            category=category,
            tags=tags,
            ratings=ratings
        )
        incubator.close()
        return ActionResult(
            success=success,
            data={"idea_id": idea_id, "updated": success}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_export_ideas",
    input_schema={
        "type": "object",
        "required": [],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
@register_action(
    "export_ideas",
    input_schema={
        "type": "object",
        "required": [],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
def incubator_export_ideas_action(user_id: str = "default_user") -> ActionResult:
    """Export incubator ideas to a JSON string."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        json_data = incubator.export_to_json()
        incubator.close()
        return ActionResult(
            success=True,
            data={"json_data": json_data, "user_id": user_id}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_import_ideas",
    input_schema={
        "type": "object",
        "required": ["json_data"],
        "properties": {
            "user_id": {"type": "string"},
            "json_data": {"type": "string"}
        },
    },
)
@register_action(
    "import_ideas",
    input_schema={
        "type": "object",
        "required": ["json_data"],
        "properties": {
            "user_id": {"type": "string"},
            "json_data": {"type": "string"}
        },
    },
)
def incubator_import_ideas_action(json_data: Union[str, dict], user_id: str = "default_user") -> ActionResult:
    """Import ideas into the incubator from JSON data."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        if isinstance(json_data, dict):
            raw_json = json.dumps(json_data)
        else:
            raw_json = str(json_data)
        count = incubator.import_from_json(raw_json)
        incubator.close()
        return ActionResult(
            success=True,
            data={"imported_count": count, "user_id": user_id}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "incubator_get_idea_graph",
    input_schema={
        "type": "object",
        "required": [],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
@register_action(
    "get_idea_graph",
    input_schema={
        "type": "object",
        "required": [],
        "properties": {
            "user_id": {"type": "string"}
        },
    },
)
def incubator_get_idea_graph_action(user_id: str = "default_user") -> ActionResult:
    """Get the full connectivity graph of ideas."""
    try:
        incubator = build_idea_incubator(user_id, memory_engine=_LIVE_MEMORY_ENGINE)
        graph = incubator.get_idea_graph()
        incubator.close()
        return ActionResult(
            success=True,
            data={"graph": graph}
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

# ══════════════
# INITIALIZATION
# ══════════════
save_metadata()

# ═══════
# EXPORTS
# ═══════
__all__ = [
    "ActionResult",
    "ACTIONS",
    "ACTION_METADATA",
    "ACTION_SCHEMAS",
    "execute_action",
    "register_action",
    "safe_resolve_path",
    "load_whitelist",
]

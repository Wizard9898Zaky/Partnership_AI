"""
plugins/example_plugin.py
─────────────────────────
Example plugin demonstrating the Partnership_AI plugin system.

This plugin adds a simple 'hello_world' action that greets the user.
It's auto-discovered by plugin_loader.py on startup — no manual registration needed.
"""
from conversation_engine.action_registry import register_action, ActionResult


@register_action(
    "hello_world",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name to greet (optional)",
                     "default": "World"},
        },
    },
)
def hello_world_action(name: str = "World") -> ActionResult:
    """
    Simple example action that returns a greeting.

    Args:
        name: Name to greet. Defaults to 'World'.

    Returns:
        ActionResult with data['greeting'] = 'Hello, {name}!'.
    """
    return ActionResult(
        success=True,
        data={"greeting": f"Hello, {name}!"},
    )

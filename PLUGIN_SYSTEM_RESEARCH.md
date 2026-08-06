# Partnership_AI Plugin System Research & Architecture

## 1. Current Registration Mechanism (Step-by-Step)

The current action registration mechanism in `Partnership_AI` relies on a centralized registry and side-effect module imports:

1. **Registry Storage Initialization (`conversation_engine/action_registry.py`)**:
   - Defines central in-memory dictionaries:
     - `ACTIONS: Dict[str, Callable] = {}` — maps action names to Python functions.
     - `ACTION_SCHEMAS: Dict[str, Dict[str, Any]] = {}` — maps action names to JSON schema validation dicts.
     - `ACTION_METADATA: Dict[str, Dict[str, Any]] = {}` — maps action names to signature metadata (parameter names, type hints, required flags, docstrings, registration timestamp).

2. **Decorator Pattern (`@register_action`)**:
   - `register_action(name: str, input_schema: Optional[Dict[str, Any]] = None)` acts as a decorator factory.
   - When a tool function is decorated with `@register_action("action_name", input_schema={...})`:
     - `ACTIONS[name] = func` binds the executable function.
     - `ACTION_SCHEMAS[name] = input_schema or {}` stores parameter validation rules.
     - Inspects `func` via `inspect.signature(func)` and `get_type_hints(func)` to generate structured parameter metadata in `ACTION_METADATA[name]`.

3. **Side-Effect Importing (`conversation_engine/tools/__init__.py`)**:
   - At the end of `action_registry.py` (line 489), `import conversation_engine.tools` is executed.
   - `conversation_engine/tools/__init__.py` explicitly imports core tool submodules (`file_tools`, `memory_tools`, `state_tools`, `agent_tools`, `incubator_tools`).
   - Importing these modules triggers top-level `@register_action` decorators, populating `ACTIONS`, `ACTION_SCHEMAS`, and `ACTION_METADATA`.

4. **Action Execution (`execute_action`)**:
   - Caller calls `execute_action(action_name, payload, simulate)`.
   - Validates `payload` against `ACTION_SCHEMAS[action_name]` using `validate_schema()`.
   - If valid, dispatches call to `ACTIONS[action_name](**payload)`.
   - Returns a uniform `ActionResult(success=True/False, data=..., error=..., metadata=...)`.

5. **Metadata Persistence & Integrity Verification**:
   - `save_metadata()` serializes `ACTION_METADATA` to `action_metadata.json` and computes a SHA-256 signature written to `action_metadata.sig`.
   - `load_and_verify_metadata()` checks integrity on startup.

---

## 2. Plugin Auto-Discovery Architecture

To dynamically discover and register plugins without hardcoding imports in `tools/__init__.py`:

1. **Dedicated Directory Structure**:
   - Establish a `plugins/` directory at the project root (`/app/Partnership_AI/plugins/`).

2. **Auto-Discovery Scanner (`load_plugins()`)**:
   - Scan `plugins/` for `.py` files (excluding `__init__.py`, `__pycache__`, and files starting with `_`).
   - Support subdirectory package plugins containing `__init__.py`.

3. **Dynamic Import Mechanism**:
   - Use `importlib.util.spec_from_file_location` and `importlib.util.module_from_spec` (or `importlib.import_module`).
   - Load and execute each module using `loader.exec_module(module)`.

4. **Automatic Registration & Origin Tracking**:
   - Module execution automatically triggers `@register_action` decorators in plugin files.
   - Maintain an `ACTION_SOURCE_MAP[action_name] = file_path` in `action_registry.py` during registration so the agent can trace any action back to its defining file.

5. **Security & Integration Lifecycle**:
   - Automatically add plugin file paths to `introspection_whitelist.json` so `safe_resolve_path()` permits file inspection.
   - Call `save_metadata()` to update `action_metadata.json` and sign `action_metadata.sig`.

---

## 3. Gotchas & Architectural Constraints

1. **Import Order & Bootstrapping Sequence**:
   - `action_registry.py` loads `tools` at the *bottom* of the file after defining `ACTIONS`, `@register_action`, and `ActionResult`.
   - Plugins import `from conversation_engine.action_registry import register_action, ActionResult`.
   - **Gotcha**: Auto-discovery must execute *after* `action_registry.py` completes initial definition to avoid circular import errors or partially initialized module state.

2. **Hardcoded `ACTION_FILE_MAP` in `adaptive_agent.py`**:
   - `AdaptiveAgent._identify_target_files()` (lines 1584–1621) hardcodes a `Dict[str, str]` mapping action names to file paths (`"list_files": "conversation_engine/tools/file_tools.py"`, etc.).
   - **Gotcha**: New plugin actions will not be found by `_identify_target_files()` during gap detection unless `_identify_target_files()` is updated to query `action_registry.get_action_source_file(action_name)`.

3. **Namespace Collisions**:
   - Currently, `@register_action` overwrites `ACTIONS[name]` without checking if `name` already exists.
   - **Gotcha**: A plugin could silently overwrite core actions (`read_file`, `respond_to_user`).
   - **Fix**: Check `if name in ACTIONS` during registration. Reject duplicates or require a `plugin_` name prefix.

4. **Error Isolation**:
   - Syntax or runtime errors in a plugin during import could crash the entire application startup.
   - **Fix**: Wrap individual plugin imports in `try...except Exception` blocks to log warnings and skip broken plugins cleanly.

5. **Sandbox & Security Policies**:
   - `PolicyEngine.PROTECTED_FILES` guards core files (`action_registry.py`, `adaptive_agent.py`, ethics files).
   - Plugins must operate within `safe_resolve_path()` and `introspection_whitelist.json` restrictions.

---

## 4. Recommended Plugin File Format

A plugin should be a single Python file or module package adhering to this template:

```python
"""
plugins/sample_plugin.py
Sample Plugin demonstrating action registration.
"""
from __future__ import annotations
from typing import Any, Dict
from conversation_engine.action_registry import register_action, ActionResult

__plugin_name__ = "sample_plugin"
__version__ = "1.0.0"

@register_action(
    "custom_feature",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"}
        },
        "required": ["query"],
    },
)
def custom_feature_action(query: str) -> ActionResult:
    """Action: executes custom feature logic and returns ActionResult."""
    try:
        # Custom logic here
        result_data = {"processed_query": query.strip().upper()}
        return ActionResult(success=True, data=result_data)
    except Exception as e:
        return ActionResult(success=False, error=str(e))
```

---

## 5. Evolution Pipeline Integration (Plugin Generation & Installation)

The existing evolution pipeline (`adaptive_agent.py` → `patch_generator.py` → `reviewer.py`) can generate and install plugins without modifying core codebase files:

1. **Capability Gap Detection**:
   - When `AdaptiveAgent._trigger_evolution()` detects a missing capability that does not belong in core tools, route the target path to `plugins/plugin_<capability_name>.py`.

2. **Plugin Code Generation (`patch_generator.py`)**:
   - Prompt `patch_generator.py` to generate a complete standalone plugin file conforming to the recommended format.
   - Log a Change Request (CR) in `cr_logs/` with `file: "plugins/plugin_<capability_name>.py"` and full `new_code`.

3. **Review & Approval (`reviewer.py`)**:
   - Human reviewer runs `python reviewer.py` to approve the CR.
   - `reviewer.py` validates syntax via `ast.parse()` and atomically writes the file to `plugins/plugin_<capability_name>.py`.

4. **Hot-Loading & System Registration**:
   - Automatically append `plugins/plugin_<capability_name>.py` to `introspection_whitelist.json`.
   - Call `load_plugins()` to hot-reload/register the new action into `ACTIONS` without requiring an application restart.
   - Call `save_metadata()` to persist updated `action_metadata.json` and sign `action_metadata.sig`.

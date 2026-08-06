"""Tests that every registered action's JSON schema exactly matches its function signature.

This catches schema/signature drift — the most common source of silent runtime failures
where the LLM calls an action with arguments that don't match the Python function.
"""
import inspect
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conversation_engine.action_registry as ar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _schema_required(action_name: str) -> set:
    """Return the set of required parameter names from an action's JSON schema."""
    schema = ar.ACTION_SCHEMAS.get(action_name, {})
    return set(schema.get("required", []))


def _schema_all_props(action_name: str) -> set:
    """Return all property names (required + optional) from an action's JSON schema."""
    schema = ar.ACTION_SCHEMAS.get(action_name, {})
    return set(schema.get("properties", {}).keys())


def _func_params(action_name: str) -> dict:
    """Return {param_name: has_default} for all non-self params of an action function."""
    fn = ar.ACTIONS[action_name]
    sig = inspect.signature(fn)
    result = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        result[name] = param.default is not inspect.Parameter.empty
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def all_actions():
    """All registered action names."""
    return list(ar.ACTIONS.keys())


def test_every_action_has_a_schema(all_actions):
    """Every registered action must have a corresponding JSON schema entry."""
    for name in all_actions:
        assert name in ar.ACTION_SCHEMAS, (
            f"Action '{name}' is registered but has no JSON schema in ACTION_SCHEMAS"
        )


def test_required_schema_params_exist_in_function_signature(all_actions):
    """Schema 'required' fields must all appear as parameters in the function."""
    mismatches = []
    for name in all_actions:
        required = _schema_required(name)
        params = set(_func_params(name).keys())
        missing_in_fn = required - params
        if missing_in_fn:
            mismatches.append(
                f"{name}: schema requires {missing_in_fn} but function has no such params"
            )
    assert not mismatches, "\n".join(mismatches)


def test_required_params_not_marked_optional_in_schema(all_actions):
    """Parameters that have no default in the function should be 'required' in the schema."""
    mismatches = []
    for name in all_actions:
        params = _func_params(name)
        schema_required = _schema_required(name)
        schema_props = _schema_all_props(name)
        for param, has_default in params.items():
            if not has_default and param in schema_props and param not in schema_required:
                mismatches.append(
                    f"{name}: param '{param}' has no default but is not listed as required in schema"
                )
    assert not mismatches, "\n".join(mismatches)


def test_schema_properties_dont_reference_nonexistent_params(all_actions):
    """Schema 'properties' keys must each correspond to a real function parameter."""
    mismatches = []
    for name in all_actions:
        props = _schema_all_props(name)
        params = set(_func_params(name).keys())
        phantom = props - params
        if phantom:
            mismatches.append(
                f"{name}: schema defines {phantom} but function has no such params"
            )
    assert not mismatches, "\n".join(mismatches)


def test_execute_action_rejects_unknown_action():
    """execute_action() must raise or return a failure for an unregistered action name."""
    result = ar.execute_action("this_action_does_not_exist_xyz", {})
    # Should return an ActionResult with success=False, not raise
    assert hasattr(result, "success")
    assert result.success is False


def test_execute_action_rejects_missing_required_arg():
    """execute_action() should fail gracefully when a required argument is omitted."""
    # 'read_file' requires 'file_path' — calling without it should fail cleanly
    result = ar.execute_action("read_file", {})
    assert hasattr(result, "success")
    assert result.success is False

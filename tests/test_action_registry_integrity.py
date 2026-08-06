"""
Tests for conversation_engine/action_registry.py's metadata integrity
verification (load_and_verify_metadata), added after discovering that
action_metadata.sig was written on every save but never read back or
checked anywhere.
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import conversation_engine.action_registry as ar


def _isolated_registry():
    """
    Point the module's file-path globals at a scratch directory so
    tests never touch the real action_metadata.json, and return
    everything needed to fully restore original state afterward.

    IMPORTANT: ACTION_METADATA is a shared, live, importable module-level
    dict - adaptive_agent.py holds a reference to this exact same object.
    Earlier versions of this helper had two isolation bugs, both fixed
    here:
      1. ar.ACTION_METADATA.clear() with no restoration at all, which
         permanently emptied it for the rest of the test process.
      2. A "fix" that restored ACTION_METADATA's *contents* but never
         restored _BASE_DIR / METADATA_FILE / METADATA_SIGNATURE_FILE -
         so those globals stayed pointed at a deleted scratch directory
         for the rest of the process, which broke unrelated tests/code
         (e.g. AdaptiveAgent initializing with only 1 action instead of
         the real ~31) whenever this file happened to run first.
    """
    scratch = Path(tempfile.mkdtemp(prefix="ar_test_"))
    original_state = {
        "_BASE_DIR": ar._BASE_DIR,
        "METADATA_FILE": ar.METADATA_FILE,
        "METADATA_SIGNATURE_FILE": ar.METADATA_SIGNATURE_FILE,
        "ACTION_METADATA": dict(ar.ACTION_METADATA),
    }
    ar._BASE_DIR = scratch
    ar.METADATA_FILE = scratch / "action_metadata.json"
    ar.METADATA_SIGNATURE_FILE = scratch / "action_metadata.sig"
    ar.ACTION_METADATA.clear()
    return scratch, original_state


def _restore_registry(scratch, original_state):
    shutil.rmtree(scratch, ignore_errors=True)
    ar._BASE_DIR = original_state["_BASE_DIR"]
    ar.METADATA_FILE = original_state["METADATA_FILE"]
    ar.METADATA_SIGNATURE_FILE = original_state["METADATA_SIGNATURE_FILE"]
    ar.ACTION_METADATA.clear()
    ar.ACTION_METADATA.update(original_state["ACTION_METADATA"])


def test_no_files_yet_is_treated_as_ok():
    scratch, original = _isolated_registry()
    try:
        result = ar.load_and_verify_metadata()
        assert result.success is True
    finally:
        _restore_registry(scratch, original)


def test_legitimate_save_verifies_successfully():
    scratch, original = _isolated_registry()
    try:
        ar.ACTION_METADATA["some_action"] = {"function_name": "some_action"}
        save_result = ar.save_metadata()
        assert save_result.success is True

        verify_result = ar.load_and_verify_metadata()
        assert verify_result.success is True
    finally:
        _restore_registry(scratch, original)


def test_tampering_is_detected():
    scratch, original = _isolated_registry()
    try:
        ar.ACTION_METADATA["some_action"] = {"function_name": "some_action"}
        ar.save_metadata()

        # Simulate tampering: edit the metadata file directly, bypassing
        # save_metadata() (e.g. a rogue self-generated patch, manual
        # edit, or corruption).
        data = json.loads(ar.METADATA_FILE.read_text())
        data["actions"]["injected_action"] = {"function_name": "evil"}
        ar.METADATA_FILE.write_text(json.dumps(data, indent=2))

        result = ar.load_and_verify_metadata()
        assert result.success is False
        assert "INTEGRITY" in result.error.upper()
    finally:
        _restore_registry(scratch, original)


def test_asymmetric_file_presence_is_detected():
    scratch, original = _isolated_registry()
    try:
        ar.ACTION_METADATA["some_action"] = {"function_name": "some_action"}
        ar.save_metadata()
        # Delete just the signature file, leaving metadata orphaned.
        ar.METADATA_SIGNATURE_FILE.unlink()

        result = ar.load_and_verify_metadata()
        assert result.success is False
    finally:
        _restore_registry(scratch, original)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL: {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")

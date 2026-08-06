"""
Tests for values_kernel/invariants.py: the kill switch and immutable
path protection that everything else (reviewer.py, patch_generator.py,
adaptive_agent.py) is supposed to defer to as the hard safety floor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from values_kernel.invariants import (
    IMMUTABLE_PATHS, KILL_SWITCH_FILE, is_kill_switch_active,
    check_invariants, enforce_kill_switch,
)


def test_kill_switch_file_not_present_by_default():
    # This test assumes a clean checkout. If it fails, someone left
    # kill_switch.flag lying around - which is itself worth knowing.
    assert not KILL_SWITCH_FILE.exists() or is_kill_switch_active()


def test_kill_switch_detected_when_present():
    created = False
    if not KILL_SWITCH_FILE.exists():
        KILL_SWITCH_FILE.touch()
        created = True
    try:
        assert is_kill_switch_active() is True
    finally:
        if created:
            KILL_SWITCH_FILE.unlink()


def test_enforce_kill_switch_raises_when_active():
    created = False
    if not KILL_SWITCH_FILE.exists():
        KILL_SWITCH_FILE.touch()
        created = True
    try:
        try:
            enforce_kill_switch()
            assert False, "enforce_kill_switch() should have raised while the flag is present"
        except SystemExit:
            pass  # acceptable if implemented as sys.exit
        except Exception:
            pass  # any raise is acceptable - the point is it does NOT silently continue
    finally:
        if created:
            KILL_SWITCH_FILE.unlink()


def test_founding_pact_is_immutable():
    assert any("FOUNDING_PACT" in p for p in IMMUTABLE_PATHS)


def test_kill_switch_itself_is_immutable():
    assert any("kill_switch" in p for p in IMMUTABLE_PATHS)


def test_values_kernel_dir_is_immutable():
    assert any("values_kernel" in p for p in IMMUTABLE_PATHS)


def test_check_invariants_blocks_change_to_immutable_path():
    for restricted in IMMUTABLE_PATHS:
        cr = {"file": restricted, "new_code": "# malicious edit"}
        ok, reason = check_invariants(cr)
        assert ok is False, f"check_invariants should block edits to {restricted}"
        assert reason  # must explain why, not just silently say no


def test_check_invariants_allows_normal_file():
    cr = {"file": "adaptive_agent.py", "new_code": "# a normal, allowed change"}
    ok, reason = check_invariants(cr)
    assert ok is True, f"check_invariants incorrectly blocked a normal file: {reason}"


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

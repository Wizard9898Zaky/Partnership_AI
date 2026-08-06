"""
Regression test for the immutable-path drift bug: patch_generator.py
used to maintain its own separate IMMUTABLE_DIRS set that only covered
values_kernel/, silently missing FOUNDING_PACT.md and kill_switch.flag
- both of which values_kernel/invariants.py's canonical IMMUTABLE_PATHS
explicitly protects. Fixed by having patch_generator.py import the
canonical list; this test pins that down so the two can't drift apart
again without a test failing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from values_kernel.invariants import IMMUTABLE_PATHS
import patch_generator


def test_patch_generator_protects_every_canonical_immutable_path():
    for rel_path in IMMUTABLE_PATHS:
        target = (patch_generator.ROOT / rel_path).resolve()
        assert patch_generator._is_under_immutable_dir(target), (
            f"patch_generator.py does not protect '{rel_path}', which "
            f"values_kernel/invariants.py's canonical IMMUTABLE_PATHS requires"
        )


def test_patch_generator_does_not_over_block_normal_files():
    for name in ("adaptive_agent.py", "new_main_chat.py", "reviewer.py"):
        target = (patch_generator.ROOT / name).resolve()
        assert not patch_generator._is_under_immutable_dir(target), (
            f"patch_generator.py incorrectly treats '{name}' as immutable"
        )


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

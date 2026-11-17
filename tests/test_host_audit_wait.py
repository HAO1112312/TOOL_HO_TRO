"""Host uses the same finalization gate while retaining room ALL OK checks."""
import types
import unittest
from unittest.mock import Mock

if __package__:
    from . import test_gui as gui_fixture
else:
    import test_gui as gui_fixture


class HostAuditWaitTests(unittest.TestCase):
    setUp = gui_fixture.GuiTests.setUp

    def host(self, *, passed=True, error="", cancelled=False):
        self.gui.is_validating = True
        self.gui.finish_validation = Mock()
        self.gui.follower.last_round_result = ("round-02", "all ok")
        self.backend.validate_finalized_capture = Mock(return_value=types.SimpleNamespace(
            passed=passed, target="actual-capture/round-02", error=error, cancelled=cancelled))

    def test_waited_local_pass_and_room_all_ok_are_forwarded(self):
        self.host()
        self.gui.run_post_capture_validation("round-02", 0)
        self.backend.validate_finalized_capture.assert_called_once()
        self.gui.finish_validation.assert_called_once_with(0, "round-02", True, True, None)

    def test_room_all_ok_cannot_override_failed_local_audit(self):
        self.host(passed=False, error="video.mp4 incomplete")
        self.gui.run_post_capture_validation("round-02", 0)
        self.gui.finish_validation.assert_called_once_with(
            0, "round-02", False, True, "video.mp4 incomplete")

    def test_native_room_failure_does_not_become_completion_after_local_pass(self):
        self.host()
        self.gui.follower.last_round_result = ("round-02", "failed: player data")
        self.gui.run_post_capture_validation("round-02", 0)
        self.gui.finish_validation.assert_called_once_with(
            0, "round-02", True, False, "failed: player data")

    def test_stale_or_cancelled_wait_cannot_apply_to_new_host_task(self):
        self.host(cancelled=True)
        self.gui.run_post_capture_validation("round-02", 0)
        self.gui.finish_validation.assert_not_called()
        self.backend.validate_finalized_capture.return_value.cancelled = False
        def newer_task(*args, **kwargs):
            active = kwargs["should_continue"]
            self.assertTrue(active())
            self.gui._transition_token += 1
            self.assertFalse(active())
            return types.SimpleNamespace(passed=True, target="round-02", error="", cancelled=False)
        self.backend.validate_finalized_capture.side_effect = newer_task
        self.gui.run_post_capture_validation("round-02", 0)
        self.gui.finish_validation.assert_not_called()
        self.backend.cleanup_all_odx.assert_not_called()


if __name__ == "__main__":
    unittest.main()

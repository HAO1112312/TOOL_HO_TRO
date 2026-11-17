"""Physical key routing without installing hooks or emitting keyboard input."""
import unittest
from unittest.mock import Mock

from odx_hotkeys import route_physical_hotkey


class HotkeyDispatchTests(unittest.TestCase):
    def setUp(self):
        self.pressed = set()
        self.dispatch = Mock()
        self.capture = Mock(side_effect=lambda key, injected: not injected and key in (0x79, 0x7A, 0x78, 0x77))

    def route(self, message, key=0x79, *, injected=False):
        return route_physical_hotkey(self.pressed, message, key, injected=injected,
                                     should_capture=self.capture, dispatch=self.dispatch)

    def test_f10_dispatches_on_initial_keydown_without_waiting_for_release(self):
        self.assertTrue(self.route(0x0100))
        self.dispatch.assert_called_once_with(0x79)
        self.assertEqual(self.pressed, {0x79})

    def test_holding_f10_and_keyup_never_dispatches_another_operation(self):
        self.route(0x0100)
        for _ in range(30):
            self.assertTrue(self.route(0x0100))
        self.assertTrue(self.route(0x0101))
        self.dispatch.assert_called_once_with(0x79)
        self.assertEqual(self.pressed, set())

    def test_new_press_after_release_dispatches_once_again(self):
        self.route(0x0100)
        self.route(0x0101)
        self.route(0x0100)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_keyup_without_captured_keydown_cannot_dispatch(self):
        self.assertTrue(self.route(0x0101))
        self.dispatch.assert_not_called()

    def test_system_keydown_and_keyup_follow_the_same_single_press_rule(self):
        self.assertTrue(self.route(0x0104))
        self.assertTrue(self.route(0x0104))
        self.assertTrue(self.route(0x0105))
        self.dispatch.assert_called_once_with(0x79)

    def test_selector_keys_remain_independent_single_shot_keydown_actions(self):
        for key in (0x7A, 0x78, 0x77):
            with self.subTest(key=key):
                self.dispatch.reset_mock()
                self.assertTrue(self.route(0x0100, key))
                self.assertTrue(self.route(0x0100, key))
                self.assertTrue(self.route(0x0101, key))
                self.dispatch.assert_called_once_with(key)

    def test_injected_key_messages_continue_without_dispatch_or_pressed_state(self):
        for message in (0x0100, 0x0101):
            self.assertFalse(self.route(message, injected=True))
        self.dispatch.assert_not_called()
        self.assertFalse(self.pressed)
        self.capture.assert_any_call(0x79, True)

    def test_unowned_key_messages_continue_without_dispatch_or_pressed_state(self):
        for message in (0x0100, 0x0101):
            self.assertFalse(self.route(message, key=0x41))
        self.dispatch.assert_not_called()
        self.assertFalse(self.pressed)

    def test_unrelated_window_messages_cannot_trigger_a_hotkey(self):
        self.assertFalse(self.route(0x0200))
        self.dispatch.assert_not_called()
        self.assertFalse(self.pressed)


if __name__ == "__main__":
    unittest.main()

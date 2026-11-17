"""End a verified Host round without global input or a second toggle."""
import json
from contextlib import ExitStack
import types
import unittest
from unittest.mock import Mock, patch

import odx_backend as backend


class VerifiedHostStopTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = 0.0
        self.context = (123, 321)
        self.end_at = 1.0
        self.armed_at = None
        self.status = 'stop_action'
        self.api_error = None
        self.server_task = 'current'
        self.server_host = 'host'
        self.dispatches = []
        self.ended = False
        self.lose_reply = False
        self.follower = types.SimpleNamespace(
            current_recording='current', last_armed=None, current_stopped=None,
            last_round_result=None, check_new_lines=Mock(side_effect=self.update))
        self.ownership = self.stack.enter_context(patch.object(
            backend, '_owned_uia_context', return_value=self.context))
        self.global_key = self.stack.enter_context(patch.object(backend, 'send_toggle_hotkey'))
        self.cleanup = self.stack.enter_context(patch.object(backend, 'cleanup_all_odx'))
        self.script = self.stack.enter_context(patch.object(
            backend, '_host_stop_script', side_effect=lambda task, allow_armed=False, invoke=False,
            context=None, **identity: dict(task=task, allow_armed=allow_armed, invoke=invoke,
                                         context=context, **identity)))
        self.run = self.stack.enter_context(patch.object(backend, 'run_ps_script', side_effect=self.native))
        self.api = self.stack.enter_context(patch.object(backend, 'fetch_room_status', side_effect=self.room))
        self.stack.enter_context(patch.object(backend.time, 'monotonic', side_effect=lambda: self.clock))
        self.stack.enter_context(patch.object(backend.time, 'sleep', side_effect=self.advance))

    def advance(self, seconds):
        self.clock = round(self.clock + seconds, 6)

    def update(self):
        if self.armed_at is not None and self.clock >= self.armed_at:
            self.follower.last_armed = 'current'
        if self.dispatches and self.end_at is not None and self.clock >= self.end_at:
            self.ended = True
            self.follower.current_recording = self.follower.last_armed = None
            self.follower.current_stopped = ('current', 'round_end')

    def reply(self, **fields):
        body = dict(status=self.status, hwnd=123, pid=321, room_code='ABC123',
                    player_id='host', task_id='current', is_host=True, invoked=False)
        body.update(fields)
        return types.SimpleNamespace(returncode=0, stdout='ODX_HOST_STOP:' + json.dumps(body))

    def native(self, script, **kwargs):
        if script['invoke']:
            self.dispatches.append(self.clock)
            if self.lose_reply:
                return None
            return self.reply(status='invoked', invoked=True)
        return self.reply(status='stopped' if self.ended else self.status)

    def room(self, room):
        if self.api_error:
            return {'error': self.api_error}
        return dict(host=self.server_host,
                    current_session=None if self.ended else {'task': self.server_task})

    def stop(self, **kwargs):
        return backend.stop_recording_verified(self.follower, 'current', hwnd=123,
                                               timeout=30, **kwargs)

    def tearDown(self):
        self.global_key.assert_not_called()
        self.cleanup.assert_not_called()

    def test_scoped_end_is_sent_once_until_room_and_local_stop_are_confirmed(self):
        self.end_at = 18
        self.assertTrue(self.stop())
        self.assertEqual(self.dispatches, [0])
        self.assertEqual(self.clock, 18)
        request = next(call.args[0] for call in self.run.call_args_list if call.args[0]['invoke'])
        self.assertEqual((request['context'], request['expected_room'], request['expected_player']),
                         (self.context, 'ABC123', 'host'))

    def test_timeout_never_retries_an_uncertain_end_toggle(self):
        self.end_at = None
        self.assertFalse(self.stop())
        self.assertEqual(self.dispatches, [0])
        self.assertEqual(self.clock, 30)

    def test_lost_dispatch_reply_waits_for_real_end_without_repeating(self):
        self.lose_reply = True
        self.assertTrue(self.stop())
        self.assertEqual(self.dispatches, [0])

    def test_room_final_result_requires_no_native_or_network_action(self):
        self.follower.current_recording = None
        self.follower.last_round_result = ('current', 'all ok')
        self.assertTrue(self.stop())
        self.run.assert_not_called()
        self.api.assert_not_called()

    def test_local_kept_and_stop_are_not_sufficient_when_other_players_still_record(self):
        self.follower.current_recording = None
        self.follower.current_stopped = ('current', 'round_end')
        self.follower.last_round_result = ('current', 'ok')
        self.assertTrue(self.stop())
        self.assertEqual(self.dispatches, [0])
        self.assertTrue(self.ended)

    def test_stale_other_task_result_cannot_authorize_a_stop(self):
        self.follower.current_recording = None
        self.follower.last_round_result = ('old', 'all ok')
        self.assertFalse(self.stop())
        self.run.assert_not_called()

    def test_other_active_task_cannot_be_stopped_even_with_matching_old_result(self):
        self.follower.current_recording = 'different'
        self.follower.last_round_result = ('current', 'all ok')
        self.assertFalse(self.stop(allow_armed=True))
        self.run.assert_not_called()

    def test_matching_armed_countdown_can_be_ended_without_starting_it(self):
        self.follower.current_recording = None
        self.follower.last_armed = 'current'
        self.assertTrue(self.stop(allow_armed=True))
        self.assertEqual(self.dispatches, [0])

    def test_late_armed_identity_and_confirmation_share_one_deadline(self):
        self.follower.current_recording = None
        self.armed_at, self.end_at = 5, 18
        self.assertTrue(self.stop(allow_armed=True))
        self.assertEqual(self.dispatches, [5])
        self.assertEqual(self.clock, 18)

    def test_waiting_for_arm_does_not_extend_stop_deadline(self):
        self.follower.current_recording = None
        self.armed_at, self.end_at = 25, 35
        self.assertFalse(self.stop(allow_armed=True))
        self.assertEqual(self.dispatches, [25])
        self.assertEqual(self.clock, 30)

    def test_missing_armed_task_never_dispatches(self):
        self.follower.current_recording = None
        self.assertFalse(self.stop(allow_armed=True))
        self.assertFalse(self.dispatches)

    def test_guard_rejects_cancelled_operation_before_any_observation(self):
        self.assertFalse(self.stop(can_stop=lambda: False))
        self.follower.check_new_lines.assert_not_called()
        self.run.assert_not_called()

    def test_guard_exception_does_not_dispatch(self):
        self.assertFalse(self.stop(can_stop=Mock(side_effect=OSError('owner lost'))))
        self.run.assert_not_called()

    def test_different_or_replaced_native_window_never_dispatches(self):
        self.ownership.side_effect = [self.context, (999, 777)]
        self.assertFalse(self.stop())
        self.run.assert_not_called()

    def test_report_from_another_window_cannot_authorize_action(self):
        self.run.side_effect = lambda *args, **kwargs: self.reply(hwnd=999)
        self.assertFalse(self.stop())
        self.assertFalse(self.dispatches)

    def test_start_ready_wrong_role_or_task_reports_are_never_invoked(self):
        for status, fields in (('stopped', {}), ('blocked', {}),
                               ('stop_action', {'is_host': False}),
                               ('stop_action', {'task_id': 'other'})):
            with self.subTest(status=status, fields=fields):
                self.clock = 0
                self.run.side_effect = lambda *args, s=status, f=fields, **kwargs: self.reply(status=s, **f)
                self.assertFalse(self.stop())
                self.assertFalse(self.dispatches)

    def test_server_host_or_task_mismatch_cannot_authorize_end(self):
        for host, task in (('other-host', 'current'), ('host', 'different-task')):
            with self.subTest(host=host, task=task):
                self.clock = 0
                self.server_host, self.server_task = host, task
                self.assertFalse(self.stop())
                self.assertFalse(self.dispatches)

    def test_network_error_keeps_odx_open_and_never_guesses_an_end_action(self):
        self.api_error = 'TIMEOUT'
        messages = []
        self.assertFalse(self.stop(on_status=lambda message, level: messages.append(message)))
        self.assertFalse(self.dispatches)
        self.assertTrue(messages)

    def test_stale_final_does_not_short_circuit_a_live_current_task(self):
        self.follower.last_round_result = ('current', 'all ok')
        self.assertTrue(self.stop())
        self.assertEqual(self.dispatches, [0])


if __name__ == '__main__':
    unittest.main()

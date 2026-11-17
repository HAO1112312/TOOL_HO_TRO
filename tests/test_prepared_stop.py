"""Warm Host stop transport: no action before claim and no ambiguous retry."""
from contextlib import ExitStack
import os
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import odx_backend as backend

if __package__:
    from . import test_verified_stop as verified_fixture
    from . import test_stop_native as native_fixture
else:
    import test_verified_stop as verified_fixture
    import test_stop_native as native_fixture


class DeferredThread:
    created = []

    def __init__(self, *, target, **kwargs):
        self.target = target
        self.created.append(self)

    def start(self):
        pass


class PreparedStopStateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 10
        DeferredThread.created = []
        self.stack.enter_context(patch.object(backend.threading, 'Thread', DeferredThread))
        self.stack.enter_context(patch.object(backend.time, 'monotonic', side_effect=lambda: self.now))
        self.native = self.stack.enter_context(patch.object(backend, '_owned_uia_context', return_value=(123, 321)))
        self.api = self.stack.enter_context(patch.object(backend, 'fetch_room_status'))
        self.cleanup = self.stack.enter_context(patch.object(backend, 'cleanup_all_odx'))
        self.transport = types.SimpleNamespace(alive=True, request=Mock(), close=Mock())
        self.job = backend.prepare_recording_stop(types.SimpleNamespace(), 'round-01', 123)
        self.job.context = (123, 321)
        self.job._transport = self.transport
        self.job._snapshot = dict(observed_at=10, room='ABC123', player='host')

    def report(self, **kwargs):
        report = dict(status='stop_action', hwnd=123, pid=321, room_code='ABC123',
                      player_id='host', task_id='round-01', is_host=True, invoked=False)
        report.update(kwargs)
        return report

    def tearDown(self):
        self.cleanup.assert_not_called()

    def test_factory_does_not_read_native_or_network_on_calling_thread(self):
        self.assertEqual(len(DeferredThread.created), 1)
        self.native.assert_not_called()
        self.api.assert_not_called()
        self.transport.request.assert_not_called()

    def test_claim_is_action_free_nonblocking_and_one_time(self):
        self.assertTrue(self.job.ready)
        self.assertTrue(self.job.claim())
        self.assertFalse(self.job.claim())
        self.assertTrue(self.job._stop_refresh.is_set())
        self.native.assert_not_called()
        self.api.assert_not_called()
        self.transport.request.assert_not_called()

    def test_stale_or_missing_snapshot_is_not_claimable(self):
        self.now = 12.01
        self.assertFalse(self.job.ready)
        self.assertFalse(self.job.claim())
        self.job._snapshot = None
        self.assertFalse(self.job.claim())
        self.transport.request.assert_not_called()

    def test_claim_handoff_ignores_old_prepare_guard_after_transition(self):
        self.job.can_prepare = Mock(return_value=False)
        self.assertTrue(self.job.claim())
        self.transport.request.return_value = self.report(status='invoked', invoked=True)
        attempted, report, context = self.job.dispatch_once('round-01', 123, lambda: True, 30)
        self.assertTrue(attempted)
        self.assertTrue(report['invoked'])
        self.assertEqual(context, (123, 321))
        self.job.can_prepare.assert_not_called()
        self.transport.request.assert_called_once_with('invoke', room='ABC123', player='host', timeout=1.5)

    def test_unclaimed_job_never_dispatches(self):
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_not_called()

    def test_cancelled_or_wrong_task_or_window_never_dispatches(self):
        self.assertTrue(self.job.claim())
        for task, hwnd in [('other-task', 123), ('round-01', 999)]:
            self.assertFalse(self.job.dispatch_once(task, hwnd, lambda: True, 30)[0])
        self.job.cancel()
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_not_called()

    def test_fresh_stop_guard_and_owned_context_are_rechecked_before_action(self):
        self.assertTrue(self.job.claim())
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: False, 30)[0])
        self.native.return_value = (999, 777)
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_not_called()

    def test_snapshot_expiring_between_claim_and_dispatch_uses_no_permission(self):
        self.assertTrue(self.job.claim())
        self.now = 12.01
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_not_called()

    def test_ambiguous_write_consumes_permission_and_is_not_retried(self):
        self.assertTrue(self.job.claim())
        self.transport.request.side_effect = BrokenPipeError('reply lost')
        self.assertTrue(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.assertTrue(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_called_once()

    def test_cancel_only_schedules_close_of_its_private_transport(self):
        self.job.cancel()
        self.job.cancel()
        self.assertEqual(len(DeferredThread.created), 2)
        DeferredThread.created[-1].target()
        self.transport.close.assert_called_once()
        self.transport.request.assert_not_called()

    def test_monitor_exit_cannot_cancel_a_claimed_stop_handoff(self):
        self.assertTrue(self.job.claim())
        self.job._cancel_unclaimed()
        self.assertFalse(self.job._cancelled)
        self.transport.close.assert_not_called()

    def test_snapshot_requires_exact_fresh_native_and_server_identity(self):
        server = dict(host='host', current_session={'task_id': 'round-01'})
        self.assertIsNotNone(self.job._accept_snapshot(self.report(), server, 10))
        for fields in ({'hwnd': 999}, {'pid': 999}, {'is_host': False}, {'task_id': 'other'},
                       {'room_code': 'invalid'}, {'player_id': ''}, {'status': 'stopped'}):
            self.assertIsNone(self.job._accept_snapshot(self.report(**fields), server, 10))
        for fields in ({'host': 'other'}, {'current_session': None},
                       {'current_session': {'task': 'other'}}, {'error': 'NETWORK_ERROR'}):
            self.assertIsNone(self.job._accept_snapshot(self.report(), dict(server, **fields), 10))

    def native_cache(self, *, recording='round-01', armed=None):
        self.job.follower.current_recording = recording
        self.job.follower.last_armed = armed
        self.job._native_snapshot = self.job._accept_native_snapshot(self.report(), 10)
        self.job._native_identity = ('ABC123', 'host')

    def test_manual_native_claim_can_dispatch_while_server_snapshot_is_unavailable(self):
        self.native_cache()
        self.job._snapshot = None
        self.now = 100
        self.assertFalse(self.job.ready)
        self.assertFalse(self.job.claim(), 'Automatic deadline still requires fresh server preflight')
        self.assertTrue(self.job.claim(native_only=True))
        self.transport.request.return_value = self.report(status='invoked', invoked=True)
        attempted, response, _ = self.job.dispatch_once('round-01', 123, lambda: True, 30)
        self.assertTrue(attempted)
        self.assertTrue(response['invoked'])
        self.api.assert_not_called()
        self.transport.request.assert_called_once_with('invoke', room='ABC123', player='host', timeout=1.5)

    def test_manual_native_claim_rejects_old_or_different_capture_proof(self):
        for recording, armed in [(None, None), ('other-task', None), ('other-task', 'round-01')]:
            with self.subTest(recording=recording, armed=armed):
                self.native_cache(recording=recording, armed=armed)
                self.assertFalse(self.job.claim(native_only=True))
        self.transport.request.assert_not_called()

    def test_manual_native_claim_accepts_same_armed_capture_and_rechecks_before_dispatch(self):
        self.native_cache(recording=None, armed='round-01')
        self.job._snapshot = None
        self.assertTrue(self.job.claim(native_only=True))
        self.job.follower.last_armed = None
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_not_called()

    def test_manual_native_dispatch_still_rejects_owner_change_and_stop_guard(self):
        self.native_cache()
        self.assertTrue(self.job.claim(native_only=True))
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: False, 30)[0])
        self.native.return_value = (999, 888)
        self.assertFalse(self.job.dispatch_once('round-01', 123, lambda: True, 30)[0])
        self.transport.request.assert_not_called()

    def test_native_cache_cannot_move_to_another_room_or_player(self):
        self.native_cache()
        for fields in ({'room_code': 'XYZ789'}, {'player_id': 'new-host'},
                       {'task_id': 'other-task'}, {'is_host': False}, {'status': 'stopped'}):
            with self.subTest(fields=fields):
                self.assertIsNone(self.job._accept_native_snapshot(self.report(**fields), 100))

    def test_manual_native_claim_is_blocked_for_missing_cache_dead_or_cancelled_helper(self):
        self.native_cache()
        self.job._native_snapshot = None
        self.assertFalse(self.job.claim(native_only=True))
        self.native_cache()
        self.transport.alive = False
        self.assertFalse(self.job.claim(native_only=True))
        self.transport.alive = True
        self.job.cancel()
        self.assertFalse(self.job.claim(native_only=True))

    def test_native_cache_is_published_before_a_slow_http_check(self):
        self.native_cache()
        self.job._native_snapshot = None
        self.job._native_identity = None
        self.job._snapshot = None
        self.transport.request.return_value = self.report()
        observations = []
        def slow_http(*args, **kwargs):
            observations.append(self.job._native_snapshot is not None)
            observations.append(self.job.claim(native_only=True))
            self.now = 100
            return {'error': 'TIMEOUT'}
        self.api.side_effect = slow_http
        with patch.object(backend, '_PreparedStopProcess', return_value=self.transport):
            self.job._prepare()
        self.assertEqual(observations, [True, True])
        self.assertFalse(self.job._cancelled, 'A claimed F10 handoff survives preparation callback exit')
        self.assertTrue(self.job.ready)
        self.transport.close.assert_not_called()


class PreparedStopTransactionTests(unittest.TestCase):
    setUp = verified_fixture.VerifiedHostStopTests.setUp
    tearDown = verified_fixture.VerifiedHostStopTests.tearDown
    advance = verified_fixture.VerifiedHostStopTests.advance
    update = verified_fixture.VerifiedHostStopTests.update
    reply = verified_fixture.VerifiedHostStopTests.reply
    native = verified_fixture.VerifiedHostStopTests.native
    room = verified_fixture.VerifiedHostStopTests.room
    stop = verified_fixture.VerifiedHostStopTests.stop

    def prepared(self, *, attempted=True, reply=True):
        def dispatch(task, hwnd, allowed, timeout):
            self.assertEqual((task, hwnd), ('current', 123))
            self.assertTrue(allowed())
            if attempted:
                self.dispatches.append(self.clock)
            return attempted, (dict(status='invoked', hwnd=123, pid=321, invoked=True) if reply else None), (123, 321)
        return types.SimpleNamespace(dispatch_once=Mock(side_effect=dispatch), cancel=Mock())

    def test_warm_action_occurs_before_any_cold_native_or_http_roundtrip(self):
        prepared = self.prepared()
        self.end_at = .1
        self.assertTrue(self.stop(prepared=prepared))
        self.assertEqual(self.dispatches, [0])
        self.assertFalse(any(call.args[0]['invoke'] for call in self.run.call_args_list))
        prepared.cancel.assert_called_once()

    def test_unready_prepared_job_falls_back_to_original_verified_cold_stop(self):
        prepared = self.prepared(attempted=False)
        self.assertTrue(self.stop(prepared=prepared))
        self.assertEqual(self.dispatches, [0])
        self.assertEqual(sum(call.args[0]['invoke'] for call in self.run.call_args_list), 1)
        prepared.cancel.assert_called_once()

    def test_lost_warm_reply_never_sends_a_second_native_toggle(self):
        prepared = self.prepared(reply=False)
        self.assertTrue(self.stop(prepared=prepared))
        self.assertEqual(self.dispatches, [0])
        self.assertFalse(any(call.args[0]['invoke'] for call in self.run.call_args_list))

    def test_warm_permission_does_not_claim_success_until_actual_room_end(self):
        prepared = self.prepared(reply=False)
        self.end_at = None
        self.assertFalse(self.stop(prepared=prepared))
        self.assertEqual(self.clock, 30)
        self.assertEqual(self.dispatches, [0])
        self.assertFalse(any(call.args[0]['invoke'] for call in self.run.call_args_list))

    def test_terminal_round_or_guard_failure_never_dispatches_prepared_job(self):
        prepared = self.prepared()
        self.follower.current_recording = None
        self.follower.last_round_result = ('current', 'all ok')
        self.assertTrue(self.stop(prepared=prepared))
        prepared.dispatch_once.assert_not_called()
        prepared.cancel.assert_called_once()

    def test_reusing_consumed_ambiguous_handoff_never_enters_cold_dispatch(self):
        with patch.object(backend.threading, 'Thread', DeferredThread):
            job = backend.prepare_recording_stop(self.follower, 'current', 123)
            job.context = (123, 321)
            transport = types.SimpleNamespace(alive=True, request=Mock(), close=Mock())
            job._transport = transport
            job._snapshot = dict(observed_at=0, room='ABC123', player='host')
            self.assertTrue(job.claim())
            def lost_reply(*args, **kwargs):
                self.dispatches.append(self.clock)
                return None
            transport.request.side_effect = lost_reply
            self.end_at = None
            self.assertFalse(self.stop(prepared=job))
            self.assertFalse(self.stop(prepared=job))
        self.assertEqual(self.dispatches, [0])
        self.assertFalse(any(call.args[0]['invoke'] for call in self.run.call_args_list))
        transport.request.assert_called_once()
        prepared = self.prepared()
        self.assertFalse(self.stop(prepared=prepared, can_stop=lambda: False))
        prepared.dispatch_once.assert_not_called()
        prepared.cancel.assert_called_once()


@unittest.skipUnless(os.name == 'nt', 'Windows WPF/UIAutomation')
class PreparedStopNativeTests(unittest.TestCase):
    fixture = native_fixture.BackgroundHostStopNativeTests.fixture

    def test_manual_native_handoff_dispatches_while_preparation_http_is_blocked(self):
        with self.fixture(language='en', mode='minimized') as (_, context, read_state, _):
            entered_http, release_http = threading.Event(), threading.Event()
            follower = types.SimpleNamespace(current_recording=native_fixture.TASK,
                last_armed=None, current_stopped=None, last_round_result=None)

            def follow():
                if read_state()['ended']:
                    follower.current_recording = None
                    follower.current_stopped = (native_fixture.TASK, 'round_end')
                    follower.last_round_result = (native_fixture.TASK, 'all ok')

            follower.check_new_lines = follow

            def room(*args, **kwargs):
                if not entered_http.is_set():
                    entered_http.set()
                    release_http.wait(5)
                    return {'error': 'TIMEOUT'}
                return dict(host='HOST_TEST', current_session=(
                    None if read_state()['ended'] else {'task': native_fixture.TASK}))

            with patch.object(backend, 'fetch_room_status', side_effect=room):
                job = backend.prepare_recording_stop(follower, native_fixture.TASK, context[0],
                                                     can_prepare=lambda: True)
                try:
                    self.assertTrue(entered_http.wait(8), 'Native preflight must precede the slow HTTP request')
                    self.assertFalse(job.ready)
                    self.assertFalse(job.claim())
                    self.assertTrue(job.claim(native_only=True))
                    self.assertTrue(backend.stop_recording_verified(
                        follower, native_fixture.TASK, hwnd=context[0], timeout=8,
                        can_stop=lambda: True, prepared=job))
                    self.assertFalse(release_http.is_set(), 'Native action must not wait for the held HTTP reply')
                    self.assertEqual(read_state()['clicks'], 1)
                    self.assertTrue(read_state()['ended'])
                finally:
                    release_http.set()
                    job.cancel()
                    if job._transport is not None:
                        job._transport.process.wait(timeout=3)

    def test_factory_refresh_handoff_and_stop_confirmation_use_same_warm_worker(self):
        with self.fixture(language='en', mode='offscreen') as (_, context, read_state, _):
            follower = types.SimpleNamespace(current_recording=native_fixture.TASK,
                last_armed=None, current_stopped=None, last_round_result=None)

            def follow():
                if read_state()['ended']:
                    follower.current_recording = None
                    follower.current_stopped = (native_fixture.TASK, 'round_end')
                    follower.last_round_result = (native_fixture.TASK, 'all ok')

            follower.check_new_lines = follow

            def room(*args, **kwargs):
                return dict(host='HOST_TEST', current_session=(
                    None if read_state()['ended'] else {'task': native_fixture.TASK}))

            with patch.object(backend, 'fetch_room_status', side_effect=room):
                job = backend.prepare_recording_stop(follower, native_fixture.TASK, context[0],
                                                     can_prepare=lambda: True)
                try:
                    deadline = time.monotonic() + 10
                    while not job.ready and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(job.ready)
                    self.assertEqual(read_state()['clicks'], 0)
                    worker_pid = job._transport.process.pid
                    self.assertTrue(job.claim())
                    self.assertTrue(backend.stop_recording_verified(
                        follower, native_fixture.TASK, hwnd=context[0], timeout=8,
                        can_stop=lambda: True, prepared=job))
                    self.assertTrue(read_state()['ended'])
                    self.assertEqual(read_state()['clicks'], 1)
                    self.assertEqual(job._transport.process.pid, worker_pid)
                finally:
                    job.cancel()
                    if job._transport is not None:
                        job._transport.process.wait(timeout=3)

    def test_persistent_worker_probes_without_action_then_invokes_only_once(self):
        with self.fixture(language='en', mode='minimized') as (_, context, read_state, _):
            transport = backend._PreparedStopProcess(native_fixture.TASK, context)
            try:
                first = transport.request('probe', timeout=8)
                self.assertIsNotNone(first)
                self.assertEqual(first['status'], 'stop_action', first)
                self.assertEqual(read_state()['clicks'], 0)
                second = transport.request('probe', timeout=2)
                self.assertEqual(second['status'], 'stop_action')
                self.assertEqual(read_state()['clicks'], 0)
                dispatch_started = time.perf_counter()
                response = transport.request('invoke', room=first['room_code'], player=first['player_id'], timeout=2)
                dispatch_ms = (time.perf_counter() - dispatch_started) * 1000
                print(f'Warm synthetic Host invoke response: {dispatch_ms:.1f} ms')
                self.assertTrue(response['invoked'])
                deadline = time.monotonic() + 3
                while not read_state()['ended'] and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(read_state()['ended'])
                self.assertEqual(read_state()['clicks'], 1)
                transport.request('invoke', room=first['room_code'], player=first['player_id'], timeout=.1)
                self.assertEqual(read_state()['clicks'], 1)
            finally:
                transport.close()
            self.assertFalse(transport.alive)

    def test_native_identity_is_rechecked_at_warm_dispatch(self):
        with self.fixture() as (_, context, read_state, command):
            transport = backend._PreparedStopProcess(native_fixture.TASK, context)
            try:
                first = transport.request('probe', timeout=8)
                self.assertEqual(first['status'], 'stop_action', first)
                command.write_text('change-task', encoding='utf-8')
                deadline = time.monotonic() + 3
                while command.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                response = transport.request('invoke', room=first['room_code'], player=first['player_id'], timeout=2)
                self.assertFalse(response['invoked'])
                self.assertEqual(response['status'], 'wrong_identity')
                self.assertEqual(read_state()['clicks'], 0)
            finally:
                transport.close()


if __name__ == '__main__':
    unittest.main()

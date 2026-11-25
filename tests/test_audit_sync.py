"""Local audit verdicts use scoped loopback HTTP, never native Odx or capture IO."""
import json
import time
import unittest
from urllib import error, request

import odx_sync as sync


class AuditTransportTests(unittest.TestCase):
    def setUp(self):
        self.task, self.nonce = "round-01", "a" * 32
        self.server = sync.HostSettingsServer("ABC123", "HOST_TEST", bind_host="127.0.0.1",
                                             port=0, discovery_port=0).start()
        self.addCleanup(self.server.stop)
        self.settings = dict(task_id=self.task, start_request_id=self.nonce,
                             phase="recording", target_duration=240)
        self.revision = self.server.publish(self.settings)
        self.address = f"127.0.0.1:{self.server.port}"
        self.client = sync.PlayerSettingsClient("ABC123", "HOST_TEST", host_address=self.address, timeout=.5)

    def payload(self, **changes):
        value = dict(task_id=self.task, start_request_id=self.nonce, player_id="P2",
                     passed=False, error="F12 is not in key bindings", report_id=1)
        value.update(changes)
        return value

    def post(self, payload, headers=None, raw=None):
        outgoing_headers = {"X-Odx-Room": "ABC123", "X-Odx-Host": "HOST_TEST",
                            "X-Odx-Instance": self.server.instance_id, "Content-Type": "application/json"}
        outgoing_headers.update(headers or {})
        body = json.dumps(payload).encode("utf-8") if raw is None else raw
        outgoing = request.Request("http://" + self.address + sync.AUDIT_PATH, data=body,
                                   method="POST", headers=outgoing_headers)
        try:
            with request.urlopen(outgoing, timeout=.5) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            exc.close()
            return exc.code, None

    def reports(self):
        return self.server.audit_results(self.task, self.nonce)

    def test_player_fail_is_received_without_granting_settings_ack(self):
        self.client.fetch()
        self.assertTrue(self.client.report_audit(self.task, self.nonce, "P2", False, "unbound F12"),
                        self.client.last_audit_error)
        result = self.reports()["P2"]
        self.assertIs(result["passed"], False)
        self.assertEqual(result["error"], "unbound F12")
        self.assertEqual(result["task_id"], self.task)
        self.assertEqual(result["start_request_id"], self.nonce)
        self.assertEqual(self.server.applied_players(self.revision), set())

    def test_revision_changes_in_same_round_do_not_discard_audit(self):
        self.client.fetch()
        self.server.publish(dict(self.settings, phase="validating"))
        self.assertTrue(self.client.report_audit(self.task, self.nonce, "P2", True))
        self.assertTrue(self.reports()["P2"]["passed"])

    def test_report_needs_a_fetched_matching_task_nonce(self):
        self.assertFalse(self.client.report_audit(self.task, self.nonce, "P2", True))
        self.client.fetch()
        self.assertFalse(self.client.report_audit("round-02", self.nonce, "P2", True))
        self.assertFalse(self.client.report_audit(self.task, "b" * 32, "P2", True))
        self.assertEqual(self.reports(), {})

    def test_host_instance_room_and_host_scope_reject_foreign_reports(self):
        for headers, expected in (({"X-Odx-Room": "XYZ123"}, 403),
                                  ({"X-Odx-Host": "OTHER"}, 403),
                                  ({"X-Odx-Instance": "b" * 32}, 409)):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(self.payload(), headers)[0], expected)
        self.assertEqual(self.reports(), {})

    def test_old_task_and_old_start_nonce_cannot_fill_new_round(self):
        self.server.publish(dict(self.settings, start_request_id="b" * 32))
        self.assertEqual(self.post(self.payload())[0], 409)
        self.assertEqual(self.server.audit_results(self.task, "b" * 32), {})
        self.server.publish(dict(self.settings, task_id="round-02"))
        self.assertEqual(self.post(self.payload())[0], 409)

    def test_delayed_pass_cannot_overwrite_newer_fail_and_retry_is_idempotent(self):
        fail = self.payload(report_id=2)
        self.assertEqual(self.post(fail)[0], 200)
        before = self.reports()["P2"]["received_at"]
        self.assertEqual(self.post(fail)[0], 200)
        self.assertEqual(self.reports()["P2"]["received_at"], before)
        self.assertEqual(self.post(self.payload(report_id=1, passed=True, error=""))[0], 409)
        self.assertEqual(self.post(self.payload(report_id=2, passed=True, error=""))[0], 409)
        self.assertFalse(self.reports()["P2"]["passed"])

    def test_newer_explicit_recheck_can_recover_failed_audit(self):
        self.client.fetch()
        self.assertTrue(self.client.report_audit(self.task, self.nonce, "P2", False, "file still pending"))
        old = self.reports()["P2"]["report_id"]
        self.assertTrue(self.client.report_audit(self.task, self.nonce, "P2", True))
        self.assertGreater(self.reports()["P2"]["report_id"], old)
        self.assertTrue(self.reports()["P2"]["passed"])
        self.assertEqual(self.reports()["P2"]["error"], "")

    def test_malformed_boolean_ids_sequence_and_error_are_rejected(self):
        changes = ({"passed": 1}, {"passed": "true"}, {"player_id": "../P2"},
                   {"task_id": "round/01"}, {"start_request_id": "A" * 32},
                   {"error": "x" * 2001}, {"error": None}, {"report_id": True},
                   {"report_id": 0}, {"report_id": 2**63}, {"player_id": "Ｐ２"})
        for change in changes:
            with self.subTest(change=str(change)[:50]):
                self.assertEqual(self.post(self.payload(**change))[0], 400)
        self.assertEqual(self.post(dict(self.payload(), command="start"))[0], 400)
        self.assertEqual(self.reports(), {})

    def test_body_limit_and_content_type_are_enforced(self):
        self.assertEqual(self.post(None, raw=b" " * 4097)[0], 413)
        self.assertEqual(self.post(self.payload(), {"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.post(None, raw=b"not json")[0], 400)

    def test_audit_errors_do_not_poison_settings_freshness_or_ack(self):
        envelope = self.client.fetch()
        seen = self.client.last_seen_at
        self.assertFalse(self.client.report_audit(self.task, self.nonce, "P2", "true"))
        self.assertIsNotNone(self.client.last_audit_error)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(self.client.last_seen_at, seen)
        self.assertTrue(self.client.acknowledge(envelope["revision"], "P2"))

    def test_result_snapshots_cannot_mutate_server_and_rounds_are_isolated(self):
        self.assertEqual(self.post(self.payload())[0], 200)
        detached = self.reports()
        detached["P2"]["passed"] = True
        self.assertFalse(self.reports()["P2"]["passed"])
        self.assertEqual(self.server.audit_results("round-02", self.nonce), {})
        self.assertEqual(self.server.audit_results(self.task, "b" * 32), {})

    def test_reports_are_bounded_to_256_players_and_128_rounds(self):
        for index in range(256):
            self.assertEqual(self.server._record_audit(self.payload(player_id=f"P{index}"), self.server.instance_id), 200)
        self.assertEqual(self.server._record_audit(self.payload(player_id="overflow"), self.server.instance_id), 429)
        self.assertEqual(len(self.reports()), 256)
        for index in range(128):
            task = f"round-{index + 2}"
            self.server.publish(dict(self.settings, task_id=task))
            self.assertEqual(self.server._record_audit(self.payload(task_id=task), self.server.instance_id), 200)
        self.assertEqual(len(self.server._audit_reports), 128)
        self.assertEqual(self.reports(), {})


if __name__ == "__main__":
    unittest.main()

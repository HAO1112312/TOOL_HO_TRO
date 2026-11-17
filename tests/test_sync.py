"""Settings transport tests use only ephemeral loopback TCP/UDP ports."""
import copy
import json
import math
import time
import unittest
from unittest.mock import patch
from urllib import error, request
import uuid

import odx_sync as sync


class SettingsValidationTests(unittest.TestCase):
    def test_local_identity_paths_credentials_and_commands_are_never_shared(self):
        for key, value in (("player_id", "other"), ("player_name", "other"),
                           ("game_process", "new.exe"), ("odx_root", "C:/other"),
                           ("access_key", "secret"), ("command", "start")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                sync.validate_settings({key: value})

    def test_invalid_numeric_boolean_and_unknown_fields_fail_closed(self):
        invalid = ({"target_duration": True}, {"target_duration": 0},
                   {"target_players": 0}, {"hud_enabled": 1},
                   {"hud_scale": math.inf}, {"round_start_epoch": math.nan},
                   {"round_duration_seconds": 100000}, {"phase": "execute"},
                   {"task_id": "../outside"}, {"sub_task": "a"},
                   {"random_algorithm": "new-unknown"})
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                sync.validate_settings(settings)

    def test_valid_shared_settings_are_copied(self):
        settings = {"task_id": "round-01-fix1", "target_duration": 237,
                    "round_duration_seconds": 240, "random_offset_seconds": 5,
                    "target_players": 4, "cooldown_between_rounds_seconds": 12,
                    "hud_enabled": True, "hud_scale": 1.2,
                    "phase": "countdown", "round_start_epoch": time.time() + 3}
        clean = sync.validate_settings(settings)
        clean["task_id"] = "different"
        self.assertEqual(settings["task_id"], "round-01-fix1")


class SettingsTransportTests(unittest.TestCase):
    def setUp(self):
        self.server = sync.HostSettingsServer("ABC123", "HOST_TEST", bind_host="127.0.0.1",
                                             port=0, discovery_port=0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.server.publish({"task_id": "task-01", "target_duration": 238,
                             "hud_enabled": True, "phase": "idle"})
        self.address = f"127.0.0.1:{self.server.port}"
        self.client = sync.PlayerSettingsClient("ABC123", "HOST_TEST", host_address=self.address,
                                                timeout=.4)

    def test_loopback_player_receives_host_revision_and_only_new_settings(self):
        first = self.client.fetch()
        self.assertEqual(first["settings"]["target_duration"], 238)
        self.assertEqual(first["host_id"], "HOST_TEST")
        self.assertIsNotNone(self.client.last_seen_at)
        self.assertIsNone(self.client.fetch())
        self.assertIsNone(self.client.last_error)
        self.assertEqual(self.server.publish(first["settings"]), first["revision"])
        self.server.publish({"task_id": "task-02", "target_duration": 242,
                             "hud_enabled": False, "phase": "countdown"})
        second = self.client.fetch()
        self.assertGreater(second["revision"], first["revision"])
        self.assertEqual(second["settings"]["task_id"], "task-02")
        self.assertFalse(second["settings"]["hud_enabled"])

    def test_udp_discovery_uses_room_host_and_unicast_response(self):
        client = sync.PlayerSettingsClient("ABC123", "HOST_TEST", timeout=.4,
                                          discovery_port=self.server.discovery_port,
                                          discovery_address="127.0.0.1")
        envelope = client.fetch()
        self.assertIsNotNone(envelope, client.last_error)
        self.assertEqual(envelope["settings"]["task_id"], "task-01")

    def test_fetch_never_acknowledges_until_player_confirms_application(self):
        envelope = self.client.fetch()
        revision = envelope["revision"]
        self.assertEqual(self.server.applied_players(revision), set())
        self.assertTrue(self.client.acknowledge(revision, "PLAYER_1"), self.client.last_error)
        self.assertEqual(self.server.applied_players(revision), {"PLAYER_1"})
        self.assertIsNone(self.client.fetch())
        self.assertTrue(self.client.acknowledge(revision, "PLAYER_1"))

    def test_ack_requires_a_fetched_revision_and_valid_player_identity(self):
        self.assertFalse(self.client.acknowledge(1, "PLAYER_1"))
        envelope = self.client.fetch()
        self.assertFalse(self.client.acknowledge(envelope["revision"] + 1, "PLAYER_1"))
        self.assertFalse(self.client.acknowledge(envelope["revision"], "../other"))
        self.assertEqual(self.server.applied_players(envelope["revision"]), set())

    def test_new_host_settings_invalidate_old_ack_until_player_applies_new_revision(self):
        first = self.client.fetch()
        self.assertTrue(self.client.acknowledge(first["revision"], "PLAYER_1"))
        revision = self.server.publish({"task_id": "task-02", "target_duration": 240})
        self.assertEqual(self.server.applied_players(first["revision"]), set())
        self.assertFalse(self.client.acknowledge(first["revision"], "PLAYER_1"))
        self.assertIn("409", self.client.last_error)
        self.assertEqual(self.server.applied_players(revision), set())
        current = self.client.fetch()
        self.assertTrue(self.client.acknowledge(current["revision"], "PLAYER_1"))
        self.assertEqual(self.server.applied_players(revision), {"PLAYER_1"})

    def test_ack_expiration_stops_counting_a_disconnected_player(self):
        envelope = self.client.fetch()
        self.assertTrue(self.client.acknowledge(envelope["revision"], "PLAYER_1"))
        seen_at = self.server._applied["PLAYER_1"][1]
        with patch.object(sync.time, "monotonic", return_value=seen_at + 11):
            self.assertEqual(self.server.applied_players(envelope["revision"], max_age=10), set())

    def test_new_start_requires_player_ack_after_that_start_request(self):
        envelope = self.client.fetch()
        revision = envelope["revision"]
        self.assertTrue(self.client.acknowledge(revision, "PLAYER_1"))
        old_ack = self.server._applied["PLAYER_1"][1]
        after = old_ack + 1
        with patch.object(sync.time, "monotonic", return_value=after):
            self.assertEqual(self.server.applied_players(revision, after=after), set())
            self.assertTrue(self.client.acknowledge(revision, "PLAYER_1"))
            self.assertEqual(self.server.applied_players(revision, after=after), {"PLAYER_1"})

    def test_same_task_new_start_request_requires_new_revision_and_rejects_inflight_old_ack(self):
        previous = self.client.fetch()
        self.assertTrue(self.client.acknowledge(previous["revision"], "PLAYER_1"))
        settings = dict(previous["settings"], start_request_id="a" * 32)
        current_revision = self.server.publish(settings)
        self.assertGreater(current_revision, previous["revision"])
        self.assertFalse(self.client.acknowledge(previous["revision"], "PLAYER_1"))
        self.assertEqual(self.server.applied_players(current_revision), set())
        current = self.client.fetch()
        self.assertEqual(current["settings"]["task_id"], previous["settings"]["task_id"])
        self.assertTrue(self.client.acknowledge(current["revision"], "PLAYER_1"))
        self.assertEqual(self.server.applied_players(current_revision), {"PLAYER_1"})

    def test_ack_rejects_wrong_scope_instance_extra_fields_and_oversized_body(self):
        envelope = self.client.fetch()
        initial = self.server.snapshot()["settings"]
        valid = {"revision": envelope["revision"], "player_id": "PLAYER_1"}
        cases = (
            ({"X-Odx-Room": "OTHER1"}, valid, 403),
            ({"X-Odx-Host": "OTHER_HOST"}, valid, 403),
            ({"X-Odx-Instance": uuid.uuid4().hex}, valid, 409),
            ({}, {"revision": True, "player_id": "PLAYER_1"}, 400),
            ({}, dict(valid, target_duration=1), 400),
            ({}, {"revision": envelope["revision"], "player_id": "../other"}, 400),
            ({}, "x" * 1025, 413),
        )
        for overrides, payload, status in cases:
            with self.subTest(status=status, overrides=overrides):
                headers = {"X-Odx-Room": "ABC123", "X-Odx-Host": "HOST_TEST",
                           "X-Odx-Instance": self.server.instance_id, "Content-Type": "application/json"}
                headers.update(overrides)
                raw = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                outgoing = request.Request("http://" + self.address + sync.ACK_PATH,
                                           data=raw, headers=headers, method="POST")
                with self.assertRaises(error.HTTPError) as raised:
                    request.urlopen(outgoing, timeout=.4)
                self.assertEqual(raised.exception.code, status)
        self.assertEqual(self.server.applied_players(envelope["revision"]), set())
        self.assertEqual(self.server.snapshot()["settings"], initial)

    def test_wrong_room_or_host_cannot_read_settings(self):
        for room, host in (("OTHER1", "HOST_TEST"), ("ABC123", "OTHER_HOST")):
            client = sync.PlayerSettingsClient(room, host, host_address=self.address, timeout=.4)
            self.assertIsNone(client.fetch())
            self.assertIn("403", client.last_error)

    def test_settings_endpoint_is_read_only(self):
        original = self.server.snapshot()
        outgoing = request.Request("http://" + self.address + sync.SETTINGS_PATH,
                                   data=json.dumps({"target_duration": 1}).encode(),
                                   headers={"X-Odx-Room": "ABC123", "X-Odx-Host": "HOST_TEST"},
                                   method="POST")
        with self.assertRaises(error.HTTPError) as raised:
            request.urlopen(outgoing, timeout=.4)
        self.assertEqual(raised.exception.code, 405)
        self.assertEqual(self.server.snapshot()["settings"], original["settings"])

    def test_invalid_publish_is_atomic_and_snapshots_cannot_mutate_server(self):
        original = self.server.snapshot()
        with self.assertRaises(ValueError):
            self.server.publish({"target_duration": -1})
        snapshot = self.server.snapshot()
        snapshot["settings"]["task_id"] = "modified"
        self.assertEqual(self.server.snapshot()["settings"], original["settings"])
        self.assertEqual(self.server.snapshot()["revision"], original["revision"])

    def test_out_of_order_same_revision_mutation_and_retired_instance_are_rejected(self):
        original = self.client.fetch()
        previous_revision = copy.deepcopy(original)
        self.server.publish({"task_id": "task-02", "target_duration": 243})
        self.assertIsNotNone(self.client.fetch())
        with self.assertRaises(ValueError):
            self.client._accept(previous_revision)

        changed = self.server.snapshot()
        changed["settings"]["task_id"] = "forged-same-revision"
        with self.assertRaises(ValueError):
            self.client._accept(changed)

        restarted = self.server.snapshot()
        restarted["instance_id"] = uuid.uuid4().hex
        restarted["revision"] = 1
        self.assertIsNotNone(self.client._accept(restarted))
        with self.assertRaises(ValueError):
            self.client._accept(self.server.snapshot())

    def test_stale_future_and_foreign_snapshots_are_rejected_without_applying(self):
        original = self.server.snapshot()
        for changes in ({"sent_at": time.time() - 61}, {"sent_at": time.time() + 61},
                        {"room_code": "OTHER1"}, {"host_id": "OTHER_HOST"},
                        {"revision": True}, {"protocol": True}, {"published_at": -1},
                        {"settings": {"player_id": "other"}}):
            candidate = copy.deepcopy(original)
            candidate.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.client._accept(candidate)
        self.assertIsNotNone(self.client.fetch())

    def test_stop_releases_owned_sockets_and_returns_promptly(self):
        started = time.monotonic()
        self.server.stop()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.server._threads, [])
        self.assertIsNone(self.client.fetch())
        self.assertIsNotNone(self.client.last_error)

    def test_manual_host_address_rejects_embedded_credentials_paths_and_redirect_targets(self):
        for address in ("http://name:password@127.0.0.1", "http://127.0.0.1/other",
                        "http://127.0.0.1/?query=1", "file:///etc/passwd", "127.0.0.1:99999"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.client._endpoint(address)


if __name__ == "__main__":
    unittest.main()

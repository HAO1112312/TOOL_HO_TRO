"""Host-authoritative, read-only settings synchronization on a reachable network.

UDP discovery works on a LAN; a reachable ``host_address`` is required across
networks that block broadcasts or use NAT. This module never changes native Odx
settings, sends capture commands, opens firewall rules, or copies local identity.
The native room code and host ID scope messages; they are not Internet passwords.
"""
import copy
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
import socket
import threading
import time
from urllib import error, parse, request
import uuid


PROTOCOL = 1
HTTP_PORT = 45455
DISCOVERY_PORT = 45454
MAX_PAYLOAD = 32768
SETTINGS_PATH = "/odx-sync/v1/settings"
ACK_PATH = "/odx-sync/v1/ack"
AUDIT_PATH = "/odx-sync/v1/audit"
MAX_AUDIT_PAYLOAD = 4096
MAX_AUDIT_ERROR = 2000
_TASK = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ROOM = re.compile(r"[A-Z0-9]{6}\Z")
_PHASES = frozenset(("idle", "preparing", "countdown", "recording", "stopping",
                     "validating", "choices", "cooldown", "complete", "error"))
_BOOL_KEYS = frozenset(("auto_increment", "random_enabled", "test_mode", "hud_enabled",
                       "hud_show_timer", "hud_show_task", "hud_show_roster",
                       "action_prompt_enabled", "record_audio"))
_INTEGER_RANGES = {
    "start_num": (1, 1000000), "total_tasks": (1, 1000000),
    "round_duration_seconds": (1, 3600), "random_offset_seconds": (0, 60),
    "target_duration": (1, 3660), "target_players": (1, 16),
    "cooldown_between_rounds_seconds": (0, 600),
}
_FLOAT_RANGES = {"hud_scale": (.5, 2), "hud_opacity": (.2, 1)}
_ID_KEYS = frozenset(("task_id", "current_batch", "base_task_id",
                     "pending_fix_task", "pending_next_task"))


def _scope(room_code, host_id):
    room = str(room_code).strip().upper()
    host = str(host_id).strip()
    if not _ROOM.fullmatch(room) or not host or len(host) > 128 or any(ord(c) < 32 for c in host):
        raise ValueError("A valid native room code and host ID are required")
    return room, host


def validate_settings(settings):
    """Copy only recognized shared fields; local paths/identity never pass."""
    if not isinstance(settings, dict):
        raise ValueError("Shared settings must be an object")
    result = {}
    for key, value in settings.items():
        valid = False
        if key in _BOOL_KEYS:
            valid = type(value) is bool
        elif key == "action_prompt_at_seconds":
            valid = value is None or (type(value) is int and 125 <= value <= 130)
        elif key in _INTEGER_RANGES:
            low, high = _INTEGER_RANGES[key]
            valid = type(value) is int and low <= value <= high
        elif key in _FLOAT_RANGES:
            low, high = _FLOAT_RANGES[key]
            valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
        elif key in _ID_KEYS:
            valid = isinstance(value, str) and (not value or bool(_TASK.fullmatch(value)))
        elif key == "sub_task":
            valid = isinstance(value, str) and bool(re.fullmatch(r"\d{1,8}", value, flags=re.ASCII))
        elif key == "phase":
            valid = isinstance(value, str) and value in _PHASES
        elif key == "round_start_epoch":
            valid = value is None or (type(value) in (int, float) and math.isfinite(value) and 0 < value < 100000000000)
        elif key == "random_algorithm":
            valid = value == "task-md5-v1"
        elif key == "hud_language":
            valid = value in ("vi", "en")
        elif key == "game_selection":
            valid = value in ("arma3", "arma-reforger")
        elif key == "app_version":
            valid = isinstance(value, str) and bool(re.fullmatch(r"\d{1,4}(?:\.\d{1,4}){1,3}", value))
        elif key == "start_request_id":
            valid = value is None or (isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{32}", value)))
        if not valid:
            raise ValueError(f"Invalid or unshared setting: {key}")
        result[key] = value
    return result


class HostSettingsServer:
    """Publish snapshots only; HTTP peers cannot change the Host's settings."""

    def __init__(self, room_code, host_id, bind_host="0.0.0.0", port=HTTP_PORT,
                 discovery_port=DISCOVERY_PORT):
        self.room_code, self.host_id = _scope(room_code, host_id)
        self.bind_host, self.port = bind_host, int(port)
        self.discovery_port = int(discovery_port)
        self.instance_id = uuid.uuid4().hex
        self.last_error = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._http = self._udp = None
        self._threads = []
        self._settings, self._revision, self._published_at = {}, 0, time.time()
        self._applied = {}
        self._audit_reports = OrderedDict()

    @property
    def address(self):
        return (self.bind_host, self.port)

    def publish(self, settings):
        clean = validate_settings(settings)
        with self._lock:
            if self._revision == 0 or clean != self._settings:
                self._settings = clean
                self._revision += 1
                self._published_at = time.time()
                self._applied.clear()
            return self._revision

    def applied_players(self, revision, max_age=10, after=None):
        """Return recently acknowledged players for this exact settings revision."""
        if type(revision) is not int or revision < 1:
            return set()
        now = time.monotonic()
        age = max(0, float(max_age))
        with self._lock:
            return {player for player, (applied_revision, seen_at) in self._applied.items()
                    if applied_revision == revision == self._revision and 0 <= now - seen_at <= age
                    and (after is None or seen_at >= after)}

    def _acknowledge(self, revision, player_id, instance_id):
        if (type(revision) is not int or revision < 1 or not isinstance(player_id, str)
                or not _TASK.fullmatch(player_id)):
            return 400
        with self._lock:
            if revision != self._revision or instance_id != self.instance_id:
                return 409
            if player_id not in self._applied and len(self._applied) >= 256:
                return 429
            self._applied[player_id] = (revision, time.monotonic())
        return 200

    def audit_results(self, task_id, start_request_id):
        """Return a detached snapshot of local Player verdicts for one round."""
        if (not isinstance(task_id, str) or not _TASK.fullmatch(task_id)
                or not isinstance(start_request_id, str)
                or not re.fullmatch(r"[0-9a-f]{32}", start_request_id)):
            return {}
        with self._lock:
            return copy.deepcopy(self._audit_reports.get((task_id, start_request_id), {}))

    def _record_audit(self, payload, instance_id):
        fields = {"task_id", "start_request_id", "player_id", "passed", "error", "report_id"}
        if not isinstance(payload, dict) or set(payload) != fields:
            return 400
        task, nonce, player = (payload[key] for key in ("task_id", "start_request_id", "player_id"))
        passed, detail, sequence = (payload[key] for key in ("passed", "error", "report_id"))
        if (not isinstance(task, str) or not _TASK.fullmatch(task)
                or not isinstance(player, str) or not _TASK.fullmatch(player)
                or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce)
                or type(passed) is not bool or not isinstance(detail, str) or len(detail) > MAX_AUDIT_ERROR
                or type(sequence) is not int or not 1 <= sequence <= 2**63 - 1):
            return 400
        with self._lock:
            if (instance_id != self.instance_id or task != self._settings.get("task_id")
                    or nonce != self._settings.get("start_request_id")):
                return 409
            key = task, nonce
            reports = self._audit_reports.get(key)
            previous = reports.get(player) if reports is not None else None
            if previous is not None:
                if sequence < previous["report_id"]:
                    return 409
                if sequence == previous["report_id"]:
                    return 200 if all(previous[field] == payload[field] for field in fields) else 409
            if previous is None and reports is not None and len(reports) >= 256:
                return 429
            if reports is None:
                reports = self._audit_reports[key] = {}
            reports[player] = dict(payload, received_at=time.monotonic())
            self._audit_reports.move_to_end(key)
            while len(self._audit_reports) > 128:
                self._audit_reports.popitem(last=False)
        return 200

    def snapshot(self):
        with self._lock:
            return dict(protocol=PROTOCOL, room_code=self.room_code, host_id=self.host_id,
                        instance_id=self.instance_id, revision=self._revision,
                        published_at=self._published_at, sent_at=time.time(),
                        settings=copy.deepcopy(self._settings))

    def start(self):
        if self._http is not None:
            return self
        self.last_error = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass

            def scoped(self):
                return (self.headers.get("X-Odx-Room") == owner.room_code
                        and self.headers.get("X-Odx-Host") == owner.host_id)

            def send_json(self, value):
                body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != SETTINGS_PATH:
                    self.send_error(404)
                    return
                if not self.scoped():
                    self.send_error(403)
                    return
                self.send_json(owner.snapshot())

            def do_POST(self):
                if self.path not in (ACK_PATH, AUDIT_PATH):
                    self.send_error(405)
                    return
                if not self.scoped():
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > (MAX_AUDIT_PAYLOAD if self.path == AUDIT_PATH else 1024):
                        self.send_error(413)
                        return
                    if length < 1 or self.headers.get_content_type() != "application/json":
                        self.send_error(400)
                        return
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if self.path == AUDIT_PATH:
                        status = owner._record_audit(payload, self.headers.get("X-Odx-Instance"))
                        if status != 200:
                            self.send_error(status)
                            return
                        self.send_json(dict(ok=True, instance_id=owner.instance_id,
                                            **{key: payload[key] for key in
                                               ("task_id", "start_request_id", "player_id", "report_id")}))
                        return
                    if not isinstance(payload, dict) or set(payload) != {"revision", "player_id"}:
                        self.send_error(400)
                        return
                    status = owner._acknowledge(payload["revision"], payload["player_id"],
                                                self.headers.get("X-Odx-Instance"))
                    if status != 200:
                        self.send_error(status)
                        return
                    self.send_json(dict(ok=True, revision=payload["revision"], instance_id=owner.instance_id))
                except (OSError, ValueError, UnicodeError):
                    self.send_error(400)

            def do_PUT(self):
                self.send_error(405)

            do_DELETE = do_PATCH = do_PUT

        self._stop.clear()
        try:
            self._http = ThreadingHTTPServer((self.bind_host, self.port), Handler)
            self._http.daemon_threads = True
            self._http.block_on_close = False
            self.port = self._http.server_address[1]
            http_thread = threading.Thread(target=self._http.serve_forever,
                                           kwargs={"poll_interval": .1}, daemon=True)
            http_thread.start()
            self._threads.append(http_thread)
            try:
                self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._udp.bind((self.bind_host, self.discovery_port))
                self.discovery_port = self._udp.getsockname()[1]
                self._udp.settimeout(.2)
                udp_thread = threading.Thread(target=self._discover_loop, daemon=True)
                udp_thread.start()
                self._threads.append(udp_thread)
            except OSError as exc:
                self.last_error = f"UDP discovery unavailable; use the Host address: {exc}"
                if self._udp is not None:
                    self._udp.close()
                    self._udp = None
        except BaseException:
            self.stop()
            raise
        return self

    def _discover_loop(self):
        udp = self._udp
        while not self._stop.is_set():
            try:
                data, address = udp.recvfrom(2048)
                message = json.loads(data.decode("utf-8"))
                if (not isinstance(message, dict) or type(message.get("protocol")) is not int
                        or message.get("protocol") != PROTOCOL
                        or message.get("type") != "discover" or message.get("room_code") != self.room_code
                        or message.get("host_id") != self.host_id
                        or not re.fullmatch(r"[0-9a-f]{32}", str(message.get("nonce", "")))):
                    continue
                response = dict(protocol=PROTOCOL, type="host", room_code=self.room_code,
                                host_id=self.host_id, port=self.port, instance_id=self.instance_id,
                                nonce=message["nonce"])
                udp.sendto(json.dumps(response).encode("utf-8"), address)
            except socket.timeout:
                continue
            except (OSError, ValueError, UnicodeError):
                if self._stop.is_set():
                    break

    def stop(self):
        self._stop.set()
        http, udp = self._http, self._udp
        self._http = self._udp = None
        if udp is not None:
            udp.close()
        if http is not None:
            http.shutdown()
            http.server_close()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1)
        self._threads.clear()


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


class PlayerSettingsClient:
    @property
    def current_revision(self):
        return self._revision

    @property
    def instance_id(self):
        return self._instance_id

    def __init__(self, room_code, host_id, host_address=None, discovery_port=DISCOVERY_PORT,
                 timeout=1.5, discovery_address="255.255.255.255"):
        self.room_code, self.host_id = _scope(room_code, host_id)
        self.host_address = host_address or None
        self.discovery_port, self.discovery_address = int(discovery_port), discovery_address
        self.timeout = min(5, max(.1, float(timeout)))
        self.last_error, self.last_seen_at = None, None
        self._instance_id, self._revision, self._last_settings = None, -1, None
        self._last_address = None
        self._retired_instances = set()
        self._audit_lock = threading.RLock()
        self._audit_sequence = 0
        self.last_audit_error = None
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _endpoint(address):
        text = str(address).strip()
        parsed = parse.urlsplit(text if "://" in text else "http://" + text)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("Enter only the reachable Host address and port")
        port = parsed.port or HTTP_PORT
        if not 1 <= port <= 65535:
            raise ValueError("Invalid Host port")
        host = parsed.hostname
        if any(c.isspace() or ord(c) < 32 for c in host):
            raise ValueError("Invalid Host address")
        if ":" in host:
            host = "[" + host + "]"
        return f"{parsed.scheme}://{host}:{port}{SETTINGS_PATH}"

    def discover(self):
        nonce = uuid.uuid4().hex
        message = dict(protocol=PROTOCOL, type="discover", room_code=self.room_code,
                       host_id=self.host_id, nonce=nonce)
        deadline = time.monotonic() + self.timeout
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp.settimeout(self.timeout)
            udp.sendto(json.dumps(message).encode("utf-8"), (self.discovery_address, self.discovery_port))
            while time.monotonic() < deadline:
                udp.settimeout(max(.01, deadline - time.monotonic()))
                try:
                    data, address = udp.recvfrom(2048)
                    response = json.loads(data.decode("utf-8"))
                    port = response.get("port") if isinstance(response, dict) else None
                    if (type(response.get("protocol")) is int and response.get("protocol") == PROTOCOL
                            and response.get("type") == "host"
                            and response.get("room_code") == self.room_code and response.get("host_id") == self.host_id
                            and response.get("nonce") == nonce and type(port) is int and 1 <= port <= 65535):
                        return f"{address[0]}:{port}"
                except (ValueError, UnicodeError, AttributeError):
                    continue
        raise TimeoutError("No settings Host found; enter its reachable address")

    def _accept(self, envelope):
        if (not isinstance(envelope, dict) or type(envelope.get("protocol")) is not int
                or envelope.get("protocol") != PROTOCOL
                or envelope.get("room_code") != self.room_code or envelope.get("host_id") != self.host_id):
            raise ValueError("Settings belong to another room or Host")
        instance = envelope.get("instance_id")
        revision, sent_at, published_at = (envelope.get(key) for key in ("revision", "sent_at", "published_at"))
        if not isinstance(instance, str) or not re.fullmatch(r"[0-9a-f]{32}", instance):
            raise ValueError("Invalid Host instance")
        if type(revision) is not int or revision < 1:
            raise ValueError("Waiting for Host settings")
        if (type(sent_at) not in (int, float) or not math.isfinite(sent_at) or abs(time.time() - sent_at) > 60
                or type(published_at) not in (int, float) or not math.isfinite(published_at)
                or published_at <= 0 or published_at > sent_at + 1):
            raise ValueError("Stale settings response or unsynchronized clock")
        settings = validate_settings(envelope.get("settings"))
        if instance in self._retired_instances:
            raise ValueError("Settings from an earlier Host instance were ignored")
        if instance == self._instance_id:
            if revision < self._revision or (revision == self._revision and settings != self._last_settings):
                raise ValueError("Out-of-order settings revision was ignored")
            if revision == self._revision:
                self.last_seen_at, self.last_error = time.monotonic(), None
                return None
        elif self._instance_id is not None:
            self._retired_instances.add(self._instance_id)
        self._instance_id, self._revision, self._last_settings = instance, revision, settings
        self.last_seen_at, self.last_error = time.monotonic(), None
        clean = {key: envelope[key] for key in ("protocol", "room_code", "host_id", "instance_id", "revision", "published_at", "sent_at")}
        clean["settings"] = settings
        return clean

    def fetch(self):
        """Return a changed validated snapshot, or None; inspect last_error."""
        try:
            address = self.host_address or self.discover()
            outgoing = request.Request(self._endpoint(address), headers={
                "X-Odx-Room": self.room_code, "X-Odx-Host": self.host_id,
                "Accept": "application/json"})
            with self._opener.open(outgoing, timeout=self.timeout) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("Host did not return JSON settings")
                body = response.read(MAX_PAYLOAD + 1)
                if len(body) > MAX_PAYLOAD:
                    raise ValueError("Settings response is too large")
                envelope = json.loads(body.decode("utf-8"))
            accepted = self._accept(envelope)
            self._last_address = address
            return accepted
        except (OSError, ValueError, UnicodeError, error.URLError) as exc:
            self.last_error = str(exc)
            return None

    def acknowledge(self, revision, player_id):
        """Acknowledge only the snapshot that the caller has actually applied."""
        try:
            if (type(revision) is not int or revision != self._revision or self._last_address is None
                    or not isinstance(player_id, str) or not _TASK.fullmatch(player_id)):
                raise ValueError("Fetch and apply this settings revision before acknowledging it")
            endpoint = self._endpoint(self._last_address)
            endpoint = endpoint[:-len(SETTINGS_PATH)] + ACK_PATH
            body = json.dumps(dict(revision=revision, player_id=player_id)).encode("utf-8")
            outgoing = request.Request(endpoint, data=body, method="POST", headers={
                "X-Odx-Room": self.room_code, "X-Odx-Host": self.host_id,
                "X-Odx-Instance": self._instance_id, "Content-Type": "application/json"})
            with self._opener.open(outgoing, timeout=self.timeout) as response:
                raw = response.read(1025)
                if response.headers.get_content_type() != "application/json" or len(raw) > 1024:
                    raise ValueError("Invalid settings acknowledgement response")
                result = json.loads(raw.decode("utf-8"))
                if (not isinstance(result, dict) or result.get("ok") is not True
                        or result.get("revision") != revision or result.get("instance_id") != self._instance_id):
                    raise ValueError("Settings acknowledgement did not match this Host revision")
            self.last_error = None
            return True
        except (OSError, ValueError, UnicodeError, error.URLError) as exc:
            self.last_error = str(exc)
            return False

    def report_audit(self, task_id, start_request_id, player_id, passed, error=""):
        """Report a completed local audit; settings and their ACK stay separate.

        The caller must discard obsolete audit generations before submitting.
        Serialized monotonic report IDs reject delayed network deliveries while
        allowing a newer explicit recheck to replace an earlier FAIL.
        """
        with self._audit_lock:
            try:
                settings = dict(self._last_settings or {})
                instance, address = self._instance_id, self._last_address
                if (not isinstance(task_id, str) or not _TASK.fullmatch(task_id)
                        or not isinstance(player_id, str) or not _TASK.fullmatch(player_id)
                        or not isinstance(start_request_id, str)
                        or not re.fullmatch(r"[0-9a-f]{32}", start_request_id)
                        or type(passed) is not bool or not isinstance(error, str) or len(error) > MAX_AUDIT_ERROR):
                    raise ValueError("Invalid local audit verdict")
                if (address is None or instance is None or task_id != settings.get("task_id")
                        or start_request_id != settings.get("start_request_id")):
                    raise ValueError("Fetch this Host round before reporting its local audit")
                sequence = max(self._audit_sequence + 1, time.monotonic_ns())
                if not 1 <= sequence <= 2**63 - 1:
                    raise ValueError("Invalid local audit report sequence")
                self._audit_sequence = sequence
                payload = dict(task_id=task_id, start_request_id=start_request_id, player_id=player_id,
                               passed=passed, error=error, report_id=sequence)
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                if len(body) > MAX_AUDIT_PAYLOAD:
                    raise ValueError("Local audit report is too large")
                endpoint = self._endpoint(address)[:-len(SETTINGS_PATH)] + AUDIT_PATH
                outgoing = request.Request(endpoint, data=body, method="POST", headers={
                    "X-Odx-Room": self.room_code, "X-Odx-Host": self.host_id,
                    "X-Odx-Instance": instance, "Content-Type": "application/json"})
                with self._opener.open(outgoing, timeout=self.timeout) as response:
                    raw = response.read(1025)
                    if response.headers.get_content_type() != "application/json" or len(raw) > 1024:
                        raise ValueError("Invalid local audit acknowledgement")
                    result = json.loads(raw.decode("utf-8"))
                    if (not isinstance(result, dict) or result.get("ok") is not True
                            or result.get("instance_id") != instance
                            or any(result.get(key) != payload[key] for key in
                                   ("task_id", "start_request_id", "player_id", "report_id"))
                            or self._instance_id != instance
                            or (self._last_settings or {}).get("task_id") != task_id
                            or (self._last_settings or {}).get("start_request_id") != start_request_id):
                        raise ValueError("Local audit acknowledgement belongs to another round")
                self.last_audit_error = None
                return True
            except (OSError, ValueError, UnicodeError) as exc:
                self.last_audit_error = str(exc)
                return False

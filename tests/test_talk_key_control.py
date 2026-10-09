"""control.sock use in scripts/minitoo-talk-key.py, against a fake Hermes Gadget control server."""

import importlib.util
import json
import os
import pathlib
import shutil
import socket
import tempfile
import threading
import time

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "minitoo-talk-key.py"
_sp = importlib.util.spec_from_file_location("talk_key_control", _p)
K = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(K)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "CFG", K.load_settings({"HOME": str(tmp_path)}, {}))
    monkeypatch.setattr(K, "SHUTDOWN", threading.Event())


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(K, "log", out.append)
    return out


@pytest.fixture
def state_dir():
    # AF_UNIX paths are limited to ~108 bytes, so not under a deep tmp_path
    d = tempfile.mkdtemp(prefix="tk-", dir="/tmp" if os.path.isdir("/tmp") else None)
    K.CFG.state_dir = d
    yield d
    shutil.rmtree(d, ignore_errors=True)


class FakeGadget:
    """Like hermes_gadget.linux.control.ControlServer: one JSON line in, one JSON line out."""

    def __init__(self, directory, handler):
        self.requests, self.handler = [], handler
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(os.path.join(directory, "control.sock"))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.closed = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(2)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                request = json.loads(data.split(b"\n", 1)[0])
                self.requests.append(request)
                reply = self.handler(request)
                if reply is None:
                    time.sleep(1)  # never answers: the client must time out
                else:
                    conn.sendall(json.dumps(reply).encode() + b"\n")

    def close(self):
        self.closed = True
        self.thread.join(2)
        self.sock.close()


@pytest.fixture
def gadget(state_dir):
    servers = []

    def start(handler=lambda request: {"ok": True}):
        servers.append(FakeGadget(state_dir, handler))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


@pytest.fixture
def cli(monkeypatch):
    calls = []
    monkeypatch.setattr(K, "_button_cli", lambda state, which="talk": calls.append((which, state)) or True)
    return calls


def test_control_request_speaks_the_gadget_socket_protocol(gadget):
    server = gadget(lambda request: {"screen": "ready", "phase": "online"})
    assert K.control_request({"command": "status"}) == {"screen": "ready", "phase": "online"}
    assert server.requests == [{"command": "status"}]


def test_socket_control_sends_talk_press_and_release(gadget, cli):
    K.CFG.control = "socket"
    server = gadget()
    assert K.button("press") is True
    assert K.button("release") is True
    assert server.requests == [{"command": "button", "button": "talk", "pressed": True},
                               {"command": "button", "button": "talk", "pressed": False}]
    assert cli == []


def test_cli_is_the_default_even_when_the_socket_exists(gadget, cli):
    server = gadget()
    assert K.button("press") is True
    assert cli == [("talk", "press")] and server.requests == []


def test_socket_control_falls_back_to_the_cli_only_without_a_socket(state_dir, cli, logs):
    K.CFG.control = "socket"
    assert K.button("press") is True  # no control.sock at all
    assert cli == [("talk", "press")]
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(os.path.join(state_dir, "control.sock"))  # a socket file nobody listens on
    stale.close()
    assert K.button("release") is True
    assert cli == [("talk", "press"), ("talk", "release")]


def test_socket_timeout_is_not_retried_through_the_cli(gadget, cli, logs, monkeypatch):
    K.CFG.control = "socket"
    monkeypatch.setattr(K, "CONTROL_TIMEOUT_S", 0.3)
    server = gadget(lambda request: None)
    t0 = time.monotonic()
    assert K.button("press") is False
    assert time.monotonic() - t0 < 1.0
    assert cli == [] and len(server.requests) == 1
    assert "failed" in logs[-1]


def test_socket_error_reply_is_a_failure(gadget, cli, logs):
    K.CFG.control = "socket"
    gadget(lambda request: {"error": "button must be talk, cancel, up or down"})
    assert K.button("press") is False
    assert cli == [] and "button must be" in logs[-1]


def test_status_is_logged_read_only_in_the_default_mode(gadget, logs):
    server = gadget(lambda request: {"screen": "listening", "phase": "online",
                                     "audio": {"input": "x", "errors": {"input": "overflow"}}})
    K._log_status_async("press").join(2)
    assert server.requests == [{"command": "status"}]
    assert logs == ["gadget[press]: screen=listening phase=online audio_errors={'input': 'overflow'}"]


def test_status_is_skipped_silently_without_a_socket(state_dir, logs):
    K._log_status_async("vad-end").join(2)
    assert logs == []


def test_cancel_needs_socket_control(logs):
    s = K.load_settings({"HOME": "/h", "MINITOO_PRESS_DURING_START": "cancel"}, {})
    assert s.press_during_start == "ignored"
    assert "control=socket" in logs[-1]
    K.CFG = s
    st = {"phase": "starting", "end": 0.0, "stop": threading.Event(), "started": time.time() - 5}
    assert K.on_press(st) == "ignored" and not st["stop"].is_set()
    s = K.load_settings({"HOME": "/h", "MINITOO_PRESS_DURING_START": "cancel",
                         "MINITOO_CONTROL": "socket"}, {})
    assert s.press_during_start == "cancel"


def test_press_while_starting_can_cancel_opt_in():
    K.CFG.control, K.CFG.press_during_start = "socket", "cancel"
    st = {"phase": "starting", "end": 0.0, "stop": threading.Event(), "started": time.time() - 2}
    assert K.on_press(st) == "cancel"
    assert st["stop"].is_set() and st["cancel"]
    st = {"phase": "starting", "end": 0.0, "stop": threading.Event(), "started": time.time()}
    assert K.on_press(st) == "ignored"  # a duplicate AVRCP event right after the start


def _fake_media(monkeypatch, calls):
    monkeypatch.setattr(K, "_log_status_async", lambda tag: None)  # keeps the request order fixed
    monkeypatch.setattr(K, "play_prompt", lambda: calls.append("play_prompt"))
    monkeypatch.setattr(K, "_profile", lambda address=None: "headset-head-unit")
    monkeypatch.setattr(K, "vad_wait", lambda stop: calls.append("vad_wait") or "кнопка")


def test_cancel_sends_cancel_press_and_release_back_to_back_then_releases_talk(gadget, monkeypatch):
    K.CFG.control, K.CFG.press_during_start = "socket", "cancel"
    server = gadget(lambda r: {"screen": "listening"} if r["command"] == "status" else {"ok": True})
    calls = []
    _fake_media(monkeypatch, calls)
    st = {"phase": "idle", "end": 0.0, "stop": threading.Event(), "cancel": True}
    st["stop"].set()
    K.record_once(st)
    assert server.requests == [
        {"command": "button", "button": "talk", "pressed": True},
        {"command": "status"},
        {"command": "button", "button": "cancel", "pressed": True},
        {"command": "button", "button": "cancel", "pressed": False},
        {"command": "button", "button": "talk", "pressed": False},
    ]
    assert calls == []  # no prompt, no pw-record


def test_cancel_is_not_sent_when_the_gadget_is_not_listening(gadget, monkeypatch, logs):
    K.CFG.control, K.CFG.press_during_start = "socket", "cancel"
    server = gadget(lambda r: {"screen": "thinking"} if r["command"] == "status" else {"ok": True})
    _fake_media(monkeypatch, [])
    st = {"phase": "idle", "end": 0.0, "stop": threading.Event(), "cancel": True}
    K.record_once(st)
    assert [r.get("button") for r in server.requests] == ["talk", None, "talk"]
    assert any("cancel не шлю" in line for line in logs)


def test_cancel_release_is_retried(monkeypatch, logs):
    sent = []

    def control(message, timeout=None):
        if message["command"] == "status":
            return {"screen": "listening"}
        sent.append(message["pressed"])
        if len(sent) == 2:
            raise TimeoutError("timed out")
        return {"ok": True}

    monkeypatch.setattr(K, "control_request", control)
    assert K.cancel_listening() is True
    assert sent == [True, False, False]  # a second cancel release is a no-op in the core

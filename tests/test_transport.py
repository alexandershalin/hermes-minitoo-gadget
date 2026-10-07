import socket

import pytest

from hermes_minitoo import transport
from hermes_minitoo.transport import RFCOMMTransport, _open_rfcomm


def test_open_rfcomm_uses_native_socket(monkeypatch):
    calls = {}

    class FakeSock:
        def settimeout(self, t):
            calls["timeout"] = t

        def connect(self, addr):
            calls["addr"] = addr

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(socket, "AF_BLUETOOTH", 31, raising=False)
    monkeypatch.setattr(socket, "BTPROTO_RFCOMM", 3, raising=False)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    sock = _open_rfcomm("AA:BB:CC:DD:EE:FF", 1, 5.0)
    assert isinstance(sock, FakeSock)
    assert calls["addr"] == ("AA:BB:CC:DD:EE:FF", 1)
    assert calls["timeout"] == 5.0


def test_open_rfcomm_native_failure_closes_socket(monkeypatch):
    closed = []

    class FakeSock:
        def settimeout(self, t):
            pass

        def connect(self, addr):
            raise ConnectionRefusedError

        def close(self):
            closed.append(True)

    monkeypatch.setattr(socket, "AF_BLUETOOTH", 31, raising=False)
    monkeypatch.setattr(socket, "BTPROTO_RFCOMM", 3, raising=False)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    with pytest.raises(ConnectionRefusedError):
        _open_rfcomm("AA:BB:CC:DD:EE:FF", 1, 5.0)
    assert closed == [True]


def test_open_rfcomm_ctypes_fallback_reports_errno(monkeypatch):
    monkeypatch.delattr(socket, "AF_BLUETOOTH", raising=False)
    monkeypatch.delattr(socket, "BTPROTO_RFCOMM", raising=False)

    class FakeLibc:
        def socket(self, *a):
            return -1

    monkeypatch.setattr("ctypes.CDLL", lambda *a, **k: FakeLibc())
    with pytest.raises(OSError):
        _open_rfcomm("AA:BB:CC:DD:EE:FF", 1, 5.0)


def test_connect_failure_sets_backoff(monkeypatch):
    def boom(*a, **k):
        raise ConnectionRefusedError

    monkeypatch.setattr(transport, "_open_rfcomm", boom)
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF", reconnect_delay_ms=60000)
    with pytest.raises(ConnectionRefusedError):
        t.connect()
    assert t.sock is None
    with pytest.raises(ConnectionError, match="backoff"):
        t.connect()

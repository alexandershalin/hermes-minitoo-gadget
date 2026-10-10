import socket

import pytest

from hermes_minitoo.platforms.linux import transport
from hermes_minitoo.platforms.linux.transport import RFCOMMTransport, _open_rfcomm


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


def test_first_connect_is_not_blocked_by_low_monotonic_clock(monkeypatch):
    # A freshly booted host has time.monotonic() < reconnect_delay; the first attempt must still go through.
    monkeypatch.setattr(transport.time, "monotonic", lambda: 5.0)
    opened = []
    monkeypatch.setattr(transport, "_open_rfcomm", lambda *a, **k: opened.append(1) or object())
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF", reconnect_delay_ms=60000)
    t.connect()
    assert opened == [1]


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


# ---- wire behaviour, ACK timeout handling, abort ----

READY = bytes.fromhex("01 07 00 04 8b 55 00 01 ec 00 02")
OTHER = bytes.fromhex("01 05 00 04 99 00 a2 00 02")


class ScriptedSock:
    """Records what the transport does; recv() replays a script (bytes or exceptions)."""

    def __init__(self, reads=(), clock=None, recv_cost=0.0):
        self.reads = list(reads)
        self.ops = []
        self.clock = clock
        self.recv_cost = recv_cost

    def settimeout(self, t):
        self.ops.append(("settimeout", round(t, 3)))

    def sendall(self, data):
        self.ops.append(("sendall", bytes(data)))

    def recv(self, n):
        self.ops.append(("recv", n))
        if self.clock is not None:
            self.clock.t += self.recv_cost
        item = self.reads.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def shutdown(self, how):
        self.ops.append(("shutdown", how))

    def close(self):
        self.ops.append(("close",))


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _wire_run(monkeypatch, reads, **send_kwargs):
    """send_rgb888 through a scripted socket; returns (ops, sleeps, connect timeouts)."""
    clock = FakeClock()
    sock = ScriptedSock(reads, clock, recv_cost=0.05)
    opened = []

    def fake_open(address, channel, timeout):
        opened.append((address, channel, timeout))
        return sock

    monkeypatch.setattr(transport, "time", clock)
    monkeypatch.setattr(transport, "_open_rfcomm", fake_open)
    monkeypatch.setattr(transport, "build_lossless_animation",
                        lambda rgb, frame_delay_ms: bytes(range(256)) * 2 + b"tail")
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF", chunk_delay_ms=5, ready_timeout_ms=8000)
    error = None
    try:
        t.send_rgb888(b"\x00" * 30, **send_kwargs)
    except Exception as exc:  # returned for the assertions
        error = exc
    return sock.ops, clock.sleeps, opened, error, t


def test_send_puts_the_same_bytes_on_the_wire(monkeypatch):
    from hermes_minitoo.protocol import build_transfer

    ops, sleeps, opened, error, t = _wire_run(monkeypatch, [OTHER, READY])
    assert error is None
    transfer = build_transfer(bytes(range(256)) * 2 + b"tail")
    assert [data for op, *data in ops if op == "sendall"] == [[transfer.start]] + [
        [chunk] for chunk in transfer.chunks]
    assert sleeps == [0.005, 0.005]  # between the 3 chunks, as before
    assert opened == [("AA:BB:CC:DD:EE:FF", 1, 8.0)]  # connect timeout max(1, ready)
    assert [op for op, *_ in ops].count("recv") == 2  # nothing is read after the ACK
    assert t.sock is not None


def test_chunk_writes_do_not_inherit_the_leftover_ack_deadline(monkeypatch):
    ops, *_ = _wire_run(monkeypatch, [OTHER, READY])
    first_chunk = next(i for i, op in enumerate(ops) if op[0] == "sendall" and i > 0)
    timeouts = [op[1] for op in ops[:first_chunk] if op[0] == "settimeout"]
    assert timeouts == [8.0, 7.95, 8.0]  # remaining, remaining, then restored


def test_timeout_is_restored_when_the_ack_wait_fails(monkeypatch):
    ops, _, _, error, t = _wire_run(monkeypatch, [OTHER, TimeoutError("timed out")])
    assert isinstance(error, TimeoutError)
    assert ops[-2:] == [("settimeout", 8.0), ("close",)]
    assert t.sock is None


def test_ready_timeout_override_is_used_once(monkeypatch):
    ops, _, opened, error, _ = _wire_run(monkeypatch, [READY], ready_timeout=1.5)
    assert error is None
    assert [op[1] for op in ops if op[0] == "settimeout"] == [1.5, 8.0]
    assert opened[0][2] == 8.0  # the connect timeout is unchanged


def test_ready_ack_latency_is_logged(monkeypatch, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger=transport.LOG.name)
    _wire_run(monkeypatch, [READY])
    fast = [r for r in caplog.records if "ready ACK in" in r.getMessage()]
    assert fast[-1].levelno == logging.DEBUG and fast[-1].getMessage() == "MiniToo ready ACK in 50 ms"
    caplog.clear()
    _wire_run(monkeypatch, [OTHER] * 24 + [READY])  # 25 reads x 50 ms
    slow = [r for r in caplog.records if "ready ACK in" in r.getMessage()]
    assert slow[-1].levelno == logging.INFO and slow[-1].getMessage() == "MiniToo ready ACK in 1250 ms"


def test_abort_shuts_down_the_socket_and_refuses_reconnects():
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF")
    sock = ScriptedSock()
    t.sock = sock
    t.abort()
    assert ("shutdown", socket.SHUT_RDWR) in sock.ops
    t.close()
    with pytest.raises(ConnectionError, match="shutting down"):
        t.connect()


def test_abort_during_connect_closes_the_new_socket(monkeypatch):
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF")
    sock = ScriptedSock()

    def slow_open(*args):
        t.abort()  # close() ran in another thread while we were connecting
        return sock

    monkeypatch.setattr(transport, "_open_rfcomm", slow_open)
    with pytest.raises(ConnectionError, match="shutting down"):
        t.connect()
    assert ("close",) in sock.ops and t.sock is None


def test_abort_without_a_socket_is_harmless():
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF")
    t.abort()
    t.close()


@pytest.mark.parametrize("address", ["AA:BB:CC", "AA:BB:CC:DD:EE:FF:00"])
def test_ctypes_fallback_rejects_a_bad_address_before_creating_a_socket(monkeypatch, address):
    monkeypatch.delattr(socket, "AF_BLUETOOTH", raising=False)
    monkeypatch.delattr(socket, "BTPROTO_RFCOMM", raising=False)
    created = []

    class FakeLibc:
        def socket(self, *a):
            created.append(a)
            return 99

    monkeypatch.setattr("ctypes.CDLL", lambda *a, **k: FakeLibc())
    with pytest.raises(ValueError, match="invalid Bluetooth address"):
        _open_rfcomm(address, 1, 1.0)
    assert created == []

import errno
import struct

import pytest

from hermes_minitoo import linkstate as L

ADDR = "B1:21:81:A0:78:53"


def _info(handle, address, kind, mode, out=0, state=1):
    raw = bytes(int(x, 16) for x in address.split(":"))[::-1]  # bdaddr_t is little-endian
    return struct.pack("<H6sBBHI", handle, raw, kind, out, state, mode)


class FakeSocket:
    instances = []

    def __init__(self, family, kind, proto):
        self.args = (family, kind, proto)
        self.closed = False
        FakeSocket.instances.append(self)

    def fileno(self):
        return 42

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def kernel(monkeypatch):
    """Fake HCIGETCONNLIST: fills the caller's buffer like hci_get_conn_list()."""
    state = {"entries": [], "error": None, "calls": []}

    def ioctl(fd, request, buf, mutate):
        state["calls"].append((fd, request, bytes(buf[:4]), len(buf), mutate))
        if state["error"] is not None:
            raise state["error"]
        dev_id, conn_num = struct.unpack_from("<HH", buf, 0)
        entries = state["entries"][:conn_num]
        struct.pack_into("<HH", buf, 0, dev_id, len(entries))
        for index, entry in enumerate(entries):
            buf[4 + 16 * index: 20 + 16 * index] = entry
        return 0

    FakeSocket.instances = []
    monkeypatch.setattr(L.socket, "socket", FakeSocket)
    monkeypatch.setattr(L.fcntl, "ioctl", ioctl)
    return state


def test_connections_parses_the_conn_list(kernel):
    kernel["entries"] = [
        _info(11, ADDR, 0x01, 0x0001),               # ACL, we are central
        _info(12, ADDR, 0x02, 0x0000),               # eSCO, peripheral
        _info(13, "11:22:33:44:55:66", 0x80, 0x0005),  # LE, central + encrypted
        _info(14, "11:22:33:44:55:66", 0x00, 0x0000),  # SCO
        _info(15, "11:22:33:44:55:66", 0x82, 0x0000),  # ISO: not reported
    ]
    links = L.connections(1)
    assert links == [
        L.Link(ADDR, "ACL", 11, True),
        L.Link(ADDR, "eSCO", 12, False),
        L.Link("11:22:33:44:55:66", "LE", 13, True),
        L.Link("11:22:33:44:55:66", "SCO", 14, False),
    ]
    fd, request, head, size, mutate = kernel["calls"][0]
    assert request == 0x800448D4 and mutate is True
    assert struct.unpack("<HH", head) == (1, 32) and size == 4 + 16 * 32
    sock = FakeSocket.instances[0]
    assert sock.args == (31, L.socket.SOCK_RAW, 1) and sock.closed


def test_sco_up(kernel):
    kernel["entries"] = [_info(11, ADDR, 0x01, 1)]
    assert L.sco_up(ADDR) is False
    kernel["entries"].append(_info(12, ADDR, 0x02, 0))
    assert L.sco_up(ADDR.lower()) is True
    assert L.sco_up("11:22:33:44:55:66") is False
    kernel["entries"] = [_info(12, ADDR, 0x00, 0)]
    assert L.sco_up(ADDR) is True


def test_errors(kernel, monkeypatch):
    kernel["error"] = OSError(errno.ENODEV, "No such device")
    with pytest.raises(OSError):
        L.connections()
    assert L.sco_up(ADDR) is False
    assert all(sock.closed for sock in FakeSocket.instances)

    def no_bluetooth(*args):
        raise OSError(errno.EAFNOSUPPORT, "Address family not supported by protocol")

    monkeypatch.setattr(L.socket, "socket", no_bluetooth)
    assert L.sco_up(ADDR) is False
    with pytest.raises(ValueError):
        L.connections(-1)


def test_probe_keeps_one_socket(kernel):
    kernel["entries"] = [_info(12, ADDR, 0x02, 0)]
    with L.LinkProbe(0) as probe:
        for _ in range(5):
            assert probe.sco_up(ADDR) is True
        assert len(probe.connections()) == 1
        assert len(FakeSocket.instances) == 1
    assert FakeSocket.instances[0].closed


def test_probe_reopens_after_a_socket_error_but_not_for_a_missing_adapter(kernel):
    probe = L.LinkProbe(0)
    kernel["error"] = OSError(errno.ENODEV, "No such device")
    assert probe.sco_up(ADDR) is False
    with pytest.raises(OSError):
        probe.connections()
    assert len(FakeSocket.instances) == 1 and not FakeSocket.instances[0].closed
    kernel["error"] = OSError(errno.EBADF, "Bad file descriptor")
    with pytest.raises(OSError):
        probe.connections()
    assert FakeSocket.instances[0].closed
    kernel["error"] = None
    assert probe.connections() == []
    assert len(FakeSocket.instances) == 2
    probe.close()


def test_real_kernel_call_never_raises_from_sco_up():
    # Whatever this machine has (no adapter, no Bluetooth support, no permission),
    # sco_up() answers False or True and never raises.
    assert L.sco_up(ADDR) in (True, False)

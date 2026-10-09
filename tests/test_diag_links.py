"""scripts/diag/links.py: HCIGETCONNLIST parsing and polling, with fake sockets and ioctl."""
import errno
import importlib.util
import json
import pathlib
import struct

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "diag" / "links.py"
_spec = importlib.util.spec_from_file_location("diag_links", _p)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

SPEAKER = "B1:21:81:A0:78:53"


def raw(address):
    return bytes(reversed(bytes.fromhex(address.replace(":", ""))))  # bdaddr_t is little-endian


def conn_buf(entries, room=L.MAX_CONN):
    """What the kernel writes back: hci_conn_list_req + hci_conn_info[]."""
    buf = bytearray(4 + 16 * room)
    struct.pack_into("<HH", buf, 0, 0, len(entries))
    for i, (handle, address, kind, out, state, mode) in enumerate(entries):
        struct.pack_into("<H6sBBHI", buf, 4 + 16 * i, handle, raw(address), kind, out, state, mode)
    return buf


def test_struct_sizes_match_the_kernel_abi():
    assert L.REQ.size == 4 and L.INFO.size == 16
    assert L.HCIGETCONNLIST == (2 << 30) | (4 << 16) | (ord("H") << 8) | 212  # _IOR('H', 212, int)


def test_parse_reads_acl_esco_and_flags():
    buf = conn_buf([(11, SPEAKER, 0x01, 1, 1, 0x0001 | 0x0004),
                    (257, SPEAKER, 0x02, 1, 5, 0x0001),
                    (64, "11:22:33:44:55:66", 0x80, 0, 1, 0)])
    links = L.parse_conn_list(buf)
    assert links[0] == L.Link(SPEAKER, "ACL", 11, True, True, "connected", True)
    assert links[1] == L.Link(SPEAKER, "eSCO", 257, True, True, "connecting", False)
    assert links[2] == L.Link("11:22:33:44:55:66", "LE", 64, False, False, "connected", False)


def test_parse_never_reads_past_the_buffer():
    buf = conn_buf([(1, SPEAKER, 0x00, 0, 1, 0)], room=1)
    struct.pack_into("<HH", buf, 0, 0, 9)  # kernel count larger than our buffer
    assert [link.type for link in L.parse_conn_list(buf)] == ["SCO"]


class FakeSock:
    def __init__(self, n):
        self.n, self.closed = n, False

    def fileno(self):
        return 100 + self.n

    def close(self):
        self.closed = True


def make_probe(answers):
    """answers: list of entry lists or OSError instances, one per ioctl call."""
    sockets, calls = [], []

    def opener():
        sockets.append(FakeSock(len(sockets)))
        return sockets[-1]

    def ioctl(fd, request, buf, mutate):
        calls.append((fd, request, bytes(buf[:4]), mutate))
        answer = answers.pop(0)
        if isinstance(answer, OSError):
            raise answer
        buf[:] = conn_buf(answer)
        return 0

    return L.ConnList(0, opener=opener, ioctl=ioctl), sockets, calls


def test_one_socket_is_reused_for_every_poll():
    probe, sockets, calls = make_probe([[], [(11, SPEAKER, 1, 1, 1, 1)], []])
    for _ in range(3):
        probe.query()
    assert len(sockets) == 1 and probe.opened == 1
    assert {c[0] for c in calls} == {100}
    assert calls[0][1] == L.HCIGETCONNLIST and calls[0][3] is True
    assert calls[0][2] == struct.pack("<HH", 0, L.MAX_CONN)


def test_socket_is_reopened_after_an_error_but_kept_on_enodev():
    probe, sockets, _ = make_probe([OSError(errno.ENODEV, "No such device"), [],
                                    OSError(errno.EIO, "I/O error"), []])
    with pytest.raises(OSError):
        probe.query()
    probe.query()
    assert len(sockets) == 1
    with pytest.raises(OSError):
        probe.query()
    assert sockets[0].closed and probe.sock is None
    probe.query()
    assert len(sockets) == 2


def test_default_socket_uses_numeric_family(monkeypatch):
    seen = []
    monkeypatch.setattr(L.socket, "socket", lambda *args: seen.append(args) or FakeSock(0))
    probe = L.ConnList(0, ioctl=lambda fd, req, buf, mutate: None)
    probe.query()
    assert seen == [(31, L.socket.SOCK_RAW, 1)]


def test_format_marks_the_speaker():
    links = L.parse_conn_list(conn_buf([(11, SPEAKER, 1, 1, 1, 5), (12, "11:22:33:44:55:66", 1, 0, 1, 0)]))
    line = L.format_links(links, SPEAKER)
    assert line == f"*{SPEAKER} ACL h=11 central enc out |  11:22:33:44:55:66 ACL h=12 peripheral"
    assert L.format_links([], SPEAKER) == "-"


def test_watch_prints_only_changes_and_errors_once():
    acl = [(11, SPEAKER, 1, 0, 1, 1)]
    esco = acl + [(257, SPEAKER, 2, 0, 1, 1)]
    probe, _, _ = make_probe([[], acl, acl, esco, OSError(errno.ENODEV, "No such device"),
                              OSError(errno.ENODEV, "No such device"), acl])
    out, sleeps = [], []
    clock = iter(float(i) for i in range(100))
    rc = L.watch(probe, SPEAKER, out=out.append, clock=lambda: next(clock), sleep=sleeps.append, limit=7)
    assert rc == 0
    lines = [line.split("  ", 1)[1] for line in out]
    acl_line = f"*{SPEAKER} ACL h=11 central"
    assert lines == ["-", acl_line, f"{acl_line} | *{SPEAKER} eSCO h=257 central",
                     "ошибка HCIGETCONNLIST на hci0: [Errno 19] No such device", acl_line]
    assert sleeps == [0.5, 0.5, 0.5, 0.5, 2.0, 2.0]  # no pause after the last poll


def test_watch_once_reports_failure():
    probe, _, _ = make_probe([OSError(errno.ENODEV, "No such device")])
    assert L.watch(probe, SPEAKER, out=lambda s: None, sleep=lambda s: None, limit=1) == 1


def test_rssi_only_while_the_speaker_has_an_acl_link():
    acl = [(11, SPEAKER, 1, 1, 1, 1)]
    probe, _, _ = make_probe([[], acl, acl, acl, acl])
    out, asked = [], []
    clock = iter([0.0, 0.5, 1.0, 1.5, 2.5])

    def metrics(address):
        asked.append(address)
        return "rssi=-3 lq=255"

    L.watch(probe, SPEAKER, rssi_every=2.0, out=out.append, clock=lambda: next(clock), sleep=lambda s: None,
            metrics=metrics, limit=5)
    assert asked == [SPEAKER, SPEAKER]  # at 0.5 s and again 2 s later, never without ACL
    assert out[-1].endswith(f"{SPEAKER} rssi=-3 lq=255")


def test_hcitool_metrics_parsing():
    class R:
        def __init__(self, stdout):
            self.stdout, self.stderr = stdout, ""

    answers = {"rssi": R("RSSI return value: -7\n"), "lq": R("Link quality: 231\n")}
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return answers[args[1]]

    assert L.hcitool_metrics(SPEAKER, run=run) == "rssi=-7 lq=231"
    assert calls == [["hcitool", "rssi", SPEAKER], ["hcitool", "lq", SPEAKER]]
    assert L.hcitool_metrics(SPEAKER, run=lambda *a, **k: R("Not connected.\n")) == "rssi=? lq=?"


def test_speaker_address_comes_from_config(tmp_path):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"token": "SECRET", "minitoo": {"address": SPEAKER.lower()}}))
    assert L.speaker_address(str(conf)) == SPEAKER
    assert L.speaker_address(str(tmp_path / "missing.json")) is None
    conf.write_text("{not json")
    assert L.speaker_address(str(conf)) is None


def test_main_once_with_a_fake_probe(monkeypatch, tmp_path, capsys):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"minitoo": {"address": SPEAKER}}))
    probe, _, _ = make_probe([[(11, SPEAKER, 1, 0, 1, 0)]])
    monkeypatch.setattr(L, "ConnList", lambda dev: probe)
    assert L.main(["--once", "--config", str(conf), "--prefix", "[links] "]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("[links] hci0:") and SPEAKER in out[0]
    assert out[1].startswith("[links] ") and out[1].endswith(f"*{SPEAKER} ACL h=11 peripheral")

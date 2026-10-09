"""Read-only Bluetooth link table for hciN, without root and with the stdlib only.

Uses the HCIGETCONNLIST ioctl on a raw HCI socket. The kernel performs no capability
check for it (net/bluetooth/hci_sock.c), it sends nothing to the controller and an
unbound raw socket receives no HCI traffic, so polling it changes nothing on the radio.
The socket is created with the numeric family/protocol, so this also works on Python
builds without socket.AF_BLUETOOTH.

Nothing in this module imports hermes_gadget; scripts can use it with the system Python.
"""

from __future__ import annotations

import errno
import fcntl
import socket
import struct
import threading
from collections import namedtuple

HCIGETCONNLIST = 0x800448D4  # _IOR('H', 212, int), include/net/bluetooth/hci_sock.h
_AF_BLUETOOTH = 31
_BTPROTO_HCI = 1
_MAX_CONN = 32

# struct hci_conn_list_req { u16 dev_id; u16 conn_num; struct hci_conn_info conn_info[]; }
_REQ = struct.Struct("<HH")
# struct hci_conn_info { u16 handle; bdaddr_t bdaddr; u8 type; u8 out; u16 state; u32 link_mode; }
_INFO = struct.Struct("<H6sBBHI")  # 16 bytes
_TYPES = {0x00: "SCO", 0x01: "ACL", 0x02: "eSCO", 0x80: "LE"}  # include/net/bluetooth/hci.h
_LM_MASTER = 0x0001  # HCI_LM_MASTER: we are the central of this link

Link = namedtuple("Link", "address type handle central")
Link.__doc__ = """One link: address 'AA:BB:CC:DD:EE:FF', type 'ACL'|'SCO'|'eSCO'|'LE', handle, central."""


def _check_dev_id(dev_id: int) -> int:
    if type(dev_id) is not int or not 0 <= dev_id <= 0xFFFF:
        raise ValueError("dev_id must be an integer from 0 to 65535")
    return dev_id


def _open_socket() -> socket.socket:
    return socket.socket(_AF_BLUETOOTH, socket.SOCK_RAW, _BTPROTO_HCI)


def _query(sock: socket.socket, dev_id: int) -> list[Link]:
    buf = bytearray(_REQ.size + _INFO.size * _MAX_CONN)
    _REQ.pack_into(buf, 0, dev_id, _MAX_CONN)
    fcntl.ioctl(sock.fileno(), HCIGETCONNLIST, buf, True)
    count = _REQ.unpack_from(buf, 0)[1]
    links = []
    for index in range(min(count, _MAX_CONN)):
        handle, raw, kind, _out, _state, mode = _INFO.unpack_from(buf, _REQ.size + index * _INFO.size)
        name = _TYPES.get(kind)
        if name is None:  # ISO/AMP and future link types are not reported
            continue
        address = ":".join(f"{byte:02X}" for byte in reversed(raw))
        links.append(Link(address, name, handle, bool(mode & _LM_MASTER)))
    return links


def _has_sco(links: list[Link], address: str) -> bool:
    want = address.upper()
    return any(link.address == want and link.type in ("SCO", "eSCO") for link in links)


def connections(dev_id: int = 0) -> list[Link]:
    """Links of hci<dev_id>. Raises OSError if there is no adapter or no permission."""
    _check_dev_id(dev_id)
    with _open_socket() as sock:
        return _query(sock, dev_id)


def sco_up(address: str, dev_id: int = 0) -> bool:
    """True if an SCO or eSCO link to address exists; False on any OSError."""
    try:
        return _has_sco(connections(dev_id), address)
    except OSError:
        return False


class LinkProbe:
    """Same queries over ONE long-lived socket.

    Every new raw HCI socket that issues an ioctl shows up as an open/close pair in
    btmon, so frequent polling should reuse a socket. It is re-opened after an error.
    """

    def __init__(self, dev_id: int = 0) -> None:
        self.dev_id = _check_dev_id(dev_id)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def connections(self) -> list[Link]:
        """Like connections(); raises OSError on failure."""
        with self._lock:
            if self._sock is None:
                self._sock = _open_socket()
            try:
                return _query(self._sock, self.dev_id)
            except OSError as exc:
                # ENODEV only means hci<dev_id> is absent; the socket itself is fine.
                if exc.errno != errno.ENODEV:
                    self._close_locked()
                raise

    def sco_up(self, address: str) -> bool:
        """True if an SCO or eSCO link to address exists; False on any OSError."""
        try:
            return _has_sco(self.connections(), address)
        except OSError:
            return False

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> LinkProbe:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

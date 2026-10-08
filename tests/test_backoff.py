import time

import pytest

from hermes_minitoo.transport import RFCOMMTransport


def test_backoff_does_not_extend_itself():
    t = RFCOMMTransport.__new__(RFCOMMTransport)
    t.sock = None
    t.reconnect_delay = 2.0
    t.last_failure = time.monotonic()
    before = t.last_failure
    with pytest.raises(ConnectionError):
        t.connect()
    assert t.last_failure == before

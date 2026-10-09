import time

import pytest

from hermes_minitoo.transport import RFCOMMTransport


def test_backoff_does_not_extend_itself():
    t = RFCOMMTransport("AA:BB:CC:DD:EE:FF", reconnect_delay_ms=2000)
    t.last_failure = time.monotonic()
    before = t.last_failure
    with pytest.raises(ConnectionError):
        t.connect()
    assert t.last_failure == before

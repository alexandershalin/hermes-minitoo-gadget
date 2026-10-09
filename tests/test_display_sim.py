"""Deterministic, single-threaded simulation of MiniTooDisplay (fake clock and threads).

Helpers only (no tests); test_display*.py import it. The worker loop runs in the test thread;
the "service thread" calls to present() are scheduled events fired whenever the fake
clock passes them (also while the worker is inside send_rgb888 or a retry pause).
"""

from __future__ import annotations

import threading

ADDR = "AA:BB:CC:DD:EE:FF"
FRAME_BYTES = 160 * 128 * 2


class Idle(Exception):
    """The worker would block waiting for a frame."""


class End(Exception):
    """The simulation reached its time limit."""


class Device:
    def __init__(self, screen: str | None = "ready"):
        self.fid = 1
        self.scr = screen
        self.screen_calls = 0
        self.fb_calls = 0

    def framebuffer_rows(self):
        self.fb_calls += 1
        return bytes([self.fid]) * FRAME_BYTES

    def screen(self):
        self.screen_calls += 1
        return self.scr


class DeviceWithoutScreen:
    def __init__(self):
        self.fid = 1
        self.scr = None
        self.fb_calls = 0

    def framebuffer_rows(self):
        self.fb_calls += 1
        return bytes([self.fid]) * FRAME_BYTES


class _Time:
    def __init__(self, sim):
        self.sim = sim

    def monotonic(self):
        return self.sim.t

    def time(self):
        return 0.0

    def sleep(self, seconds):
        self.sim.pause(seconds)


class _Condition:
    def __init__(self, sim):
        self.sim = sim

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait_for(self, predicate, timeout=None):
        if predicate():
            return True
        raise Idle

    def wait(self, timeout=None):
        self.sim.trace.append(("wait", self.sim.now(), timeout))
        self.sim.advance(timeout)
        return False

    def notify(self, n=1):
        pass

    def notify_all(self):
        pass


class _Event:
    def __init__(self, sim):
        self.sim = sim
        self.flag = False

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def is_set(self):
        return self.flag

    def wait(self, timeout=None):
        if self.flag:
            return True
        self.sim.pause(timeout)
        return self.flag


class _Thread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


class Transport:
    def __init__(self, sim, *args, **kwargs):
        self.sim = sim
        sim.transport_args = (args, kwargs)

    def send_rgb888(self, rgb, **kwargs):
        sim = self.sim
        sim.calls += 1
        sim.trace.append(("send", sim.now(), rgb[0], kwargs))
        duration, exc = sim.outcome(sim, sim.calls, rgb, kwargs)
        sim.advance(duration)
        if exc is not None:
            sim.trace.append(("fail", sim.now(), type(exc).__name__))
            raise exc
        sim.trace.append(("ok", sim.now()))

    def abort(self):
        self.sim.trace.append(("abort", self.sim.now()))

    def close(self):
        self.sim.trace.append(("close", self.sim.now()))


def ok(duration=0.3):
    return lambda sim, call, rgb, kwargs: (duration, None)


class Sim:
    """present_at: (t_seconds, dirty, frame_id[, screen]) or (t_seconds, action) events.
    outcome(sim, call_number, rgb, kwargs) -> (duration_s, exception or None)."""

    def __init__(self, module, monkeypatch, config=None, *, present_at=(), outcome=None,
                 device=None, before_init=None):
        self.module = module
        self.t = 0.0
        self.until = float("inf")
        self.trace: list[tuple] = []
        self.events = sorted(present_at, key=lambda event: event[0])
        self.outcome = outcome or ok()
        self.calls = 0
        self.dev = device if device is not None else Device()
        fake_threading = type("T", (), {})()
        fake_threading.Thread = _Thread
        fake_threading.Condition = lambda *a: _Condition(self)
        fake_threading.Event = lambda: _Event(self)
        fake_threading.Lock = threading.Lock
        monkeypatch.setattr(module, "time", _Time(self))
        monkeypatch.setattr(module, "threading", fake_threading)
        monkeypatch.setattr(module, "rgb565le_to_rgb888", lambda raw: raw)
        monkeypatch.setattr(module, "RFCOMMTransport", lambda *a, **k: Transport(self, *a, **k))
        if before_init is not None:
            before_init(self)
        self.display = module.MiniTooDisplay({"address": ADDR, **(config or {})})

    def now(self):
        return round(self.t, 3)

    def pause(self, seconds):
        self.trace.append(("sleep", self.now(), seconds))
        self.advance(seconds)

    def advance(self, seconds):
        target = self.t + (seconds or 0)
        while self.events and self.events[0][0] <= target:
            event = self.events.pop(0)
            self.t = max(self.t, event[0])
            self.fire(event)
        self.t = target
        if self.t > self.until:
            raise End

    def fire(self, event):
        if callable(event[1]):  # (t, action): run action(sim) at time t
            event[1](self)
            return
        _, dirty, fid, *rest = event
        d = self.display
        if dirty:
            d.dirty = True
        self.dev.fid = fid
        if rest:
            self.dev.scr = rest[0]
        before = d.last_queued
        ms = int(round(self.t * 1000))
        d.present(self.dev, ms)
        if d.last_queued != before:
            self.trace.append(("queued", ms, fid))

    def run(self, until):
        """Run worker and scheduled presents until the fake clock passes `until`."""
        self.until = until
        try:
            while True:
                try:
                    self.display._run()
                    return  # worker exited (closed)
                except Idle:
                    pass
                if not self.events or self.events[0][0] > until:
                    return
                event = self.events.pop(0)
                self.t = max(self.t, event[0])
                self.fire(event)
        except End:
            return

    def sends(self):
        return [entry for entry in self.trace if entry[0] == "send"]


# ---- scenarios shared with the origin/main equivalence check ----

def scenario_a_events():
    """An animated screen: dirty every 0.5 s for 20 s, a new frame id each time."""
    return [(i * 0.5, True, i % 250 + 1) for i in range(41)]


def scenario_a_outcome(sim, call, rgb, kwargs):
    script = {
        2: (8.0, TimeoutError("MiniToo did not send the 0x8B ready ACK")),
        4: (0.0, ConnectionError("MiniToo reconnect backoff is active")),
        5: (2.0, ConnectionRefusedError(111, "Connection refused")),
    }
    return script.get(call, (0.3, None))


def scenario_b_events():
    """A static screen during a 70 s outage (exceeds the 60 s retry window)."""
    return [(0.0, True, 7)] + [(float(i), False, 7) for i in range(1, 86)]


def scenario_b_outcome(sim, call, rgb, kwargs):
    if sim.t < 70:
        return 0.0, ConnectionError("MiniToo reconnect backoff is active")
    return 0.3, None

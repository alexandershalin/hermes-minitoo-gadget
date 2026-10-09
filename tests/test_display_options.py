"""Opt-in display options: screen_change_immediate, hfp_gate, switch_ready_timeout_ms,
listen_preroll. Simulated time; no hardware."""

import logging

import pytest
from test_display_sim import ADDR, Device, DeviceWithoutScreen, Sim, ok

from hermes_minitoo import display as D
from hermes_minitoo.linkstate import Link

# ---- screen_change_immediate ----

SCREEN_EVENTS = [
    (0.0, True, 1, "ready"),
    (1.0, True, 2, "listening"),   # screen change 1 s after the last frame
    (1.5, True, 3, "listening"),   # same screen: throttled as before
    (3.5, True, 4, "listening"),   # 2.5 s after the 1.0 s frame
]


def _queued(sim):
    return [(ms, fid) for kind, ms, fid in (e for e in sim.trace if e[0] == "queued")]


def test_screen_change_bypasses_the_throttle_when_enabled(monkeypatch):
    sim = Sim(D, monkeypatch, {"screen_change_immediate": True}, present_at=SCREEN_EVENTS)
    sim.run(5.0)
    assert _queued(sim) == [(0, 1), (1000, 2), (3500, 4)]


def test_screen_change_is_throttled_by_default(monkeypatch):
    sim = Sim(D, monkeypatch, present_at=SCREEN_EVENTS + [(2.5, False, 3, "listening")])
    sim.run(5.0)
    assert _queued(sim) == [(0, 1), (2500, 3)]
    assert sim.dev.screen_calls == 0


@pytest.mark.parametrize("device", [DeviceWithoutScreen(), "raises"])
def test_screen_change_without_a_usable_screen_behaves_as_today(monkeypatch, device):
    if device == "raises":
        device = Device()

        def boom():
            raise RuntimeError("no screen")

        device.screen = boom
    sim = Sim(D, monkeypatch, {"screen_change_immediate": True}, device=device,
              present_at=SCREEN_EVENTS + [(2.5, False, 3, "listening")])
    sim.run(5.0)
    assert _queued(sim) == [(0, 1), (2500, 3)]


# ---- HfpGate (unit) ----

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Probe:
    def __init__(self, links=(), error=None):
        self.links = list(links)
        self.error = error
        self.calls = 0
        self.closed = False

    def connections(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.links)

    def close(self):
        self.closed = True


SCO = Link(ADDR, "eSCO", 12, False)
ACL = Link(ADDR, "ACL", 11, True)


def _gate(probe, clock, **kw):
    return D.HfpGate(ADDR.lower(), probe=probe, settle_s=1.5, post_mic_s=3.0,
                     max_s=kw.pop("max_s", 60.0), clock=clock, **kw)


def test_gate_blocks_while_mic_open_and_for_the_post_mic_hold():
    clock, probe = Clock(), Probe([ACL])
    gate = _gate(probe, clock)
    assert not gate.blocked()
    gate.note_mic(True)
    assert gate.blocked() and gate.risky()
    clock.t += 30
    assert gate.blocked()
    gate.note_mic(False)
    clock.t += 2.9
    assert gate.blocked()
    clock.t += 0.2
    assert not gate.blocked() and not gate.risky()


def test_gate_blocks_while_sco_is_up_and_for_the_settle_time():
    clock, probe = Clock(), Probe([ACL])
    gate = _gate(probe, clock)
    assert not gate.blocked()
    clock.t += 1
    probe.links = [ACL, SCO]
    assert gate.blocked() and gate.sco() is True
    clock.t += 1
    probe.links = [ACL]
    assert gate.blocked()  # settle after SCO went down
    clock.t += 1.6
    assert not gate.blocked() and gate.sco() is False


def test_gate_blocks_while_sco_is_up_even_with_zero_settle():
    # Regression: with settle_s below the 250 ms poll interval the gate used to let
    # uploads through in the middle of an SCO link.
    clock, probe = Clock(), Probe([ACL, SCO])
    gate = D.HfpGate(ADDR.lower(), probe=probe, settle_s=0.0, post_mic_s=0.0,
                     max_s=60.0, clock=clock)
    assert gate.blocked() and gate.risky()
    probe.links = [ACL]
    clock.t += 0.3  # past the poll interval, SCO gone, settle is zero
    assert not gate.blocked()


def test_gate_ignores_other_devices_and_acl_only_links():
    clock = Clock()
    gate = _gate(Probe([ACL, Link("11:22:33:44:55:66", "SCO", 5, True)]), clock)
    assert not gate.blocked() and gate.sco() is False


def test_gate_polls_the_probe_at_most_every_250_ms():
    clock, probe = Clock(), Probe([ACL])
    gate = _gate(probe, clock)
    for _ in range(10):
        gate.blocked()
        clock.t += 0.05
    assert probe.calls == 2  # at 100.0 and 100.25 (+/- float noise: 100.25..100.45)


def test_gate_falls_back_to_mic_timing_when_the_probe_fails(caplog):
    clock, probe = Clock(), Probe(error=PermissionError(1, "Operation not permitted"))
    gate = _gate(probe, clock)
    caplog.set_level(logging.INFO, logger=D.LOG.name)
    for _ in range(5):
        assert not gate.blocked()
        assert gate.sco() is None
        clock.t += 0.3
    warnings = [r for r in caplog.records if "SCO probe unavailable" in r.getMessage()]
    assert len(warnings) == 1
    gate.note_mic(True)
    assert gate.blocked()
    probe.error = None
    clock.t += 0.3
    assert gate.sco() is False
    assert any("available again" in r.getMessage() for r in caplog.records)


def test_gate_max_s_bounds_a_stall_per_episode(caplog):
    clock, probe = Clock(), Probe([ACL])
    gate = _gate(probe, clock, max_s=10.0)
    gate.note_mic(True)
    assert gate.blocked()
    clock.t += 9.9
    assert gate.blocked()
    clock.t += 0.2
    assert not gate.blocked() and gate.risky()  # released, but still risky
    clock.t += 50
    assert not gate.blocked()
    gate.note_mic(False)
    clock.t += 3.1
    assert not gate.blocked() and not gate.risky()  # episode over
    gate.note_mic(True)
    assert gate.blocked()  # a new episode blocks again
    assert sum("held uploads" in r.getMessage() for r in caplog.records) == 1


def test_gate_new_recording_starts_a_new_episode_even_while_still_risky():
    clock, probe = Clock(), Probe([ACL])
    gate = _gate(probe, clock, max_s=10.0)
    gate.note_mic(True)
    assert gate.blocked()
    clock.t += 10.1
    assert not gate.blocked()  # released
    gate.note_mic(False)
    clock.t += 1  # inside the post-mic hold: never "not risky" in between
    gate.note_mic(True)
    assert gate.blocked()


# ---- hfp_gate in the worker ----

def _gate_sim(monkeypatch, probe, config=None, **kw):
    monkeypatch.setattr(D, "LinkProbe", lambda dev_id: probe)
    return Sim(D, monkeypatch, {"hfp_gate": True, **(config or {})}, **kw)


class TimedProbe(Probe):
    """SCO to the speaker exists while sim time is in [start, end)."""

    def __init__(self, start, end):
        super().__init__()
        self.start, self.end = start, end
        self.sim = None

    def connections(self):
        self.calls += 1
        up = self.sim is not None and self.start <= self.sim.t < self.end
        return [ACL, SCO] if up else [ACL]


def _animated(until, step=0.5):
    return [(i * step, True, i % 250 + 1) for i in range(int(until / step) + 1)]


def test_gate_holds_uploads_during_sco_and_sends_only_the_latest_frame(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=D.LOG.name)
    probe = TimedProbe(2.0, 6.0)
    sim = _gate_sim(monkeypatch, probe, present_at=_animated(12.0))
    probe.sim = sim
    sim.run(12.0)
    sends = [(t, fid) for _, t, fid, _ in sim.sends()]
    # Frames are queued at 0, 2.5, 5.0, 7.5 and 10.0 s. SCO is up from 2.0 to 6.0 s; the
    # last poll that saw it was at 5.75 s, so the hold ends 1.5 s later, at 7.25 s.
    # The 2.5 s frame (6) was replaced by the 5.0 s frame (11) while held.
    assert sends == [(0.0, 1), (7.25, 11), (7.55, 16), (10.0, 21)]
    assert all(kw == {} for *_, kw in sim.sends())  # no switch_ready_timeout configured
    messages = [r.getMessage() for r in caplog.records]
    assert "MiniToo display paused: HFP/SCO" in messages
    assert any(m.startswith("MiniToo display resumed after") for m in messages)


def test_gate_holds_uploads_while_the_gadget_mic_is_open(monkeypatch):
    probe = Probe([ACL])
    events = _animated(10.0) + [
        (1.0, lambda sim: sim.display.note_mic(True)),
        (4.0, lambda sim: sim.display.note_mic(False)),
    ]
    sim = _gate_sim(monkeypatch, probe, present_at=events)
    sim.run(10.0)
    times = [t for _, t, _, _ in sim.sends()]
    assert times[0] == 0.0
    assert not any(1.0 <= t < 7.0 for t in times)  # mic open + 3 s post-mic hold
    assert any(7.0 <= t < 7.3 for t in times)


def test_gate_does_not_hold_a_failed_frame_once_the_switch_is_over(monkeypatch):
    probe = TimedProbe(1.0, 4.0)

    def outcome(sim, call, rgb, kwargs):
        if call == 1:
            return 8.0, TimeoutError("MiniToo did not send the 0x8B ready ACK")
        return 0.3, None

    sim = _gate_sim(monkeypatch, probe, present_at=[(0.5, True, 9)], outcome=outcome)
    probe.sim = sim
    sim.run(20.0)
    # The upload started before SCO and failed at 8.5 s, after SCO (1-4 s) had settled,
    # so it is retried after the normal 1 s pause.
    assert [e for e in sim.trace if e[0] in ("send", "fail", "sleep", "ok")] == [
        ("send", 0.5, 9, {}), ("fail", 8.5, "TimeoutError"), ("sleep", 8.5, 1.0),
        ("send", 9.5, 9, {}), ("ok", 9.8)]


def test_gate_parks_instead_of_retrying_while_blocked(monkeypatch):
    probe = TimedProbe(1.0, 30.0)

    def outcome(sim, call, rgb, kwargs):
        if call == 1:
            return 8.0, TimeoutError("MiniToo did not send the 0x8B ready ACK")
        return 0.3, None

    sim = _gate_sim(monkeypatch, probe, present_at=[(0.5, True, 9)], outcome=outcome)
    probe.sim = sim
    sim.run(40.0)
    sends = [(t, fid) for _, t, fid, _ in sim.sends()]
    # Failed at 8.5 while SCO is up: no 1 s retry loop, the frame waits behind the gate
    # and is sent once SCO is gone and settled (last seen at 29.75 s + 1.5 s).
    assert sends == [(0.5, 9), (31.25, 9)]
    assert not any(e[0] == "sleep" for e in sim.trace)


def test_failure_log_reports_sco_state_with_the_gate(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=D.LOG.name)
    probe = TimedProbe(1.0, 30.0)

    def outcome(sim, call, rgb, kwargs):
        return (8.0, TimeoutError("no ACK")) if call == 1 else (0.3, None)

    sim = _gate_sim(monkeypatch, probe, present_at=[(0.5, True, 9)], outcome=outcome)
    probe.sim = sim
    sim.run(40.0)
    retry = [r.getMessage() for r in caplog.records if "display retry" in r.getMessage()]
    assert retry == ["MiniToo display retry 1: TimeoutError('no ACK') sco=up"]


def test_switch_ready_timeout_applies_only_while_risky(monkeypatch):
    probe = Probe([ACL])
    events = [
        (0.0, True, 1),
        (1.0, lambda sim: sim.display.note_mic(True)),
        (3.0, True, 2),     # held; max_s=1 releases it while the mic is still open
        (20.0, lambda sim: sim.display.note_mic(False)),
        (30.0, True, 3),    # after the post-mic hold: normal timeout again
    ]
    sim = _gate_sim(monkeypatch, probe, {"switch_ready_timeout_ms": 1500, "hfp_gate_max_s": 1},
                    present_at=events)
    sim.run(35.0)
    assert [(fid, kw) for _, _, fid, kw in sim.sends()] == [
        (1, {}), (2, {"ready_timeout": 1.5}), (3, {})]


def test_switch_ready_timeout_without_gate_is_a_logged_no_op(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=D.LOG.name)
    sim = Sim(D, monkeypatch, {"switch_ready_timeout_ms": 1500}, present_at=[(0.0, True, 1)])
    sim.run(1.0)
    assert [kw for *_, kw in sim.sends()] == [{}]
    assert "no effect without minitoo.hfp_gate" in caplog.text


def test_gate_probe_is_closed_with_the_display(monkeypatch):
    probe = Probe([ACL])
    sim = _gate_sim(monkeypatch, probe)
    sim.display.close()
    assert probe.closed


def test_gate_config_reaches_the_probe_and_windows(monkeypatch):
    seen = {}

    def factory(dev_id):
        seen["dev_id"] = dev_id
        return Probe()

    monkeypatch.setattr(D, "LinkProbe", factory)
    sim = Sim(D, monkeypatch, {"hfp_gate": True, "hci_dev": 1, "hfp_gate_settle_ms": 500,
                               "hfp_gate_post_mic_ms": 6500, "hfp_gate_max_s": 30})
    gate = sim.display._gate
    assert seen == {"dev_id": 1}
    assert (gate.settle_s, gate.post_mic_s, gate.max_s) == (0.5, 6.5, 30.0)
    assert gate.address == ADDR


def test_gate_is_absent_by_default(monkeypatch):
    def factory(dev_id):
        raise AssertionError("no probe without hfp_gate")

    monkeypatch.setattr(D, "LinkProbe", factory)
    sim = Sim(D, monkeypatch, present_at=[(0.0, True, 1)])
    sim.run(1.0)
    assert sim.display._gate is None


# ---- listen_preroll ----

def _preroll_sim(monkeypatch, events, config=None, outcome=None):
    return Sim(D, monkeypatch, {"listen_preroll": True, **(config or {})},
               present_at=events, outcome=outcome)


def test_preroll_sends_the_listening_frame_immediately_and_reports_it(monkeypatch):
    states = []
    events = [
        (0.0, True, 1, "ready"),
        (1.0, lambda sim: sim.display.arm_listening()),
        (1.0, True, 2, "listening"),
        (1.35, lambda sim: states.append(sim.display.preroll_ready())),
        (1.5, lambda sim: states.append(sim.display.preroll_ready())),
    ]
    sim = _preroll_sim(monkeypatch, events)
    sim.run(3.0)
    assert ("queued", 1000, 2) in sim.trace  # not throttled until 2.5 s
    assert sim.display.listening_sent.is_set()
    assert states == [False, True]  # sent at 1.3, plus 150 ms settle


def test_preroll_gives_up_after_listen_preroll_max_ms(monkeypatch):
    states = []
    events = [
        (0.0, True, 1, "ready"),
        (1.0, lambda sim: sim.display.arm_listening()),
        (1.0, True, 2, "listening"),
        (2.9, lambda sim: states.append(sim.display.preroll_ready())),
        (3.0, lambda sim: states.append(sim.display.preroll_ready())),
    ]

    def outcome(sim, call, rgb, kwargs):
        return (8.0, TimeoutError("no ACK")) if call == 2 else (0.3, None)

    sim = _preroll_sim(monkeypatch, events, {"listen_preroll_max_ms": 2000}, outcome)
    sim.run(4.0)
    assert states == [False, True]
    assert not sim.display.listening_sent.is_set()


def test_preroll_ignores_listening_frames_queued_before_arming(monkeypatch):
    events = [
        (0.0, True, 1, "listening"),
        (1.0, lambda sim: sim.display.arm_listening()),
    ]
    sim = _preroll_sim(monkeypatch, events)
    sim.run(2.0)
    assert not sim.display.listening_sent.is_set()
    assert not sim.display.preroll_ready()


def test_end_preroll_disarms_and_logs(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=D.LOG.name)
    sim = _preroll_sim(monkeypatch, [])
    d = sim.display
    assert d.preroll_ready()  # nothing armed
    d.arm_listening()
    assert not d.preroll_ready()
    d.end_preroll()
    d.end_preroll()
    assert d.preroll_ready()
    assert sum("listen preroll" in r.getMessage() for r in caplog.records) == 1


def test_preroll_is_off_by_default(monkeypatch):
    sim = Sim(D, monkeypatch, present_at=[(0.0, True, 1, "ready"), (1.0, True, 2, "listening")],
              outcome=ok())
    sim.run(2.0)
    assert sim.display.listen_preroll is False
    assert _queued(sim) == [(0, 1)]

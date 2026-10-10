"""hermes_minitoo.audio.Audio: microphone signals to the display, listen_preroll deferral."""

import logging

import pytest
from test_display_sim import ADDR, Sim

from hermes_minitoo import audio as A
from hermes_minitoo import display as D
from hermes_minitoo.platforms.linux.linkstate import Link


class FakeStream:
    def __init__(self, owner, kind, **kwargs):
        self.owner, self.kind, self.kwargs = owner, kind, kwargs
        self.active = False

    def start(self):
        if self.owner.fail:
            raise self.owner.sd.PortAudioError(self.owner.fail)
        self.active = True
        self.owner.opened.append((self.kind, self.kwargs["samplerate"]))

    def abort(self):
        self.active = False

    def close(self):
        self.active = False


class FakeSd:
    """Just enough of the sounddevice module for Audio."""

    def __init__(self, fail=None):
        self.fail = fail
        self.opened = []
        self.sd = self

    class PortAudioError(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    def check_input_settings(self, **kwargs):
        pass

    check_output_settings = check_input_settings

    def query_devices(self, device=None, kind=None):
        return {"name": str(device)}

    def RawInputStream(self, **kwargs):  # noqa: N802 - mirrors the sounddevice API
        return FakeStream(self, "input", **kwargs)

    def RawOutputStream(self, **kwargs):  # noqa: N802
        return FakeStream(self, "output", **kwargs)


class FakeDisplay:
    def __init__(self, preroll=False):
        self.listen_preroll = preroll
        self.mic = []
        self.armed = 0
        self.ready = False
        self.ended = 0

    def note_mic(self, is_open):
        self.mic.append(is_open)

    def arm_listening(self):
        self.armed += 1

    def preroll_ready(self):
        return self.ready

    def end_preroll(self):
        self.ended += 1


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSd()
    monkeypatch.setattr(A, "backend", lambda: fake)
    return fake


def make(display=None, **config):
    return A.Audio({"input": "in", "output": "out", "rate": 16000, **config}, display)


def test_without_a_display_the_mic_opens_directly(sd):
    audio = make()
    assert audio.mic_start(16000) is True
    assert sd.opened == [("input", 16000)]
    assert audio.read() == b""
    audio.mic_stop()


def test_mic_open_and_close_are_reported_to_the_display(sd):
    disp = FakeDisplay()
    audio = make(disp)
    assert audio.mic_start(16000) is True
    assert disp.mic == [True] and disp.armed == 0  # preroll is off: no deferral
    assert audio.mic_start(16000) is True  # restart closes the previous stream first
    assert disp.mic == [True, False, True]
    audio.mic_stop()
    audio.mic_stop()  # a second stop is a no-op
    assert disp.mic == [True, False, True, False]


def test_a_mic_that_failed_to_open_is_not_reported(sd):
    sd.fail = "device unavailable"
    disp = FakeDisplay()
    audio = make(disp)
    assert audio.mic_start(16000) is False
    audio.mic_stop()
    assert disp.mic == []
    assert audio.errors["input"] == "device unavailable"


def test_preroll_defers_the_real_mic_open_until_the_display_is_ready(sd):
    disp = FakeDisplay(preroll=True)
    audio = make(disp)
    audio.errors["input"] = "stale"
    assert audio.mic_start(16000) is True  # the core switches to Listening
    assert sd.opened == [] and disp.armed == 1 and "input" not in audio.errors
    assert audio.read() == b"" and sd.opened == []
    disp.ready = True
    assert audio.read() == b""
    assert sd.opened == [("input", 16000)] and disp.ended == 1 and disp.mic == [True]
    audio.read()
    assert sd.opened == [("input", 16000)]  # opened once
    audio.mic_stop()
    assert disp.mic == [True, False]


def test_preroll_open_failure_surfaces_as_an_input_error(sd):
    sd.fail = "device unavailable"
    disp = FakeDisplay(preroll=True)
    disp.ready = True
    audio = make(disp)
    assert audio.mic_start(16000) is True
    assert audio.read() == b""
    # Client.step() sees errors["input"] and cancels listening, as for a direct failure.
    assert audio.errors["input"] == "device unavailable"


def test_preroll_open_exception_does_not_escape_read(sd, monkeypatch, caplog):
    disp = FakeDisplay(preroll=True)
    disp.ready = True
    audio = make(disp)

    def broken(rate):
        raise RuntimeError("install libportaudio2 and the sounddevice package to use audio")

    monkeypatch.setattr(audio, "_open_mic", broken)
    assert audio.mic_start(16000) is True
    assert audio.read() == b""
    assert "libportaudio2" in audio.errors["input"]
    assert "deferred microphone open failed" in caplog.text


def test_preroll_stop_before_open_never_opens_the_mic(sd):
    disp = FakeDisplay(preroll=True)
    audio = make(disp)
    audio.mic_start(16000)
    audio.mic_stop()  # e.g. a too-short press
    disp.ready = True
    audio.read()
    assert sd.opened == [] and disp.ended == 1 and disp.mic == []


def test_preroll_without_a_ready_display_opens_on_the_first_read(sd):
    audio = make(FakeDisplay(preroll=True))
    audio.display = None
    audio._deferred_rate = 16000
    audio.read()
    assert sd.opened == [("input", 16000)]


def test_preroll_and_gate_with_a_real_display(sd, monkeypatch, caplog):
    """Press -> Listening frame uploaded -> 150 ms -> mic opens -> uploads held."""
    caplog.set_level(logging.INFO)
    probe = type("P", (), {"connections": lambda self: [Link(ADDR, "ACL", 1, True)],
                           "close": lambda self: None})()
    monkeypatch.setattr(D, "LinkProbe", lambda dev_id: probe)
    holder = []
    opened_at = []

    def read(sim):
        holder[0].read()
        if sd.opened and not opened_at:
            opened_at.append(sim.now())

    events = [(0.0, True, 1, "ready"),
              (1.0, lambda sim: holder[0].mic_start(16000)),
              (1.0, True, 2, "listening")]
    events += [(1.0 + i * 0.05, read) for i in range(1, 20)]
    events += [(2.0, True, 3, "listening"), (3.6, True, 4, "listening")]
    sim = Sim(D, monkeypatch, {"hfp_gate": True, "listen_preroll": True}, present_at=events)
    holder.append(A.Audio({"input": "in", "output": "out", "rate": 16000}, sim.display))
    sim.run(5.0)
    assert [(t, fid) for _, t, fid, _ in sim.sends()] == [(0.0, 1), (1.0, 2)]
    assert opened_at == [1.5]  # sent at 1.3 + 150 ms settle (reads every 50 ms)
    assert ("queued", 3600, 4) in sim.trace  # queued, but held: the mic is open
    assert "listen preroll: microphone after" in caplog.text
    assert "display paused: HFP/SCO" in caplog.text

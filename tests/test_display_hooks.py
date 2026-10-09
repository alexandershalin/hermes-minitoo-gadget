"""runtime.install_audio_hooks: opt-in wrappers around the SDK's Audio class."""

import logging
import sys

from test_display_sim import ADDR, Sim

from hermes_minitoo import display as D
from hermes_minitoo import runtime as R
from hermes_minitoo.linkstate import Link


def make_audio_cls(*, fail=False, without=()):
    """A stand-in for hermes_gadget.linux.audio.Audio 0.2.0 (same method contract)."""

    class Audio:
        def __init__(self):
            self.mic = None
            self.errors = {}
            self.input_buffer = bytearray()
            self.opened = []

        def mic_start(self, rate):
            self.mic_stop()
            self.errors.pop("input", None)
            if fail:
                self.errors["input"] = "device unavailable"
                self.mic_stop()
                return False
            self.mic = object()
            self.opened.append(rate)
            return True

        def mic_stop(self):
            self.mic = None
            self.input_buffer.clear()

        def read(self):
            data = bytes(self.input_buffer)
            self.input_buffer.clear()
            return data

    for name in without:
        delattr(Audio, name)
    return Audio


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


def test_default_config_installs_no_hooks():
    cls = make_audio_cls()
    before = dict(vars(cls))
    assert R.install_audio_hooks({"address": ADDR}, cls) is False
    assert R.install_audio_hooks({"address": ADDR, "hfp_gate": False, "listen_preroll": False},
                                 cls) is False
    assert dict(vars(cls)) == before


def test_missing_sdk_methods_are_skipped_with_a_warning(caplog):
    cls = make_audio_cls(without=("read",))
    before = dict(vars(cls))
    assert R.install_audio_hooks({"hfp_gate": True}, cls) is False
    assert dict(vars(cls)) == before
    assert "has no read" in caplog.text


def test_missing_sdk_module_is_skipped_with_a_warning(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "hermes_gadget.linux.audio", None)  # import fails
    assert R.install_audio_hooks({"hfp_gate": True}) is False
    assert "hooks not installed" in caplog.text


def test_gate_hooks_report_real_mic_open_and_close(monkeypatch):
    cls = make_audio_cls()
    original_read = cls.read
    assert R.install_audio_hooks({"hfp_gate": True}, cls) is True
    assert cls.read is original_read  # read() is wrapped only for listen_preroll
    disp = FakeDisplay()
    monkeypatch.setattr(D, "ACTIVE", disp)
    audio = cls()
    assert audio.mic_start(16000) is True
    assert audio.opened == [16000] and disp.mic == [True]
    assert audio.mic_start(16000) is True  # restart closes the previous stream first
    assert disp.mic == [True, False, True]
    audio.mic_stop()
    audio.mic_stop()
    assert disp.mic == [True, False, True, False]


def test_gate_hooks_do_not_report_a_mic_that_failed_to_open(monkeypatch):
    cls = make_audio_cls(fail=True)
    R.install_audio_hooks({"hfp_gate": True}, cls)
    disp = FakeDisplay()
    monkeypatch.setattr(D, "ACTIVE", disp)
    audio = cls()
    assert audio.mic_start(16000) is False
    audio.mic_stop()
    assert disp.mic == []


def test_hooks_pass_through_without_a_display(monkeypatch):
    cls = make_audio_cls()
    R.install_audio_hooks({"hfp_gate": True, "listen_preroll": True}, cls)
    monkeypatch.setattr(D, "ACTIVE", None)
    audio = cls()
    assert audio.mic_start(16000) is True and audio.opened == [16000]
    assert audio.read() == b""


def test_preroll_defers_the_real_mic_open_until_the_display_is_ready(monkeypatch):
    cls = make_audio_cls()
    R.install_audio_hooks({"hfp_gate": True, "listen_preroll": True}, cls)
    disp = FakeDisplay(preroll=True)
    monkeypatch.setattr(D, "ACTIVE", disp)
    audio = cls()
    audio.errors["input"] = "stale"
    assert audio.mic_start(16000) is True  # the core switches to Listening
    assert audio.opened == [] and disp.armed == 1 and "input" not in audio.errors
    assert audio.read() == b"" and audio.opened == []
    disp.ready = True
    assert audio.read() == b""
    assert audio.opened == [16000] and disp.ended == 1 and disp.mic == [True]
    audio.read()
    assert audio.opened == [16000]  # opened once
    audio.mic_stop()
    assert disp.mic == [True, False]


def test_preroll_open_failure_surfaces_as_an_input_error(monkeypatch):
    cls = make_audio_cls(fail=True)
    R.install_audio_hooks({"listen_preroll": True}, cls)
    disp = FakeDisplay(preroll=True)
    disp.ready = True
    monkeypatch.setattr(D, "ACTIVE", disp)
    audio = cls()
    assert audio.mic_start(16000) is True
    assert audio.read() == b""
    # Client.step() sees errors["input"] and cancels listening, as for a direct failure.
    assert audio.errors["input"] == "device unavailable"


def test_preroll_open_exception_does_not_escape_read(monkeypatch, caplog):
    cls = make_audio_cls()

    def broken_start(self, rate):
        raise RuntimeError("install the audio extra and libportaudio2 to use audio")

    cls.mic_start = broken_start
    R.install_audio_hooks({"listen_preroll": True}, cls)
    disp = FakeDisplay(preroll=True)
    disp.ready = True
    monkeypatch.setattr(D, "ACTIVE", disp)
    audio = cls()
    assert audio.mic_start(16000) is True
    assert audio.read() == b""
    assert "libportaudio2" in audio.errors["input"]
    assert "deferred microphone open failed" in caplog.text


def test_preroll_stop_before_open_never_opens_the_mic(monkeypatch):
    cls = make_audio_cls()
    R.install_audio_hooks({"hfp_gate": True, "listen_preroll": True}, cls)
    disp = FakeDisplay(preroll=True)
    monkeypatch.setattr(D, "ACTIVE", disp)
    audio = cls()
    audio.mic_start(16000)
    audio.mic_stop()  # e.g. a too-short press
    disp.ready = True
    audio.read()
    assert audio.opened == [] and disp.ended == 1 and disp.mic == []


def test_installing_twice_wraps_once():
    cls = make_audio_cls()
    assert R.install_audio_hooks({"hfp_gate": True}, cls)
    wrapped = cls.mic_start
    assert R.install_audio_hooks({"hfp_gate": True}, cls)
    assert cls.mic_start is wrapped


def test_preroll_and_gate_with_a_real_display(monkeypatch, caplog):
    """Press -> Listening frame uploaded -> 150 ms -> mic opens -> uploads held."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(D, "ACTIVE", None)
    cls = make_audio_cls()
    R.install_audio_hooks({"hfp_gate": True, "listen_preroll": True}, cls)
    probe = type("P", (), {"connections": lambda self: [Link(ADDR, "ACL", 1, True)],
                           "close": lambda self: None})()
    monkeypatch.setattr(D, "LinkProbe", lambda dev_id: probe)
    audio = cls()
    opened_at = []

    def read(sim):
        audio.read()
        if audio.opened and not opened_at:
            opened_at.append(sim.now())

    events = [(0.0, True, 1, "ready"),
              (1.0, lambda sim: audio.mic_start(16000)),
              (1.0, True, 2, "listening")]
    events += [(1.0 + i * 0.05, read) for i in range(1, 20)]
    events += [(2.0, True, 3, "listening"), (3.6, True, 4, "listening")]
    sim = Sim(D, monkeypatch, {"hfp_gate": True, "listen_preroll": True}, present_at=events)
    sim.run(5.0)
    assert [(t, fid) for _, t, fid, _ in sim.sends()] == [(0.0, 1), (1.0, 2)]
    assert opened_at == [1.5]  # sent at 1.3 + 150 ms settle (reads every 50 ms)
    assert ("queued", 3600, 4) in sim.trace  # queued, but held: the mic is open
    assert "listen preroll: microphone after" in caplog.text
    assert "display paused: HFP/SCO" in caplog.text


def _fake_sdk(monkeypatch, audio_cls):
    import types

    calls = {}
    names = ("hermes_gadget", "hermes_gadget.linux", "hermes_gadget.linux.display",
             "hermes_gadget.linux.control", "hermes_gadget.linux.audio")
    modules = {name: types.ModuleType(name) for name in names}
    modules["hermes_gadget.linux.display"].Display = object
    modules["hermes_gadget.linux.audio"].Audio = audio_cls
    modules["hermes_gadget.linux.control"].run = lambda config, state_dir, stop: calls.update(
        config=config, state_dir=state_dir)
    modules["hermes_gadget"].linux = modules["hermes_gadget.linux"]
    for short in ("display", "control", "audio"):
        setattr(modules["hermes_gadget.linux"], short, modules[f"hermes_gadget.linux.{short}"])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return modules, calls


def _config(tmp_path, **minitoo):
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server": "ws://127.0.0.1:8765/gadget",
                                "minitoo": {"address": ADDR, **minitoo}}))
    return path


def test_run_with_the_default_config_installs_no_audio_hooks(monkeypatch, tmp_path):
    cls = make_audio_cls()
    before = dict(vars(cls))
    modules, calls = _fake_sdk(monkeypatch, cls)
    R.run(_config(tmp_path), tmp_path / "state")
    assert dict(vars(cls)) == before
    assert modules["hermes_gadget.linux.display"].Display is D.MiniTooDisplay
    assert calls["config"]["display"] == {"address": ADDR}


def test_run_with_hfp_gate_installs_the_audio_hooks(monkeypatch, tmp_path):
    cls = make_audio_cls()
    _fake_sdk(monkeypatch, cls)
    R.run(_config(tmp_path, hfp_gate=True), tmp_path / "state")
    assert getattr(cls, "_minitoo_hooked", False) is True

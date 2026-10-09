import importlib.util
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "minitoo-talk-key.py"
_sp = importlib.util.spec_from_file_location("talk_key", _p)
K = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(K)

# origin/main 21af541 scripts/minitoo-talk-key.py: the hard-coded paths the defaults must reproduce
OLD_PATHS = {
    "bin": "/home/bishop/hermes-minitoo-gadget/.venv/bin/hermes-minitoo",
    "tts_python": "/home/bishop/.hermes/installs/20715197cc5be820/environments/"
                  "738223755d2649faa3439a3b8f7036ae/venv/bin/python",
    "tts_client": "/home/bishop/.local/bin/piper-tts-client.py",
    "speak_wav": "/home/bishop/.cache/minitoo-speak.wav",
    "config": "/home/bishop/hermes-minitoo-gadget/config.json",
    "ffmpeg": "/home/bishop/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg",
    "indicator": "/home/bishop/.cache/minitoo-indicator",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "CFG", K.load_settings({"HOME": str(tmp_path)}, {}))
    monkeypatch.setattr(K, "SHUTDOWN", threading.Event())
    monkeypatch.setattr(K, "_LINK", None)


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(K, "log", out.append)
    return out


def _state():
    return {"phase": "idle", "end": 0.0, "stop": threading.Event()}


def _write_config(tmp_path, **extra):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "server": "ws://127.0.0.1:1", "minitoo": {"address": "B1:21:81:A0:78:53"},
        "audio": {"output": "bluez_output.B1:21:81:A0:78:53", "input": "bluez_input.B1:21:81:A0:78:53"},
        **extra}), encoding="utf-8")
    K.CFG.config = str(path)
    return path


def _wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# --- the original tests ---------------------------------------------------------------------

def test_first_press_starts_and_second_stops(monkeypatch):
    started = threading.Event()

    def fake_worker(st):
        st["phase"] = "recording"
        started.set()
        st["stop"].wait(2)
        st["phase"] = "idle"
        st["end"] = time.time()

    monkeypatch.setattr(K, "worker", fake_worker)
    st = _state()
    assert K.on_press(st) == "start"
    assert started.wait(1)
    assert K.on_press(st) == "stop"
    assert st["stop"].is_set()


def test_press_while_starting_is_ignored(monkeypatch):
    monkeypatch.setattr(K, "worker", lambda st: None)
    st = _state()
    st["phase"] = "starting"
    assert K.on_press(st) == "ignored"


def test_debounce_after_end():
    st = _state()
    st["end"] = time.time()
    assert K.on_press(st) == "debounce"


# --- settings and paths ---------------------------------------------------------------------

def test_defaults_resolve_to_the_old_hardcoded_paths():
    s = K.load_settings({"HOME": "/home/bishop"}, {})
    for name, old in OLD_PATHS.items():
        assert getattr(s, name) == old, name
    assert s.state_dir == "/home/bishop/.local/state/hermes-minitoo-gadget"
    assert s.cli_state_dir is None  # the CLI is called exactly as before
    assert s.tts_voice == "dmitri"
    assert (s.control, s.press_during_start) == ("cli", "ignored")
    assert not (s.hfp_keys or s.hfp_keys_start or s.preroll_wait_sco or s.vad_adaptive)
    assert (s.silence_s, s.nospeech_s, s.max_s, s.stall_s) == (1.5, 12.0, 30.0, 30.0)
    assert (s.vad_warmup_s, s.vad_calib_s, s.vad_floor, s.vad_mult) == (0.0, 0.8, 250.0, 3.0)


def test_default_state_dir_matches_the_cli(monkeypatch):
    from hermes_minitoo.cli import default_state_dir

    monkeypatch.setenv("HOME", "/home/bishop")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert K.load_settings(dict(os.environ), {}).state_dir == str(default_state_dir())
    monkeypatch.setenv("XDG_STATE_HOME", "/srv/state")
    assert K.load_settings(dict(os.environ), {}).state_dir == str(default_state_dir())


def test_script_has_no_hardcoded_home():
    assert "/home/bishop" not in _p.read_text(encoding="utf-8")


def test_repo_and_config_come_from_env():
    s = K.load_settings({"HOME": "/h", "MINITOO_REPO": "~/src/gadget"}, {})
    assert s.repo == "/h/src/gadget"
    assert s.config == "/h/src/gadget/config.json"
    assert s.bin == "/h/src/gadget/.venv/bin/hermes-minitoo"
    s = K.load_settings({"HOME": "/h", "MINITOO_CONFIG": "/etc/minitoo.json"}, {"config": "/x"})
    assert s.config == "/etc/minitoo.json"


def test_env_wins_over_talk_key_and_talk_key_over_defaults():
    tk = {"control": "socket", "silence_s": 2, "vad_adaptive": True, "speak_wav": "~/p.wav",
          "tts_voice": "irina"}
    s = K.load_settings({"HOME": "/h", "MINITOO_SILENCE_S": "0.9", "MINITOO_VAD_ADAPTIVE": "0"}, tk)
    assert s.control == "socket"
    assert s.silence_s == 0.9
    assert s.vad_adaptive is False
    assert s.speak_wav == "/h/p.wav"
    assert s.tts_voice == "irina"


def test_bad_values_fall_back_to_defaults_and_are_logged(logs):
    s = K.load_settings({"HOME": "/h", "MINITOO_MAX_S": "lots", "MINITOO_HFP_KEYS": "maybe"},
                        {"control": "tcp", "nospeech_s": -1, "vad_adaptive": "yes", "typo_key": 1,
                         "preroll_wait_sco": 1, "speak_wav": 5, "silence_s": None})
    assert (s.max_s, s.hfp_keys, s.control, s.nospeech_s, s.vad_adaptive) == (30.0, False, "cli", 12.0, False)
    assert (s.preroll_wait_sco, s.speak_wav, s.silence_s) == (False, "/h/.cache/minitoo-speak.wav", 1.5)
    text = "\n".join(logs)
    for name in ("MINITOO_MAX_S", "MINITOO_HFP_KEYS", "talk_key.control", "talk_key.nospeech_s",
                 "talk_key.vad_adaptive", "talk_key.typo_key", "talk_key.preroll_wait_sco",
                 "talk_key.speak_wav"):
        assert name in text


def test_bool_env_values():
    for raw, want in (("1", True), ("true", True), ("ON", True), ("0", False), ("no", False), ("", False)):
        assert K.load_settings({"HOME": "/h", "MINITOO_HFP_KEYS": raw}, {}).hfp_keys is want


def test_stall_defaults_to_max_s():
    assert K.load_settings({"HOME": "/h", "MINITOO_MAX_S": "60"}, {}).stall_s == 60.0
    assert K.load_settings({"HOME": "/h", "MINITOO_STALL_S": "5"}, {}).stall_s == 5.0


def test_state_dir_override_is_passed_to_the_cli():
    s = K.load_settings({"HOME": "/h", "MINITOO_STATE_DIR": "/run/gadget"}, {})
    assert (s.state_dir, s.cli_state_dir) == ("/run/gadget", "/run/gadget")
    s = K.load_settings({"HOME": "/h", "XDG_STATE_HOME": "/xdg"}, {})
    assert (s.state_dir, s.cli_state_dir) == ("/xdg/hermes-minitoo-gadget", None)


def test_startup_settings_read_talk_key_and_log_only_changes(tmp_path, logs):
    cfg = tmp_path / "hermes-minitoo-gadget" / "config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"talk_key": {"vad_adaptive": True}}), encoding="utf-8")
    s = K._startup_settings({"HOME": str(tmp_path)})
    assert s.vad_adaptive is True
    assert logs == ["настройки: vad_adaptive=True"]
    logs.clear()
    cfg.write_text(json.dumps({"server": "ws://x"}), encoding="utf-8")
    K._startup_settings({"HOME": str(tmp_path)})
    assert logs == []  # defaults: nothing new in the journal


def test_import_has_no_side_effects(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("I/O at import time")

    monkeypatch.setattr("builtins.open", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(signal, "signal", boom)
    threads = threading.active_count()
    spec = importlib.util.spec_from_file_location("talk_key_fresh", _p)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh.CFG is None and not fresh.SHUTDOWN.is_set()
    assert threading.active_count() == threads


# --- buttons through the CLI ----------------------------------------------------------------

def test_cli_call_is_unchanged_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(K.subprocess, "run", lambda *a, **k: calls.append((a, k)) or _Result())
    assert K.button("press") is True
    assert calls == [(([K.CFG.bin, "button", "talk", "press"],),
                      {"capture_output": True, "text": True, "timeout": 20})]


def test_cli_gets_state_dir_only_when_overridden(monkeypatch, tmp_path):
    K.CFG = K.load_settings({"HOME": str(tmp_path), "MINITOO_STATE_DIR": "/run/g"}, {})
    calls = []
    monkeypatch.setattr(K.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _Result())
    K.button("release")
    assert calls == [[K.CFG.bin, "--state-dir", "/run/g", "button", "talk", "release"]]


def test_cli_error_text_from_stdout_is_logged(monkeypatch, logs):
    out = "hermes-minitoo: [Errno 2] No such file or directory\n"
    monkeypatch.setattr(K.subprocess, "run", lambda *a, **k: _Result(1, stdout=out))
    assert K._button_cli("press") is False
    assert any("No such file" in line for line in logs)


def test_cli_timeout_and_missing_binary_are_caught(monkeypatch, logs):
    def timeout(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 20, output=b"partial out", stderr=None)

    monkeypatch.setattr(K.subprocess, "run", timeout)
    assert K._button_cli("press") is False
    assert "таймаут 20 с partial out" in logs[-1]

    def missing(cmd, **k):
        raise FileNotFoundError(2, "No such file", cmd[0])

    monkeypatch.setattr(K.subprocess, "run", missing)
    assert K._button_cli("release") is False
    assert "No such file" in logs[-1]


def test_release_is_sent_even_when_press_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(K, "button", lambda state, which="talk": calls.append(state) or state != "press")
    K.record_once(_state())
    assert calls == ["press", "release"]


def test_release_is_sent_when_the_press_cli_times_out(monkeypatch):
    calls = []

    def run(cmd, **k):
        calls.append(cmd[-1])
        if cmd[-1] == "press":
            raise subprocess.TimeoutExpired(cmd, 20)
        return _Result()

    monkeypatch.setattr(K.subprocess, "run", run)
    st = _state()
    K.worker(st)
    assert calls == ["press", "release"]
    assert st["phase"] == "idle" and st["end"] > 0


# --- one recording ----------------------------------------------------------------------------

def _fake_recording(monkeypatch, tmp_path, vad=lambda stop: "тишина после речи"):
    _write_config(tmp_path)
    wav = tmp_path / "speak.wav"
    wav.write_bytes(b"RIFF" + bytes(100))
    K.CFG.speak_wav = str(wav)
    calls = []

    def run(cmd, **k):
        calls.append(("run", cmd, k))
        if cmd == ["pw-dump"]:
            return _Result(stdout="[]")
        return _Result()

    monkeypatch.setattr(K.subprocess, "run", run)
    monkeypatch.setattr(K, "button",
                        lambda state, which="talk": calls.append(("button", which, state)) or True)
    monkeypatch.setattr(K, "vad_wait", lambda stop: calls.append(("vad",)) or vad(stop))
    monkeypatch.setattr(K, "_log_status_async", lambda tag: calls.append(("status", tag)))
    monkeypatch.setattr(K, "wait_sco", lambda stop: calls.append(("wait_sco",)))
    return calls, str(wav)


def test_default_recording_runs_the_same_commands_in_the_same_order(monkeypatch, tmp_path):
    calls, wav = _fake_recording(monkeypatch, tmp_path)
    st = _state()
    K.record_once(st)
    assert calls == [
        ("button", "talk", "press"),
        ("status", "press"),
        ("run", ["pw-play", "--target=bluez_output.B1:21:81:A0:78:53", wav],
         {"timeout": 15, "capture_output": True, "text": True}),
        ("run", ["pw-dump"], {"capture_output": True, "text": True, "timeout": 5}),
        ("vad",),
        ("status", "vad-end"),
        ("button", "talk", "release"),
    ]
    assert st["phase"] == "recording"  # worker() sets idle


def test_preroll_wait_sco_runs_between_press_and_prompt_only_when_enabled(monkeypatch, tmp_path):
    calls, _ = _fake_recording(monkeypatch, tmp_path)
    K.CFG.preroll_wait_sco = True
    K.record_once(_state())
    names = [c[0] if c[0] != "run" else c[1][0] for c in calls]
    assert names[:4] == ["button", "status", "wait_sco", "pw-play"]


def test_missing_prompt_is_skipped_not_fatal(monkeypatch, tmp_path, logs):
    calls, wav = _fake_recording(monkeypatch, tmp_path)
    os.remove(wav)
    K.record_once(_state())
    assert not any(c[0] == "run" and c[1][0] == "pw-play" for c in calls)
    assert ("button", "talk", "release") == calls[-1]


def test_worker_sets_end_before_idle(monkeypatch):
    class Recorder(dict):
        order = []

        def __setitem__(self, key, value):
            self.order.append(key)
            super().__setitem__(key, value)

    monkeypatch.setattr(K, "record_once", lambda st: None)
    st = Recorder(_state())
    K.worker(st)
    assert st.order[-2:] == ["end", "phase"]
    assert st["phase"] == "idle"


def test_on_press_waits_for_the_phase_lock(monkeypatch):
    monkeypatch.setattr(K, "worker", lambda st: None)
    st = _state()
    result = []
    with K.LOCK:
        th = threading.Thread(target=lambda: result.append(K.on_press(st)))
        th.start()
        time.sleep(0.1)
        assert result == []
    th.join(1)
    assert result == ["start"]


def test_shutdown_finishes_the_recording_with_a_release(monkeypatch, tmp_path):
    calls, _ = _fake_recording(monkeypatch, tmp_path, vad=lambda stop: "кнопка" if stop.wait(5) else "x")
    st = _state()
    assert K.on_press(st) == "start"
    assert _wait_for(lambda: ("vad",) in calls)
    t0 = time.monotonic()
    K.shutdown(st, timeout=3)
    assert time.monotonic() - t0 < 2
    assert calls[-1] == ("button", "talk", "release")
    assert st["phase"] == "idle"
    assert K.on_press(st) == "ignored"  # no new recording once shutting down


def test_signal_handler_only_sets_the_shutdown_flag():
    K._on_signal(signal.SIGTERM, None)
    assert K.SHUTDOWN.is_set()


def test_main_default_flow_starts_no_extra_threads(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(K.signal, "signal", lambda sig, handler: calls.append(("signal", sig)))
    monkeypatch.setattr(K, "_startup_settings", lambda env: K.load_settings({"HOME": str(tmp_path)}, {}))
    monkeypatch.setattr(K, "ensure_wav", lambda: calls.append("ensure_wav") or True)
    monkeypatch.setattr(K, "set_ind", lambda state: calls.append("set_ind"))
    monkeypatch.setattr(K, "button", lambda state, which="talk": calls.append(("button", which, state)))
    monkeypatch.setattr(K, "listen", lambda st: calls.append("listen"))
    monkeypatch.setattr(K, "hfp_follow", lambda st: calls.append("hfp_follow"))
    monkeypatch.setattr(K, "wp_log_level_keeper", lambda: calls.append("wp_log_level_keeper"))
    monkeypatch.setattr(K, "_retry_wav", lambda: calls.append("retry_wav"))
    assert K.main() == 0
    time.sleep(0.05)
    assert calls == [("signal", signal.SIGTERM), ("signal", signal.SIGINT), "ensure_wav", "set_ind",
                     ("button", "talk", "release"), "listen"]


def test_main_starts_hfp_threads_and_wav_retry_only_when_needed(monkeypatch, tmp_path):
    seen = []
    done = threading.Event()

    def note(name):
        seen.append(name)
        if len(seen) == 3:
            done.set()

    monkeypatch.setattr(K.signal, "signal", lambda sig, handler: None)
    monkeypatch.setattr(K, "_startup_settings",
                        lambda env: K.load_settings({"HOME": str(tmp_path), "MINITOO_HFP_KEYS": "1"}, {}))
    monkeypatch.setattr(K, "ensure_wav", lambda: False)
    monkeypatch.setattr(K, "set_ind", lambda state: None)
    monkeypatch.setattr(K, "button", lambda state, which="talk": True)
    monkeypatch.setattr(K, "listen", lambda st: done.wait(2))
    monkeypatch.setattr(K, "hfp_follow", lambda st: note("hfp_follow"))
    monkeypatch.setattr(K, "wp_log_level_keeper", lambda: note("wp_log_level_keeper"))
    monkeypatch.setattr(K, "_retry_wav", lambda: note("retry_wav"))
    K.main()
    assert sorted(seen) == ["hfp_follow", "retry_wav", "wp_log_level_keeper"]


# --- input devices ----------------------------------------------------------------------------

DEVICES = """I: Bus=0005 Vendor=05d6 Product=000a Version=0240
N: Name="Divoom MiniToo-Audio (AVRCP)"
P: Phys=00:11:22:33:44:55
S: Sysfs=/devices/virtual/input/input30
U: Uniq=
H: Handlers=kbd event14
B: PROP=0
B: EV=100007

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver"
H: Handlers=sysrq kbd leds event3

I: Bus=0005 Vendor=05d6 Product=000a Version=0240
N: Name="Divoom MiniToo-App (AVRCP)"
H: Handlers=kbd event11

I: Bus=0005 Vendor=05d6 Product=000a Version=0240
N: Name="Divoom MiniToo Keyboard"
H: Handlers=kbd event12
"""


def test_find_events_lists_every_minitoo_avrcp_device(tmp_path):
    path = tmp_path / "devices"
    path.write_text(DEVICES)
    assert K.find_events(str(path)) == {
        "/dev/input/event11": "Divoom MiniToo-App (AVRCP)",
        "/dev/input/event14": "Divoom MiniToo-Audio (AVRCP)",
    }


def test_find_events_matches_ids_even_when_renamed_and_falls_back_to_the_name(tmp_path):
    path = tmp_path / "devices"
    path.write_text('I: Bus=0005 Vendor=05D6 Product=000A Version=0240\nN: Name="Speaker (AVRCP)"\n'
                    'H: Handlers=kbd event5 \n\n'
                    'I: Bus=0005 Vendor=1234 Product=0001 Version=0001\nN: Name="MiniToo (AVRCP)"\n'
                    'H: Handlers=kbd event6 \n')
    assert list(K.find_events(str(path))) == ["/dev/input/event5"]
    path.write_text('I: Bus=0005 Vendor=1234 Product=0001 Version=0001\nN: Name="MiniToo (AVRCP)"\n'
                    'H: Handlers=kbd event6 \n')
    assert list(K.find_events(str(path))) == ["/dev/input/event6"]
    assert K.find_events(str(tmp_path / "missing")) == {}


def test_event_struct_matches_kernel_input_event():
    assert K.EVENT.size == 24  # x86_64: timeval(16) + u16 + u16 + s32


def test_handle_events_presses_only_on_key_down(monkeypatch, logs):
    presses = []
    monkeypatch.setattr(K, "on_press", lambda st: presses.append(1) or "start")
    now = time.time()
    data = b"".join(K.EVENT.pack(int(now), 0, typ, code, val)
                    for typ, code, val in ((1, 200, 1), (0, 0, 0), (1, 200, 0), (1, 200, 2), (1, 165, 1)))
    K.handle_events(_state(), data)
    assert presses == [1]
    assert [line.split(" phase")[0] for line in logs] == [
        "key code=200 val=1", "key code=200 val=0", "key code=200 val=2", "key code=165 val=1"]


def test_listen_reads_every_device_and_rescans_after_it_vanishes(monkeypatch, tmp_path, logs):
    fifo = tmp_path / "event7"
    os.mkfifo(fifo)
    scans = []

    def find():
        scans.append(time.monotonic())
        return {str(fifo): "Divoom MiniToo-App (AVRCP)"}

    presses = []
    monkeypatch.setattr(K, "find_events", find)
    monkeypatch.setattr(K, "on_press", lambda st: presses.append(1) or "start")
    monkeypatch.setattr(K, "RESCAN_S", 0.2)
    th = threading.Thread(target=K.listen, args=(_state(),))
    th.start()
    writer = None
    try:
        assert _wait_for(lambda: any("слушаю" in line for line in logs))
        writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        os.write(writer, K.EVENT.pack(int(time.time()), 0, 1, 164, 1))
        assert _wait_for(lambda: presses == [1])
        n = len(scans)
        assert _wait_for(lambda: len(scans) > n)  # periodic rescan while a device is open

        def gone(fd):
            raise OSError(19, "No such device")

        monkeypatch.setattr(K, "_read_events", gone)
        os.write(writer, K.EVENT.pack(int(time.time()), 0, 1, 164, 1))
        assert _wait_for(lambda: any("пропало" in line for line in logs))
        assert _wait_for(lambda: sum("слушаю" in line for line in logs) >= 2)  # reopened after the error
    finally:
        K.SHUTDOWN.set()
        th.join(3)
        if writer is not None:
            os.close(writer)
    assert not th.is_alive()


# --- the speaker profile ----------------------------------------------------------------------

PW_DUMP = [
    {"id": 0, "type": "PipeWire:Interface:Core", "info": {"props": {}}},
    {"id": 41, "type": "PipeWire:Interface:Device", "info": None},
    {"id": 50, "type": "PipeWire:Interface:Device",
     "info": {"props": {"device.name": "bluez_card.11_22_33_44_55_66",
                        "api.bluez5.address": "11:22:33:44:55:66"},
              "params": {"Profile": [{"index": 1, "name": "a2dp-sink"}]}}},
    {"id": 60, "type": "PipeWire:Interface:Device",
     "info": {"props": {"device.name": "bluez_card.B1_21_81_A0_78_53"},
              "params": {"Profile": [{"index": 2, "name": "headset-head-unit"}]}}},
]


def test_profile_reports_the_minitoo_card_not_the_first_card(monkeypatch, tmp_path):
    _write_config(tmp_path)
    monkeypatch.setattr(K.subprocess, "run", lambda *a, **k: _Result(stdout=json.dumps(PW_DUMP)))
    assert K._profile() == "headset-head-unit"
    assert K._profile("11:22:33:44:55:66") == "a2dp-sink"
    assert K._profile("AA:AA:AA:AA:AA:AA") is None


# --- the prompt -------------------------------------------------------------------------------

def _fake_tts(monkeypatch, tts_rc=0, ffmpeg=lambda cmd: _Result()):
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if cmd[0] == K.CFG.tts_python:
            assert pathlib.Path(cmd[2]).read_text(encoding="utf-8") == "Говорите!"
            assert cmd[2] != "/tmp/minitoo-speak.txt"
            if tts_rc:
                raise subprocess.CalledProcessError(tts_rc, cmd)
            pathlib.Path(cmd[3]).write_bytes(b"RAW" + bytes(100))
            return _Result()
        return ffmpeg(cmd)

    monkeypatch.setattr(K.subprocess, "run", run)
    return calls


def test_ensure_wav_is_atomic_and_uses_the_old_commands(monkeypatch, tmp_path):
    wav = tmp_path / "cache" / "minitoo-speak.wav"
    K.CFG.speak_wav = str(wav)

    def ffmpeg(cmd):
        pathlib.Path(cmd[-1]).write_bytes(b"PADDED" + bytes(200))
        return _Result()

    calls = _fake_tts(monkeypatch, ffmpeg=ffmpeg)
    assert K.ensure_wav() is True
    tts, ff = calls
    assert tts[:2] == [K.CFG.tts_python, K.CFG.tts_client] and tts[3:] == [str(wav) + ".orig", "dmitri"]
    assert ff[:-1] == [K.CFG.ffmpeg, "-loglevel", "error", "-y", "-i", str(wav) + ".orig", "-af",
                       "adelay=900:all=1,apad=pad_dur=0.3", "-ar", "48000"]
    assert ff[-1] != str(wav) and ff[-1].endswith(".wav") and os.path.dirname(ff[-1]) == str(wav.parent)
    assert wav.read_bytes().startswith(b"PADDED")
    assert not os.path.exists(tts[2])
    assert sorted(p.name for p in wav.parent.iterdir()) == ["minitoo-speak.wav", "minitoo-speak.wav.orig"]
    assert K.ensure_wav() is True and len(calls) == 2  # already there: nothing runs


def test_ensure_wav_regenerates_an_empty_file_and_falls_back_to_the_raw_tts(monkeypatch, tmp_path):
    wav = tmp_path / "minitoo-speak.wav"
    wav.write_bytes(bytes(44))
    K.CFG.speak_wav = str(wav)
    _fake_tts(monkeypatch, ffmpeg=lambda cmd: _Result(1))
    assert K.ensure_wav() is True
    assert wav.read_bytes().startswith(b"RAW")
    wav.write_bytes(bytes(10))

    def missing(cmd):
        raise FileNotFoundError(2, "No such file", cmd[0])

    _fake_tts(monkeypatch, ffmpeg=missing)
    assert K.ensure_wav() is True
    assert wav.read_bytes().startswith(b"RAW")


def test_ensure_wav_failure_is_not_fatal(monkeypatch, tmp_path, logs):
    wav = tmp_path / "minitoo-speak.wav"
    K.CFG.speak_wav = str(wav)
    _fake_tts(monkeypatch, tts_rc=1)
    assert K.ensure_wav() is False
    assert not wav.exists() and list(tmp_path.iterdir()) == []
    assert "не создана" in logs[-1]


def test_wav_is_retried_in_the_background_until_it_works(monkeypatch):
    results = iter([False, False, True])
    waits = []

    class Stop:
        def wait(self, timeout):
            waits.append(timeout)
            return False

    monkeypatch.setattr(K, "SHUTDOWN", Stop())
    monkeypatch.setattr(K, "ensure_wav", lambda: next(results))
    K._retry_wav()
    assert waits == [30, 60, 120]


# --- preroll_wait_sco -------------------------------------------------------------------------

class _Probe:
    def __init__(self, up_after):
        self.calls, self.up_after = [], up_after

    def sco_up(self, address):
        self.calls.append(address)
        return len(self.calls) > self.up_after


def test_wait_sco_polls_the_link_probe(tmp_path, logs):
    _write_config(tmp_path)
    probe = _Probe(up_after=2)
    K._LINK = probe
    assert K.wait_sco(threading.Event()) is True
    assert probe.calls == ["B1:21:81:A0:78:53"] * 3
    assert "SCO к колонке есть" in logs[-1]


def test_wait_sco_gives_up_and_honours_stop(tmp_path, logs):
    _write_config(tmp_path)
    K._LINK = _Probe(up_after=10 ** 6)
    t0 = time.monotonic()
    assert K.wait_sco(threading.Event(), max_s=0.2) is False
    assert 0.15 < time.monotonic() - t0 < 1.0
    stop = threading.Event()
    stop.set()
    assert K.wait_sco(stop) is False


def test_sco_check_imports_linkstate_from_the_repo(monkeypatch, tmp_path):
    import types

    import hermes_minitoo

    probe = _Probe(up_after=0)
    link = types.SimpleNamespace(connections=list, sco_up=probe.sco_up)
    seen = []
    fake = types.SimpleNamespace(LinkProbe=lambda dev_id=0: (seen.append(dev_id), link)[1])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "hermes_minitoo.linkstate", fake)
    monkeypatch.setattr(hermes_minitoo, "linkstate", fake, raising=False)
    check, step = K._sco_check()
    assert check("AA:BB:CC:DD:EE:FF") is True and step == 0.05
    assert os.path.join(K.CFG.repo, "src") in sys.path
    assert seen == [0]  # no minitoo.hci_dev in the config: hci0, as before


def test_minitoo_hci_dev_comes_from_the_same_config_as_the_display(tmp_path):
    path = _write_config(tmp_path)
    assert K._minitoo_hci_dev() == 0
    cfg = json.loads(path.read_text())
    cfg["minitoo"]["hci_dev"] = 1
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert K._minitoo_hci_dev() == 1
    cfg["minitoo"]["hci_dev"] = "1"  # invalid values fall back to hci0
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert K._minitoo_hci_dev() == 0


def test_sco_check_falls_back_to_the_card_profile(monkeypatch, logs):
    import hermes_minitoo

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "hermes_minitoo.linkstate", None)  # import fails
    monkeypatch.delattr(hermes_minitoo, "linkstate", raising=False)
    profiles = iter(["a2dp-sink", "headset-head-unit"])
    monkeypatch.setattr(K, "_profile", lambda addr=None: next(profiles))
    check, step = K._sco_check()
    assert step == 0.25 and check("X") is False and check("X") is True
    assert "linkstate недоступен" in logs[0]

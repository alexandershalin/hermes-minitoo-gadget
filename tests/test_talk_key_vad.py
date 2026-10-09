"""VAD of scripts/minitoo-talk-key.py: identical decisions by default, no hangs, opt-in modes."""

import array
import ast
import importlib.util
import math
import os
import pathlib
import random
import threading
import time
import types

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "minitoo-talk-key.py"
_sp = importlib.util.spec_from_file_location("talk_key_vad", _p)
K = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(K)

# origin/main 21af541 scripts/minitoo-talk-key.py lines 54-86, verbatim.
LEGACY_VAD_WAIT = '''
def vad_wait(stop_evt=None, max_s=MAX_REC_S, silence_s=1.5, nospeech_s=12.0):
    """Ждёт конец фразы по тишине. Возвращает причину остановки."""
    proc = subprocess.Popen(["pw-record", f"--target={_audio('input')}", "--rate", "16000",
                             "--channels", "1", "--format", "s16", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunk = 3200  # 100 мс
    t0 = time.time(); noise = []; thr = None; speech = False; last_voice = t0
    try:
        while time.time() - t0 < max_s:
            buf = proc.stdout.read(chunk)
            if stop_evt is not None and stop_evt.is_set():
                return "кнопка"
            if not buf or len(buf) < chunk:
                return "поток закрыт"
            a = array.array("h"); a.frombytes(buf)
            rms = math.sqrt(sum(x * x for x in a) / len(a))
            now = time.time()
            if thr is None:
                noise.append(rms)
                if len(noise) >= 8:
                    thr = max(250.0, 3.0 * sorted(noise)[len(noise) // 2])
                    log(f"VAD порог={thr:.0f}")
                continue
            if rms > thr:
                speech = True; last_voice = now
            if speech and now - last_voice > silence_s:
                return "тишина после речи"
            if not speech and now - t0 > nospeech_s:
                return "речи нет"
        return "максимум"
    finally:
        proc.kill()
        proc.wait()
'''


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "CFG", K.load_settings({"HOME": str(tmp_path)}, {}))
    monkeypatch.setattr(K, "_audio", lambda key: "bluez_input.X")


_TONES = {}


def tone(rms):
    """100 ms of a 300 Hz sine with the given RMS (s16 mono 16 kHz)."""
    key = int(rms)
    if key not in _TONES:
        amp = key * math.sqrt(2)
        samples = (int(amp * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(1600))
        _TONES[key] = array.array("h", [max(-32768, min(32767, x)) for x in samples]).tobytes()
    return _TONES[key]


class Clock:
    """time stand-in: every time() call advances 50 ms (two calls per 100 ms chunk in both versions)."""

    monotonic = staticmethod(time.monotonic)  # before def time(), which shadows the module here
    strftime = staticmethod(time.strftime)
    sleep = staticmethod(time.sleep)

    def __init__(self, stop_evt=None, stop_at_call=None):
        self.t, self.calls = 1_000_000.0, 0
        self.stop_evt, self.stop_at_call = stop_evt, stop_at_call

    def time(self):
        self.calls += 1
        if self.stop_at_call is not None and self.calls >= self.stop_at_call:
            self.stop_evt.set()
        self.t += 0.05
        return self.t


class FilePopen:
    """pw-record stand-in reading PCM from a regular file (select() is always ready: deterministic)."""

    def __init__(self, path):
        self.stdout = open(path, "rb")

    def kill(self):
        pass

    def wait(self):
        return 0


def _fake_subprocess(popen):
    return types.SimpleNamespace(Popen=lambda *a, **k: popen, PIPE=-1, DEVNULL=-3)


def run_legacy(path, stop_evt=None, stop_at_call=None):
    logs, popen = [], FilePopen(path)
    clock = Clock(stop_evt, stop_at_call)
    ns = {"subprocess": _fake_subprocess(popen), "_audio": lambda key: "x", "time": clock,
          "log": logs.append, "array": array, "math": math, "MAX_REC_S": 30}
    exec(LEGACY_VAD_WAIT, ns)
    try:
        why = ns["vad_wait"](stop_evt)
    finally:
        popen.stdout.close()
    return why, [m for m in logs if m.startswith("VAD порог")], clock.calls


def run_new(monkeypatch, path, stop_evt=None, stop_at_call=None):
    logs, popen = [], FilePopen(path)
    clock = Clock(stop_evt, stop_at_call)
    monkeypatch.setattr(K, "subprocess", _fake_subprocess(popen))
    monkeypatch.setattr(K, "time", clock)
    monkeypatch.setattr(K, "log", logs.append)
    why = K.vad_wait(stop_evt)
    assert popen.stdout.closed
    return why, [m for m in logs if m.startswith("VAD порог")], clock.calls


def _random_stream(rng):
    noise = rng.choice([30, 80, 150, 400, 900, 2594])
    seq = [noise * rng.uniform(0.6, 1.4) for _ in range(rng.randint(0, 12))]
    for _ in range(rng.randint(0, 5)):
        seq += [rng.uniform(200, 6000)] * rng.randint(1, 15)
        seq += [noise * rng.uniform(0.5, 1.5)] * rng.randint(1, 25)
    seq += [noise] * rng.randint(0, 150)
    data = b"".join(tone(r) for r in seq)
    if rng.random() < 0.2:
        data = data[:max(0, len(data) - rng.randint(1, 3199))]  # truncated last chunk
    return data


def test_default_vad_decisions_are_identical_to_origin_main(monkeypatch, tmp_path):
    rng = random.Random(7)
    path = tmp_path / "pcm"
    reasons = set()
    fixed = [b"", tone(100) * 5,  # EOF
             b"".join(tone(r) for r in [100] * 8 + [3000] * 320),  # max_s
             b"".join(tone(r) for r in [100] * 8 + [301] * 3 + [100] * 40)]  # just above 299
    for n in range(150):
        path.write_bytes(fixed[n] if n < len(fixed) else _random_stream(rng))
        stop_at = rng.randint(2, 200) if rng.random() < 0.15 else None
        old = run_legacy(path, threading.Event(), stop_at)
        new = run_new(monkeypatch, path, threading.Event(), stop_at)
        assert new == old, (n, old, new)
        reasons.add(old[0])
    # the random set really exercises every outcome
    assert reasons >= {"кнопка", "поток закрыт", "тишина после речи", "речи нет", "максимум"}


def test_vad_logs_one_rms_trace_line(monkeypatch, tmp_path):
    path = tmp_path / "pcm"
    path.write_bytes(b"".join(tone(r) for r in [100] * 8 + [1000] * 50 + [100] * 30))
    logs = []
    popen = FilePopen(path)
    monkeypatch.setattr(K, "subprocess", _fake_subprocess(popen))
    monkeypatch.setattr(K, "time", Clock())
    monkeypatch.setattr(K, "log", logs.append)
    assert K.vad_wait(None) == "тишина после речи"
    trace = [m for m in logs if m.startswith("VAD rms(100мс)=")]
    assert len(trace) == 1
    values = ast.literal_eval(trace[0].split("=", 1)[1].split(" порог")[0])
    assert len(values) == 40 and values[:8] == [100] * 8 and values[8] == 1000
    threshold = [m for m in logs if m.startswith("VAD порог=")]
    assert threshold == ["VAD порог=299"]  # max(250, 3 x median RMS of the 8 calibration chunks)
    assert trace[0].endswith(" порог=299")


class PipePopen:
    """pw-record stand-in that never writes anything (a stalled or missing stream)."""

    def __init__(self):
        self.r, self.w = os.pipe()
        self.stdout = os.fdopen(self.r, "rb")
        self.killed = False

    def kill(self):
        self.killed = True
        if self.w is not None:
            os.close(self.w)
            self.w = None

    def wait(self):
        return 0


def test_vad_returns_when_pw_record_delivers_nothing(monkeypatch):
    K.CFG.stall_s = 0.5
    popen = PipePopen()
    monkeypatch.setattr(K, "subprocess", _fake_subprocess(popen))
    t0 = time.monotonic()
    why = K.vad_wait(threading.Event(), max_s=5.0)
    assert why == "нет звука от pw-record 0.5 с"
    assert time.monotonic() - t0 < 2.0
    assert popen.killed and popen.stdout.closed


def test_vad_max_s_ends_a_silent_stream_with_default_stall(monkeypatch):
    popen = PipePopen()
    monkeypatch.setattr(K, "subprocess", _fake_subprocess(popen))
    t0 = time.monotonic()
    assert K.vad_wait(None, max_s=0.6) == "максимум"
    assert time.monotonic() - t0 < 1.5


def test_vad_stop_button_works_even_without_audio(monkeypatch):
    popen = PipePopen()
    monkeypatch.setattr(K, "subprocess", _fake_subprocess(popen))
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    assert K.vad_wait(stop) == "кнопка"
    assert time.monotonic() - t0 < 1.5


def test_pcm_reader_reassembles_partial_reads_and_reports_eof():
    r, w = os.pipe()
    with os.fdopen(r, "rb") as stream:
        pcm = K._Pcm(stream)
        os.write(w, b"a" * 1000)
        assert pcm.read(0.05) is None  # not a full chunk yet
        os.write(w, b"b" * 2300)
        assert pcm.read(0.5) == b"a" * 1000 + b"b" * 2200
        os.close(w)
        assert pcm.read(0.5) == b"b" * 100  # EOF: the short tail
        assert pcm.read(0.5) == b""


def test_vad_settings_reach_the_detector(monkeypatch, tmp_path):
    K.CFG = K.load_settings({"HOME": str(tmp_path), "MINITOO_SILENCE_S": "2.5", "MINITOO_NOSPEECH_S": "8",
                             "MINITOO_VAD_WARMUP_S": "0.5", "MINITOO_VAD_CALIB_S": "1",
                             "MINITOO_VAD_FLOOR": "300", "MINITOO_VAD_MULT": "4",
                             "MINITOO_VAD_ADAPTIVE": "1",
                             "MINITOO_VAD_CEILING": "700"}, {})
    path = tmp_path / "pcm"
    path.write_bytes(tone(100) * 3)
    monkeypatch.setattr(K, "subprocess", _fake_subprocess(FilePopen(path)))
    seen = {}

    class Recorder(K.Vad):
        def __init__(self, *args):
            seen["args"] = args[1:]
            super().__init__(*args)

    monkeypatch.setattr(K, "Vad", Recorder)
    K.vad_wait(None)
    assert seen["args"] == (2.5, 8.0, 0.5, 1.0, 300.0, 4.0, True, 700.0)


# --- the detector on synthetic RMS sequences ------------------------------------------------

def detect(seq, **kw):
    """(reason, seconds since start) for 100 ms chunks fed at 0.1, 0.2, ..."""
    vad = K.Vad(1000.0, **kw)
    for i, rms in enumerate(seq):
        now = 1000.0 + 0.1 * (i + 1)
        why = vad.feed(rms, now)
        if why:
            return why, now - 1000.0
    return None, None


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(K, "log", lambda msg: None)


def test_classic_threshold_and_warmup(monkeypatch):
    logs = []
    monkeypatch.setattr(K, "log", logs.append)
    detect([100] * 8 + [1000] * 3)
    assert logs == ["VAD порог=300"]
    logs.clear()
    detect([2594] * 8 + [1500] * 3)
    assert logs == ["VAD порог=7782"]  # start-up garbage poisons the default calibration (HANDOFF 8.3)
    logs.clear()
    detect([2594] * 8 + [100] * 8 + [1500] * 3, warmup_s=0.8)
    assert logs == ["VAD порог=300"]  # opt-in warm-up skips it


@pytest.mark.parametrize("warmup_s", [0.0, 0.5])
@pytest.mark.parametrize("garbage", [9, 15])
def test_adaptive_garbage_then_pause_does_not_end_before_speech(quiet, warmup_s, garbage):
    pause, speech = 20, 20
    seq = [2594] * garbage + [150] * pause + [1500] * speech + [150] * 40
    speech_end = 0.1 * (garbage + pause + speech)
    why, t = detect(seq, adaptive=True, warmup_s=warmup_s)
    assert why == "тишина после речи"
    assert speech_end + 1.5 < t < speech_end + 1.75


def test_adaptive_garbage_then_short_pause_then_speech(quiet):
    seq = [2594] * 9 + [150] * 10 + [1500] * 20 + [150] * 40
    why, t = detect(seq, adaptive=True)
    assert why == "тишина после речи" and t > 3.9 + 1.5


def test_adaptive_immediate_speech(quiet):
    why, t = detect([1500] * 25 + [150] * 40, adaptive=True)  # talks right after the prompt
    assert why == "тишина после речи" and 4.0 < t < 4.25
    words = ([1500] * 4 + [200]) * 8 + [150] * 40
    why, t = detect(words, adaptive=True)
    assert why == "тишина после речи" and 3.9 + 1.5 < t < 3.9 + 1.75
    assert detect([1500] * 25 + [150] * 40)[0] is None  # classic: calibrates on the speech, waits on


def test_adaptive_long_speech_with_gaps_is_not_cut(quiet):
    seq = [150] * 10 + ([1500] * 8 + [300] * 2) * 10 + [150] * 40
    why, t = detect(seq, adaptive=True)
    assert why == "тишина после речи" and 10.8 + 1.5 < t < 10.8 + 1.75
    why, t = detect([150] * 3 + [1500] * 60 + [150] * 40, adaptive=True)
    assert why == "тишина после речи" and 6.3 + 1.5 < t < 6.3 + 1.75


def test_adaptive_no_speech_and_quiet_room(quiet):
    assert detect([150] * 200, adaptive=True) == ("речи нет", pytest.approx(12.1))
    why, t = detect([60] * 8 + [400] * 20 + [60] * 40, adaptive=True)
    assert why == "тишина после речи" and 2.8 + 1.5 < t < 2.8 + 1.75


def test_last_run_end_rules():
    # calibration = first 3 values
    assert K._last_run_end([1, 1, 1, 9, 9, 1], 5, 3) == 4  # starts after calibration
    assert K._last_run_end([9, 9, 9, 9, 1, 1], 5, 3) is None  # started in calibration, too short after
    assert K._last_run_end([9, 9, 9, 9, 9, 9, 1], 5, 3) == 5  # ... long enough after it
    assert K._last_run_end([1, 1, 1, 1], 5, 3) is None
    assert K._last_run_end([], 5, 3) is None


def test_ceiling_caps_threshold_when_speech_in_calibration(quiet):
    loud = [2500] * 8 + [20] * 40 + [2500] * 5 + [20] * 30
    vad = K.Vad(1000.0, ceiling=600.0)
    for i in range(8):
        vad.feed(2500, 1000.0 + 0.1 * (i + 1))
    assert vad.thr == 600.0
    why, _ = detect(loud, ceiling=600.0)
    assert why == "тишина после речи"


def test_ceiling_never_below_floor_and_off_by_default(quiet):
    vad = K.Vad(1000.0, floor=250.0, ceiling=100.0)
    assert vad.ceiling == 250.0
    plain = K.Vad(1000.0)
    for i in range(8):
        plain.feed(2500, 1000.0 + 0.1 * (i + 1))
    assert plain.thr == 7500.0

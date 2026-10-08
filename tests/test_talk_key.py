import importlib.util
import pathlib
import threading
import time

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "minitoo-talk-key.py"
_sp = importlib.util.spec_from_file_location("talk_key", _p)
K = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(K)


def _state():
    return {"phase": "idle", "end": 0.0, "stop": threading.Event()}


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

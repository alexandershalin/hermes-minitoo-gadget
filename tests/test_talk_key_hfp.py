"""hfp_keys of scripts/minitoo-talk-key.py: AT commands from the WirePlumber journal as a key."""

import importlib.util
import pathlib
import threading
import time

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "minitoo-talk-key.py"
_sp = importlib.util.spec_from_file_location("talk_key_hfp", _p)
K = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(K)

INFO = "spa.bluez5.native: RFCOMM receive command but modem not available: {}\n"
DEBUG = "D spa.bluez5.native [backend-native.c:2535:rfcomm_process_events]: RFCOMM event: {}\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "CFG", K.load_settings({"HOME": str(tmp_path), "MINITOO_HFP_KEYS": "1"}, {}))
    monkeypatch.setattr(K, "SHUTDOWN", threading.Event())
    monkeypatch.setattr(K, "_HFP_PROC", None)


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(K, "log", out.append)
    return out


@pytest.fixture
def no_worker(monkeypatch):
    started = []
    monkeypatch.setattr(K, "worker", lambda st: started.append(st))
    return started


def _state(phase="idle", end=0.0):
    return {"phase": phase, "end": end, "stop": threading.Event()}


class Stop:
    """SHUTDOWN stand-in: is set after `rounds` calls of wait()."""

    def __init__(self, rounds):
        self.rounds, self.waits, self._set = rounds, [], False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) >= self.rounds:
            self._set = True
        return self._set


def test_hfp_command_parsing():
    assert K.hfp_command(INFO.format("AT+CHUP")) == "AT+CHUP"
    assert K.hfp_command(DEBUG.format("AT+BVRA=1")) == "AT+BVRA=1"
    assert K.hfp_command("I spa.bluez5.native [backend-native.c:1433:rfcomm_hfp_ag]: RFCOMM receive "
                         "command but modem not available: at+ckpd=200") == "AT+CKPD=200"
    assert K.hfp_command(DEBUG.format("DATA")) is None
    assert K.hfp_command("RFCOMM event: +CIEV: 2,1") is None  # replies in the HF role
    assert K.hfp_command("RFCOMM received unsupported event: AT+FOO") is None
    assert K.hfp_command("RFCOMM >> AT+CHUP") is None  # our own (HF role) commands
    assert K.hfp_command("Transport /org/bluez/hci0/dev_X/sco released") is None


def test_hfp_key_matches_the_command_name_only():
    for cmd, key in (("AT+CHUP", "AT+CHUP"), ("AT+BVRA=1", "AT+BVRA"), ("AT+BVRA=0", "AT+BVRA"),
                     ("ATA", "ATA"), ("ATD123;", "ATD"), ("ATD>1;", "ATD"), ("AT+CKPD=200", "AT+CKPD"),
                     ("AT+BLDN", "AT+BLDN")):
        assert K.hfp_key(cmd) == key, cmd
    for cmd in ("AT+VGS=7", "AT+NREC=0", "AT+CDATA", "ATAB", "AT+CHUPX", "AT+BIEV=2,50", "DATA"):
        assert K.hfp_key(cmd) is None, cmd


def test_duplicate_info_and_debug_lines_are_one_press(logs, no_worker):
    now = [100.0]
    st = _state("recording")
    w = K.HfpWatcher(st, clock=lambda: now[0])
    assert w.feed(DEBUG.format("AT+CHUP")) == "stop"
    now[0] += 0.01
    assert w.feed(INFO.format("AT+CHUP")) is None  # the same command, logged twice by PipeWire
    now[0] += 0.6
    assert w.feed(INFO.format("AT+CHUP")) == "stop"
    assert w.feed("spa.bluez5.native: something else\n") is None
    assert logs == ["hfp at=AT+CHUP phase=recording action=stop"] * 2


def test_hfp_key_in_recording_stops_like_avrcp(logs, no_worker):
    st = _state("recording")
    assert K.HfpWatcher(st).feed(INFO.format("AT+CHUP")) == "stop"
    assert st["stop"].is_set()


def test_hfp_key_in_idle_is_only_logged_by_default(logs, no_worker):
    st = _state("idle")
    assert K.HfpWatcher(st).feed(INFO.format("AT+BVRA=1")) == "только лог"
    assert st["phase"] == "idle" and no_worker == []
    assert logs == ["hfp at=AT+BVRA=1 phase=idle action=только лог"]


def test_hfp_start_is_opt_in_and_not_right_after_a_recording(logs, no_worker):
    K.CFG.hfp_keys_start = True
    st = _state("idle", end=time.time() - 3)
    assert K.HfpWatcher(st).feed(INFO.format("AT+CHUP")).startswith("игнор")
    assert no_worker == []
    st = _state("idle", end=time.time() - 9)
    assert K.HfpWatcher(st).feed(INFO.format("AT+CHUP")) == "start"
    deadline = time.monotonic() + 3
    while not no_worker and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(no_worker) == 1 and st["phase"] == "starting"


def test_hfp_key_while_starting_is_ignored_even_with_cancel(logs, no_worker):
    K.CFG.control, K.CFG.press_during_start = "socket", "cancel"
    st = _state("starting")
    st["started"] = time.time() - 5
    assert K.HfpWatcher(st).feed(INFO.format("AT+CHUP")) == "ignored"
    assert not st["stop"].is_set()


def test_other_at_commands_are_logged_without_action(logs, monkeypatch):
    monkeypatch.setattr(K, "on_press", lambda *a, **k: pytest.fail("not a key"))
    assert K.HfpWatcher(_state("recording")).feed(DEBUG.format("AT+VGS=7")) == "-"
    assert logs == ["hfp at=AT+VGS=7 phase=recording action=-"]


class FakeProc:
    def __init__(self, lines, rc=0):
        import io
        self.stdout = io.StringIO("".join(lines))
        self.returncode = rc

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


def test_hfp_follow_runs_journalctl_and_restarts_it(monkeypatch, logs):
    procs = [FakeProc([INFO.format("AT+CHUP"), "noise\n"], rc=1), FakeProc([DEBUG.format("AT+BVRA=1")])]
    calls = []

    def popen(cmd, **kw):
        calls.append((cmd, kw))
        return procs[len(calls) - 1]

    stop = Stop(rounds=2)
    monkeypatch.setattr(K, "SHUTDOWN", stop)
    monkeypatch.setattr(K.subprocess, "Popen", popen)
    st = _state("recording")
    K.hfp_follow(st)
    assert [c[0] for c in calls] == [["journalctl", "--user", "-u", "wireplumber.service", "-f", "-n", "0",
                                      "-o", "cat", "TOPIC=spa.bluez5.native"]] * 2
    assert calls[0][1]["stdout"] is K.subprocess.PIPE
    assert "hfp at=AT+CHUP phase=recording action=stop" in logs
    assert "hfp at=AT+BVRA=1 phase=recording action=stop" in logs
    assert any("перезапуск" in line for line in logs)
    assert stop.waits == [5.0, 10.0]  # back-off between quick restarts


def test_hfp_follow_without_journalctl_logs_once_and_stops(monkeypatch, logs):
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        raise FileNotFoundError(2, "No such file", cmd[0])

    monkeypatch.setattr(K.subprocess, "Popen", popen)
    K.hfp_follow(_state())
    assert len(calls) == 1
    assert len(logs) == 1 and "journalctl не найден" in logs[0]


def test_shutdown_stops_journalctl(monkeypatch):
    class Proc:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    proc = Proc()
    monkeypatch.setattr(K, "_HFP_PROC", proc)
    K.shutdown(_state(), timeout=0.1)
    assert proc.terminated


OK_META = "update: id:57 key:'log.level' value:'N,spa.bluez5.native:I' type:''\n"


def test_wp_log_level_ok():
    assert K.wp_log_level_ok(OK_META)
    assert K.wp_log_level_ok("update: id:62 key:'log.level' value:'W,spa.bluez5.native:D' type:''")
    assert not K.wp_log_level_ok("update: id:0 key:'clock.rate' value:'48000' type:''")
    assert not K.wp_log_level_ok("update: id:57 key:'log.level' value:'2' type:''")
    assert not K.wp_log_level_ok("update: id:57 key:'log.level' value:'N,spa.bluez5.native:W' type:''")
    assert not K.wp_log_level_ok("")


def test_wp_log_level_keeper_sets_info_at_start_and_after_a_wireplumber_restart(monkeypatch, logs):
    metadata = iter(["", OK_META, OK_META, ""])  # start, applied, applied, WirePlumber restarted
    calls = []

    def run(cmd, timeout=10):
        calls.append(cmd)
        if cmd[0] == "pw-metadata":
            assert cmd == ["pw-metadata", "-n", "settings"]
            return True, next(metadata)
        return True, ""

    stop = Stop(rounds=4)
    monkeypatch.setattr(K, "SHUTDOWN", stop)
    monkeypatch.setattr(K, "_run", run)
    K.wp_log_level_keeper()
    wpctl = [c for c in calls if c[0] == "wpctl"]
    assert wpctl == [["wpctl", "set-log-level", "N,spa.bluez5.native:I"]] * 2
    assert all(c[-1] != "-" for c in calls)
    assert stop.waits == [60.0] * 4
    assert logs == ["hfp_keys: wpctl set-log-level N,spa.bluez5.native:I"] * 2


def test_wp_log_level_keeper_logs_a_wpctl_failure_once(monkeypatch, logs):
    def run(cmd, timeout=10):
        if cmd[0] == "pw-metadata":
            return False, "FileNotFoundError(2, 'No such file')"
        return False, "Could not connect to PipeWire"

    monkeypatch.setattr(K, "SHUTDOWN", Stop(rounds=3))
    monkeypatch.setattr(K, "_run", run)
    K.wp_log_level_keeper()
    assert logs == ["hfp_keys: wpctl set-log-level не удалось: Could not connect to PipeWire"]


def test_run_never_raises(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(2, "No such file", cmd[0])

    monkeypatch.setattr(K.subprocess, "run", missing)
    ok, out = K._run(["wpctl", "status"])
    assert not ok and "No such file" in out

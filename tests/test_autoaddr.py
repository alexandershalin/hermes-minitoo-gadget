"""minitoo-autoaddr: config rewrite, default behaviour pinned to origin/main, opt-ins.

No hardware: bluetoothctl/systemctl are replaced by FakeBt, which records every
command, stdin line and sleep so the default sequence can be compared exactly.
"""
import importlib.util
import json
import os
import pathlib
import stat

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "minitoo-autoaddr.py"
OLD, NEW, OTHER = "B1:21:81:A0:CB:09", "B1:21:81:A0:78:53", "B1:21:81:11:22:33"
MODALIAS = "bluetooth:v05D6p000Ad0240"
FLAGS = ("MINITOO_AUTOADDR_SAFE_SCAN", "MINITOO_AUTOADDR_ACCEPT_UNPAIRED",
         "MINITOO_AUTOADDR_IDENTIFY", "MINITOO_AUTOADDR_ONESHOT")


def load(monkeypatch, **env):
    for name in (*FLAGS, "MINITOO_CONFIG", "MINITOO_AUTOADDR_PREFIX"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("autoaddr_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Done:
    def __init__(self, stdout=""):
        self.stdout, self.stderr, self.returncode = stdout, "", 0


class FakeProc:
    """bluetoothctl driven through stdin (the default mode)."""

    def __init__(self, bt):
        self.bt, self.lines = bt, []
        self.stdin = self

    def write(self, text):
        self.lines.append(text.rstrip("\n"))
        self.bt.events.append(("stdin", text.rstrip("\n")))

    def flush(self):
        pass

    def communicate(self, timeout=None):
        self.bt.events.append(("communicate", timeout))
        out = ""
        for line in self.lines:
            if line == "scan on":
                out += self.bt.scan_text()
            if line.startswith("connect "):
                self.bt.connected(line.split()[1])
        return out, None

    def kill(self):
        self.bt.events.append(("kill",))

    def wait(self):
        return 0


class FakeBt:
    """devices: {addr: {Name, Paired, Connected, Modalias?, AddressType?}}.
    scan: addresses in the order their '[CHG] Device X RSSI' lines appear.
    after_connect: {addr: {property updates applied when 'connect addr' runs}}."""

    def __init__(self, devices, scan=(), after_connect=None):
        self.devices, self.scan, self.after_connect = devices, list(scan), after_connect or {}
        self.events = []

    def info_text(self, addr):
        d = self.devices.get(addr)
        if d is None:
            return f"Device {addr} not available\n"
        out = f"Device {addr} ({d.get('AddressType', 'public')})\n"
        for key in ("Name", "Alias", "Paired", "Bonded", "Trusted", "Connected"):
            if key in d or key == "Alias":
                out += f"\t{key}: {d.get(key, d.get('Name', ''))}\n"
        out += "\tUUID: Serial Port               (00001101-0000-1000-8000-00805f9b34fb)\n"
        if "Modalias" in d:
            out += f"\tModalias: {d['Modalias']}\n"
        out += "\tBREDR.Connected: no\n"
        return out

    def scan_text(self):
        return "".join(f"[\x1b[0;93mCHG\x1b[0m] Device {a} RSSI: 0xffffffb5 (-75)\n" for a in self.scan)

    def connected(self, addr):
        self.devices.setdefault(addr, {"Name": "Divoom MiniToo-Audio", "Paired": "no", "Connected": "no"})
        self.devices[addr].update(self.after_connect.get(addr, {}))

    def run(self, args, capture_output=False, text=False, timeout=None):
        args = list(args)
        self.events.append(("run", tuple(args), timeout))
        if args[:2] == ["bluetoothctl", "info"]:
            return Done(self.info_text(args[2]))
        if args[:2] == ["bluetoothctl", "devices"]:
            return Done("".join(f"Device {a} {d.get('Name', '')}\n" for a, d in self.devices.items()))
        if args[0] == "bluetoothctl" and "scan" in args:
            return Done(self.scan_text())
        if args[0] == "bluetoothctl" and "connect" in args:
            self.connected(args[-1])
        return Done("")

    def popen(self, args, **kwargs):
        self.events.append(("popen", tuple(args)))
        return FakeProc(self)

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))

    def install(self, monkeypatch, module):
        monkeypatch.setattr(module.subprocess, "run", self.run)
        monkeypatch.setattr(module.subprocess, "Popen", self.popen)
        monkeypatch.setattr(module.time, "sleep", self.sleep)
        return self

    def commands(self):
        return [e for e in self.events if e[0] != "run" or e[1][:2] != ("bluetoothctl", "info")]


def write_config(path, address=OLD, mode=0o600, **audio):
    cfg = {"server": "ws://127.0.0.1:8765/gadget", "token": "SECRET-TOKEN", "minitoo": {"address": address},
           "audio": {"rate": 48000, **audio}}
    path.write_text(json.dumps(cfg, indent=2))
    os.chmod(path, mode)
    return cfg


def info(addr):
    return ("run", ("bluetoothctl", "info", addr), 10)


DEVICES = ("run", ("bluetoothctl", "devices"), 10)
RESTART = ("run", ("systemctl", "--user", "restart", "hermes-minitoo"), 60)
# What origin/main (21af541) sends for one scan and one pair attempt, line by line.
SCAN_STDIN = [("popen", ("bluetoothctl",)), ("stdin", "scan on"), ("sleep", 12), ("stdin", "scan off"),
              ("sleep", 1), ("stdin", "quit"), ("communicate", 32)]


def pair_stdin(addr):
    return [("popen", ("bluetoothctl",)), ("stdin", "agent on"), ("sleep", 1), ("stdin", "default-agent"),
            ("sleep", 1), ("stdin", f"pair {addr}"), ("sleep", 15), ("stdin", f"trust {addr}"), ("sleep", 2),
            ("stdin", f"connect {addr}"), ("sleep", 10), ("stdin", "quit"), ("communicate", 70)]


def minitoo(connected="no", paired="yes", **extra):
    return {"Name": "Divoom MiniToo-Audio", "Paired": paired, "Connected": connected, **extra}


@pytest.fixture
def conf(tmp_path):
    return tmp_path / "config.json"


# ---------------------------------------------------------------- config rewrite


def test_config_rewrite_keeps_0600_and_updates_both_mac_forms(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf, output=f"bluez_output.{OLD.replace(':', '_')}.1", input=f"bluez_input.{OLD.lower()}")
    original = conf.read_text()
    bt = FakeBt({}).install(monkeypatch, a)
    old_umask = os.umask(0o022)
    try:
        assert a.apply_address(NEW) is True
    finally:
        os.umask(old_umask)
    cfg = json.loads(conf.read_text())
    assert cfg["minitoo"]["address"] == NEW
    assert cfg["audio"] == {"rate": 48000, "output": f"bluez_output.{NEW.replace(':', '_')}.1",
                            "input": f"bluez_input.{NEW}"}
    assert cfg["token"] == "SECRET-TOKEN" and cfg["server"] == "ws://127.0.0.1:8765/gadget"
    bak = pathlib.Path(str(conf) + ".bak-autoaddr")
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert bak.read_text() == original
    assert sorted(p.name for p in conf.parent.iterdir()) == ["config.json", "config.json.bak-autoaddr"]
    assert bt.events == [RESTART]


def test_config_rewrite_preserves_a_non_default_mode(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf, mode=0o640, output="Divoom MiniToo")
    FakeBt({}).install(monkeypatch, a)
    assert a.apply_address(NEW) is True
    assert stat.S_IMODE(conf.stat().st_mode) == 0o640
    assert json.loads(conf.read_text())["audio"]["output"] == "Divoom MiniToo"


def test_no_rewrite_and_no_restart_when_nothing_changes(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf, output=f"bluez_output.{OLD}")
    before = conf.read_bytes()
    bt = FakeBt({}).install(monkeypatch, a)
    assert a.apply_address(OLD) is False
    assert conf.read_bytes() == before
    assert not pathlib.Path(str(conf) + ".bak-autoaddr").exists()
    assert bt.events == []


def test_failed_replace_keeps_the_original_and_removes_the_temp_file(monkeypatch, conf):
    a = load(monkeypatch)
    write_config(conf)
    before = conf.read_bytes()

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(a.os, "replace", broken_replace)
    with pytest.raises(OSError):
        a.write_atomic(str(conf), "{}", 0o600)
    assert conf.read_bytes() == before
    assert [p.name for p in conf.parent.iterdir()] == ["config.json"]


def test_paths_come_from_home_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = load(monkeypatch)
    assert a.CONF == str(tmp_path / "hermes-minitoo-gadget" / "config.json")
    assert a.PREFIX == "B1:21:81:"
    a = load(monkeypatch, MINITOO_CONFIG="/etc/x.json", MINITOO_AUTOADDR_PREFIX="aa:bb:cc:")
    assert a.CONF == "/etc/x.json"
    assert a.PREFIX == "AA:BB:CC:"


# ------------------------------------------------- defaults == origin/main (21af541)


def test_defaults_do_nothing_while_configured_speaker_is_connected(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    bt = FakeBt({OLD: minitoo("yes")}).install(monkeypatch, a)
    a.cycle()
    assert bt.events == [info(OLD)]


def test_defaults_adopt_another_connected_and_paired_entry(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf, output=f"bluez_output.{OLD}")
    bt = FakeBt({OLD: minitoo(), NEW: minitoo("yes")}).install(monkeypatch, a)
    a.cycle()
    assert bt.events == [info(OLD), DEVICES, info(OLD), info(NEW), RESTART]
    assert json.loads(conf.read_text())["minitoo"]["address"] == NEW


def test_defaults_choose_the_same_address_as_today(monkeypatch, conf):
    # The connected-but-unpaired entry is ignored, a scan runs, the address with
    # the most RSSI lines wins (NEW: 3, OLD: 1, OTHER prefix filtered out).
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    devices = {OLD: minitoo(), OTHER: minitoo("yes", "no"), "11:22:33:44:55:66": {"Name": "Phone"}}
    bt = FakeBt(devices, scan=[OLD, NEW, "11:22:33:44:55:66", NEW, NEW],
                after_connect={NEW: {"Paired": "yes", "Connected": "yes"}}).install(monkeypatch, a)
    a.cycle()
    assert bt.events == ([info(OLD), DEVICES, info(OLD), info(OTHER)] + SCAN_STDIN + pair_stdin(NEW)
                         + [info(NEW), RESTART])
    assert json.loads(conf.read_text())["minitoo"]["address"] == NEW


def test_defaults_empty_scan_and_failed_pairing_change_nothing(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    before = conf.read_bytes()
    bt = FakeBt({OLD: minitoo()}).install(monkeypatch, a)
    a.cycle()
    assert bt.events == [info(OLD), DEVICES, info(OLD)] + SCAN_STDIN
    bt = FakeBt({OLD: minitoo()}, scan=[NEW]).install(monkeypatch, a)  # NEW never connects
    a.cycle()
    assert bt.events == [info(OLD), DEVICES, info(OLD)] + SCAN_STDIN + pair_stdin(NEW) + [info(NEW)]
    assert conf.read_bytes() == before


def test_defaults_scan_on_every_cycle_and_main_sleeps_20s(monkeypatch, conf):
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    bt = FakeBt({OLD: minitoo()}).install(monkeypatch, a)

    class Stop(BaseException):
        pass

    sleeps = []

    def sleep(seconds):
        bt.events.append(("sleep", seconds))
        if seconds == a.INTERVAL:
            sleeps.append(seconds)
            if len(sleeps) == 3:
                raise Stop

    monkeypatch.setattr(a.time, "sleep", sleep)
    with pytest.raises(Stop):
        a.main()
    assert [e for e in bt.events if e == ("stdin", "scan on")] == [("stdin", "scan on")] * 3
    assert sleeps == [20, 20, 20]


def test_cycle_errors_are_logged_and_the_loop_goes_on(monkeypatch, capsys):
    a = load(monkeypatch)

    class Stop(BaseException):
        pass

    def boom(backoff=None):
        raise RuntimeError("bluetoothctl missing")

    def sleep(seconds):
        raise Stop

    monkeypatch.setattr(a, "cycle", boom)
    monkeypatch.setattr(a.time, "sleep", sleep)
    with pytest.raises(Stop):
        a.main()
    assert "ошибка цикла: RuntimeError('bluetoothctl missing')" in capsys.readouterr().out


# ------------------------------------------------------------------- opt-ins


def test_safe_scan_never_scans_while_a_candidate_is_connected(monkeypatch, conf):
    a = load(monkeypatch, MINITOO_AUTOADDR_SAFE_SCAN="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    bt = FakeBt({OLD: minitoo(), OTHER: minitoo("yes", "no")}, scan=[NEW]).install(monkeypatch, a)
    a.cycle()
    assert bt.events == [info(OLD), DEVICES, info(OLD), info(OTHER)]


def test_safe_scan_backs_off_from_20s_to_5min_and_rearms_when_seen(monkeypatch, conf):
    a = load(monkeypatch, MINITOO_AUTOADDR_SAFE_SCAN="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    bt = FakeBt({OLD: minitoo()}).install(monkeypatch, a)
    now = [0.0]
    backoff = a.ScanBackoff(clock=lambda: now[0])
    scans = []
    for step in range(46):  # main() runs a cycle every 20 s
        now[0] = step * 20.0
        before = len(bt.events)
        a.cycle(backoff)
        if ("stdin", "scan on") in bt.events[before:]:
            scans.append(now[0])
    assert scans == [0, 20, 60, 140, 300, 600, 900]
    bt.scan = [NEW]  # visible again (pairing fails): next scan 20 s later, not 5 min
    now[0] = 1200.0
    a.cycle(backoff)
    bt.scan = []
    times = []
    for t in (1220.0, 1240.0, 1260.0):
        now[0] = t
        before = len(bt.events)
        a.cycle(backoff)
        if ("stdin", "scan on") in bt.events[before:]:
            times.append(t)
    assert times == [1220.0, 1240.0]


def test_accept_unpaired_adopts_a_connected_entry_without_pairing(monkeypatch, conf):
    devices = {OLD: minitoo(), NEW: minitoo("yes", "no")}
    a = load(monkeypatch)
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    FakeBt(dict(devices), scan=[]).install(monkeypatch, a)
    a.cycle()
    assert json.loads(conf.read_text())["minitoo"]["address"] == OLD
    a = load(monkeypatch, MINITOO_AUTOADDR_ACCEPT_UNPAIRED="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    bt = FakeBt(dict(devices)).install(monkeypatch, a)
    a.cycle()
    assert bt.events == [info(OLD), DEVICES, info(OLD), info(NEW), RESTART]
    assert json.loads(conf.read_text())["minitoo"]["address"] == NEW


def test_accept_unpaired_counts_a_connected_but_unpaired_pair_result(monkeypatch, conf):
    after = {NEW: {"Paired": "no", "Connected": "yes"}}
    for flag, expected in (("", OLD), ("1", NEW)):
        a = load(monkeypatch, MINITOO_AUTOADDR_ACCEPT_UNPAIRED=flag)
        monkeypatch.setattr(a, "CONF", str(conf))
        write_config(conf)
        FakeBt({OLD: minitoo()}, scan=[NEW], after_connect=after).install(monkeypatch, a)
        a.cycle()
        assert json.loads(conf.read_text())["minitoo"]["address"] == expected


@pytest.mark.parametrize("bad", [{"Modalias": "bluetooth:v0001p0002d0003"},
                                 {"Modalias": MODALIAS, "AddressType": "random"},
                                 {"Modalias": MODALIAS, "Name": "Divoom Tiivoo 2"}])
def test_identify_rejects_a_connected_entry_that_is_not_a_minitoo(monkeypatch, conf, bad):
    a = load(monkeypatch, MINITOO_AUTOADDR_IDENTIFY="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    dev = minitoo("yes", **bad)
    if dev["Name"] != "Divoom MiniToo-Audio":
        dev["Alias"] = dev["Name"]
    bt = FakeBt({OLD: minitoo(), NEW: dev}).install(monkeypatch, a)
    monkeypatch.setattr(a, "known_minitoo", lambda: [OLD, NEW])  # as if the BlueZ alias still said MiniToo
    a.cycle()
    assert RESTART not in bt.events
    assert json.loads(conf.read_text())["minitoo"]["address"] == OLD


def test_identify_accepts_a_connected_minitoo(monkeypatch, conf):
    a = load(monkeypatch, MINITOO_AUTOADDR_IDENTIFY="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    bt = FakeBt({OLD: minitoo(), NEW: minitoo("yes", Modalias=MODALIAS)}).install(monkeypatch, a)
    a.cycle()
    assert bt.events[-1] == RESTART
    assert json.loads(conf.read_text())["minitoo"]["address"] == NEW


def test_identify_checks_modalias_after_connecting_a_new_address(monkeypatch, conf):
    for modalias, expected in (("bluetooth:v1234p5678d0001", OLD), (MODALIAS, NEW)):
        a = load(monkeypatch, MINITOO_AUTOADDR_IDENTIFY="1")
        monkeypatch.setattr(a, "CONF", str(conf))
        write_config(conf)
        devices = {OLD: minitoo(), NEW: {"Name": "Divoom MiniToo-Audio", "Paired": "no", "Connected": "no"}}
        after = {NEW: {"Paired": "yes", "Connected": "yes", "Modalias": modalias}}
        bt = FakeBt(devices, scan=[NEW], after_connect=after).install(monkeypatch, a)
        a.cycle()
        assert pair_stdin(NEW)[0] in bt.events  # no Modalias before SDP: it is paired first
        assert json.loads(conf.read_text())["minitoo"]["address"] == expected


def test_identify_skips_scan_results_without_minitoo_in_the_name(monkeypatch, conf):
    a = load(monkeypatch, MINITOO_AUTOADDR_IDENTIFY="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    devices = {OLD: minitoo(), NEW: {"Name": "Divoom Tiivoo 2", "Paired": "no", "Connected": "no"}}
    bt = FakeBt(devices, scan=[NEW]).install(monkeypatch, a)
    a.cycle()
    assert bt.events[-1] == info(NEW)
    assert ("stdin", f"pair {NEW}") not in bt.events


def test_oneshot_uses_single_bluetoothctl_commands(monkeypatch, conf):
    a = load(monkeypatch, MINITOO_AUTOADDR_ONESHOT="1")
    monkeypatch.setattr(a, "CONF", str(conf))
    write_config(conf)
    bt = FakeBt({OLD: minitoo()}, scan=[NEW, NEW, OTHER],
                after_connect={NEW: {"Paired": "yes", "Connected": "yes"}}).install(monkeypatch, a)
    a.cycle()
    assert bt.commands() == [
        DEVICES,
        ("run", ("bluetoothctl", "--timeout", "12", "scan", "bredr"), 32),
        ("run", ("bluetoothctl", "--agent", "NoInputNoOutput", "--timeout", "30", "pair", NEW), 50),
        ("run", ("bluetoothctl", "trust", NEW), 20),
        ("run", ("bluetoothctl", "connect", NEW), 60),
        RESTART,
    ]
    assert not [e for e in bt.events if e[0] in ("popen", "stdin", "sleep")]
    assert json.loads(conf.read_text())["minitoo"]["address"] == NEW


def test_oneshot_survives_a_bluetoothctl_timeout(monkeypatch):
    a = load(monkeypatch)

    def run(*args, **kwargs):
        raise a.subprocess.TimeoutExpired("bluetoothctl", 32)

    monkeypatch.setattr(a.subprocess, "run", run)
    assert a.btctl1(["--timeout", "12", "scan", "bredr"], 32).startswith("ошибка: TimeoutExpired")

#!/usr/bin/env python3
"""Play/Pause на колонке MiniToo (AVRCP) -> toggle записи голоса в Hermes."""
import array, json, math, os, re, select, struct, subprocess, sys, threading, time

BIN = "/home/bishop/hermes-minitoo-gadget/.venv/bin/hermes-minitoo"
PY = "/home/bishop/.hermes/installs/20715197cc5be820/environments/738223755d2649faa3439a3b8f7036ae/venv/bin/python"
TTS = "/home/bishop/.local/bin/piper-tts-client.py"
SPEAK_WAV = "/home/bishop/.cache/minitoo-speak.wav"
CONF = "/home/bishop/hermes-minitoo-gadget/config.json"
FFMPEG = "/home/bishop/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg"
IND = "/home/bishop/.cache/minitoo-indicator"


def _audio(key):
    return json.load(open(CONF))["audio"][key]


def _sink():
    return _audio("output")


KEYS = {164, 200, 201, 119, 207}  # PLAYPAUSE, PLAYCD, PAUSECD, PAUSE, PLAY
MAX_REC_S = 30
DEBOUNCE_S = 1.0


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def find_event():
    for line in open("/proc/bus/input/devices").read().split("\n\n"):
        if "MiniToo" in line and "(AVRCP)" in line:
            m = re.search(r"event(\d+)", line)
            if m:
                return f"/dev/input/event{m.group(1)}"
    return None


def ensure_wav():
    """«Говорите» с 0,9 с тишины в начале: колонка при смене профиля съедает начало звука."""
    if os.path.exists(SPEAK_WAV):
        return
    os.makedirs(os.path.dirname(SPEAK_WAV), exist_ok=True)
    raw = SPEAK_WAV + ".orig"
    open("/tmp/minitoo-speak.txt", "w").write("Говорите!")
    subprocess.run([PY, TTS, "/tmp/minitoo-speak.txt", raw, "dmitri"], check=True)
    r = subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", raw, "-af",
                        "adelay=900:all=1,apad=pad_dur=0.3", "-ar", "48000", SPEAK_WAV])
    if r.returncode != 0:
        os.replace(raw, SPEAK_WAV)


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


def _profile():
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5)
        for o in json.loads(r.stdout):
            props = o.get("info", {}).get("props", {})
            if str(props.get("device.name", "")).startswith("bluez_card"):
                pr = o["info"]["params"].get("Profile", [])
                return pr[0].get("name") if pr else None
    except Exception:
        return None
    return None


def set_ind(state):
    """Индикатор на экране отключён (по просьбе Саши); только чистим старый файл."""
    try:
        if os.path.exists(IND):
            os.remove(IND)
    except OSError:
        pass


def button(state):
    r = subprocess.run([BIN, "button", "talk", state], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        log(f"button talk {state} failed: {r.stderr.strip()[:200]}")
    return r.returncode == 0


def record_once(st):
    """Одна запись: подсказка -> press -> VAD (или кнопка) -> release. Индикатор и release в finally."""
    st["phase"] = "starting"
    try:
        r = subprocess.run(["pw-play", f"--target={_sink()}", SPEAK_WAV],
                           timeout=15, capture_output=True, text=True)
        log(f"говорите rc={r.returncode} {r.stderr.strip()[:80]}")
    except Exception as exc:
        log(f"pw-play: {exc!r}")
    if not button("press"):
        return
    st["phase"] = "recording"
    set_ind("rec")
    log(f"запись начата, профиль={_profile()}")
    try:
        why = vad_wait(st["stop"])
    except Exception as exc:
        why = f"vad ошибка {exc!r}"
    finally:
        set_ind("done")
        button("release")
    log(f"остановка: {why}")


def worker(st):
    try:
        record_once(st)
    except Exception as exc:
        log(f"запись упала: {exc!r}")
    finally:
        st["phase"] = "idle"
        st["end"] = time.time()


def on_press(st):
    """Реакция на нажатие. idle -> старт записи, recording -> досрочный стоп, иначе игнор."""
    phase = st["phase"]
    if phase == "idle":
        if time.time() - st["end"] < DEBOUNCE_S:
            return "debounce"
        st["stop"] = threading.Event()
        st["phase"] = "starting"
        threading.Thread(target=worker, args=(st,), daemon=True).start()
        return "start"
    if phase == "recording":
        st["stop"].set()
        return "stop"
    return "ignored"


def main():
    ensure_wav()
    set_ind(None)  # не оставлять красный экран от прошлого запуска
    button("release")  # не оставлять «зажатую» кнопку talk после падения
    st = {"phase": "idle", "end": 0.0, "stop": threading.Event()}
    fd = None
    while True:
        if fd is None:
            path = find_event()
            if not path:
                time.sleep(3)
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                log(f"слушаю {path}")
            except OSError as exc:
                log(f"open {path}: {exc}")
                time.sleep(3)
                continue
        r, _, _ = select.select([fd], [], [], 1.0)
        if not r:
            continue
        try:
            data = os.read(fd, 24 * 16)
        except OSError:
            os.close(fd)
            fd = None
            log("устройство пропало, жду")
            continue
        for i in range(0, len(data) - 23, 24):
            sec, usec, typ, code, val = struct.unpack("llHHi", data[i:i + 24])
            if typ != 1:
                continue
            lag = time.time() - (sec + usec / 1e6)  # задержка между событием ядра и нашим чтением
            note = ""
            if val == 1 and code in KEYS:
                note = on_press(st)
            log(f"key code={code} val={val} phase={st['phase']} lag={lag:.2f}с {note}")


if __name__ == "__main__":
    sys.exit(main())

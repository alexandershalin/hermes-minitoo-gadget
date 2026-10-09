#!/usr/bin/env python3
"""Play/Pause на колонке MiniToo (AVRCP) -> toggle записи голоса в Hermes.

Поток: нажатие -> `hermes-minitoo button talk press` -> «Говорите!» (pw-play) -> pw-record + VAD
(стоп по тишине или повторным нажатием) -> `button talk release`.

Только stdlib, запускается системным /usr/bin/python3. Без настроек всё, что уходит в Hermes
Gadget и на колонку, такое же, как раньше: те же вызовы CLI, pw-play, pw-record, те же решения
VAD и тайминги. По умолчанию добавлены только строки журнала: статус гаджета (чтение через
control.sock) после press и после VAD и «VAD rms(100мс)=[...]» на каждую запись.

Настройки: переменная окружения MINITOO_<ИМЯ> > ключ <имя> раздела "talk_key" в config.json >
умолчание. REPO и CONFIG — только из окружения. Настройки читаются один раз при старте (audio.* и
minitoo.address, как и раньше, — при каждой записи). Раздел talk_key добавляйте, только когда
hermes_minitoo/config.py его принимает (со старым config.py hermes-minitoo не запустится), до того — env.

Пути (умолчания = прежние жёсткие пути):
  MINITOO_REPO     ~/hermes-minitoo-gadget                     корень репозитория (только env)
  MINITOO_CONFIG   <repo>/config.json                          audio.*, minitoo.address (только env)
  bin              <repo>/.venv/bin/hermes-minitoo             CLI для button talk press/release
  state_dir        ${XDG_STATE_HOME:-~/.local/state}/hermes-minitoo-gadget   где control.sock
  speak_wav        ~/.cache/minitoo-speak.wav                  подсказка «Говорите»
  tts_python       ~/.hermes/installs/<хеш>/.../venv/bin/python   Python для TTS-клиента
  tts_client       ~/.local/bin/piper-tts-client.py            TTS-клиент Piper
  ffmpeg           ~/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg   пауза 0,9 с перед словом
  tts_voice        dmitri                                      голос подсказки
  (tts_* и ffmpeg нужны, только пока speak_wav нет или он пустой.)

Поведение (умолчания = прежнее поведение):
  имя                 тип             умолч.   смысл
  control             cli|socket      cli      socket: press/release одной JSON-строкой в
                                               control.sock (без 0,5–1 с на запуск CLI)
  press_during_start  ignored|cancel  ignored  cancel: нажатие во время «Говорите» отменяет
                                               запись; только при control=socket
  hfp_keys            bool            0        AT-команды колонки из журнала WirePlumber = кнопка
  hfp_keys_start      bool            0        hfp-команда в idle начинает запись (не раньше
                                               8 с после конца прошлой)
  preroll_wait_sco    bool            0        после press до 4 с ждать SCO/eSCO к колонке, потом
                                               «Говорите» (пара к minitoo.listen_preroll)
  speak_prompt        bool            1        0: не играть «Говорите» (экран Listening уже виден)
  scroll_keys         bool            0        1: джойстик колонки в idle = кнопки up/down Gadget
                                               (последний ответ); в записи не используется
  silence_s           с               1.5      тишина после речи -> стоп
  nospeech_s          с               12       речи нет столько -> стоп
  max_s               с               30       предел записи
  stall_s             с               = max_s  нет ни байта от pw-record столько -> стоп
  vad_warmup_s        с               0        отбросить начало записи (мусор старта SCO)
  vad_calib_s         с               0.8      калибровка уровня шума
  vad_floor           RMS             250      минимальный порог
  vad_mult            ×               3        порог = vad_mult × уровень шума
  vad_ceiling         RMS             0        потолок порога (0 = выкл.); речь в первые 0,8 с
                                               иначе завышает порог. Не ниже vad_floor
  vad_adaptive        bool            0        скользящий уровень шума (10-й перцентиль за 3 с,
                                               рост ≤ 2 % за блок); речь — серия громких блоков
                                               после прогрева и калибровки; серия, начатая ещё
                                               во время калибровки, — только если длится ещё
                                               vad_calib_s после неё (мусор старта SCO и пауза
                                               после него не заканчивают запись до речи)

bool: 1/true/yes/on или 0/false/no/off (в config.json — true/false).

hfp_keys: поток читает `journalctl --user -u wireplumber.service -f -o cat TOPIC=spa.bluez5.native`
и держит уровень "N,spa.bluez5.native:I" через `wpctl set-log-level` (при старте и после рестарта
WirePlumber, проверка раз в 60 с по `pw-metadata -n settings`). Строки «modem not available: AT…»
(INFO) и «RFCOMM event: AT…» (DEBUG): AT+CHUP, AT+BVRA, ATA, AT+CKPD, AT+BLDN, ATD — кнопка.
В recording — стоп, в idle — только лог (или старт при hfp_keys_start), в starting — только лог.
Адреса устройства в этих строках нет: годится, пока MiniToo — единственная HFP-гарнитура на сервере.
"""
import array
import json
import math
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

KEYS = {164, 200, 201, 119, 207}  # PLAYPAUSE, PLAYCD, PAUSECD, PAUSE, PLAY
SCROLL_KEYS = {165: "up", 163: "down"}  # джойстик колонки (PREVIOUSSONG/NEXTSONG) -> up/down Gadget
MAX_REC_S = 30
DEBOUNCE_S = 1.0
EVENT = struct.Struct("llHHi")  # struct input_event: 24 байта на x86_64
CHUNK = 3200  # 100 мс: s16 mono 16 кГц
INPUT_DEVICES = "/proc/bus/input/devices"
MINITOO_IDS = ("0005", "05d6", "000a")  # Bus, Vendor, Product: BlueZ копирует DID колонки в uinput
_TTS_PYTHON = (".hermes/installs/20715197cc5be820/environments/"
               "738223755d2649faa3439a3b8f7036ae/venv/bin/python")

CONTROL_TIMEOUT_S = 3.0  # как hermes_gadget.linux.control.request
RESCAN_S = 5.0  # пересканировать /proc/bus/input/devices, пока устройство открыто
RESCAN_EMPTY_S = 3.0  # ... и пока ни одного нет
SCO_WAIT_S = 4.0
HFP_QUIET_S = 8.0  # hfp-старт не раньше: прошивка может сама слать AT+… при закрытии SCO
HFP_DEDUPE_S = 0.5  # одна команда приходит и INFO-, и DEBUG-строкой
HFP_JOURNAL = ["journalctl", "--user", "-u", "wireplumber.service", "-f", "-n", "0", "-o", "cat",
               "TOPIC=spa.bluez5.native"]
HFP_RE = re.compile(r"(?:modem not available|RFCOMM event): (AT\S*)", re.IGNORECASE)
HFP_KEYS = {"AT+CHUP", "AT+BVRA", "ATA", "AT+CKPD", "AT+BLDN", "ATD"}
WP_LOG_LEVEL = "N,spa.bluez5.native:I"  # никогда не "-": он стирает все шаблоны уровней
WP_CHECK_S = 60.0
ADAPTIVE_WINDOW = 30  # блоков по 100 мс
ADAPTIVE_PCT = 10
ADAPTIVE_RISE = 0.02

# имя: (тип или кортеж допустимых значений, умолчание); None у путей — см. _default_paths()
OPTIONS = {
    "bin": (str, None),
    "state_dir": (str, None),
    "speak_wav": (str, None),
    "tts_python": (str, None),
    "tts_client": (str, None),
    "tts_voice": (str, "dmitri"),
    "ffmpeg": (str, None),
    "control": (("cli", "socket"), "cli"),
    "press_during_start": (("ignored", "cancel"), "ignored"),
    "hfp_keys": (bool, False),
    "hfp_keys_start": (bool, False),
    "preroll_wait_sco": (bool, False),
    "speak_prompt": (bool, True),
    "scroll_keys": (bool, False),
    "silence_s": (float, 1.5),
    "nospeech_s": (float, 12.0),
    "max_s": (float, float(MAX_REC_S)),
    "stall_s": (float, None),  # None = max_s
    "vad_warmup_s": (float, 0.0),
    "vad_calib_s": (float, 0.8),
    "vad_floor": (float, 250.0),
    "vad_mult": (float, 3.0),
    "vad_adaptive": (bool, False),
    "vad_ceiling": (float, 0.0),
}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

LOCK = threading.Lock()  # переходы st["phase"]
SHUTDOWN = threading.Event()
CFG = None  # настройки; main() загружает их с talk_key, иначе cfg() — только из env
_HFP_PROC = None  # journalctl для hfp_keys
_LINK = None  # LinkProbe для preroll_wait_sco; False — недоступен


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --- настройки -----------------------------------------------------------------------------

def _home(env):
    return env.get("HOME") or os.path.expanduser("~")


def _expand(path, home):
    """~ и ~/... относительно HOME из env (а не из os.environ)."""
    return home + path[1:] if path == "~" or path.startswith("~/") else path


def _default_paths(env):
    home = Path(_home(env))
    repo = Path(_expand(env["MINITOO_REPO"], str(home))) if env.get("MINITOO_REPO") \
        else home / "hermes-minitoo-gadget"
    state = Path(env.get("XDG_STATE_HOME", str(home / ".local/state")))  # как cli.default_state_dir
    return {
        "repo": str(repo),
        "config": _expand(env.get("MINITOO_CONFIG") or str(repo / "config.json"), str(home)),
        "indicator": str(home / ".cache/minitoo-indicator"),
        "bin": str(repo / ".venv/bin/hermes-minitoo"),
        "state_dir": str(state / "hermes-minitoo-gadget"),
        "speak_wav": str(home / ".cache/minitoo-speak.wav"),
        "tts_python": str(home / _TTS_PYTHON),
        "tts_client": str(home / ".local/bin/piper-tts-client.py"),
        "ffmpeg": str(home / ".hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg"),
    }


def _parse(kind, raw, from_env, home):
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        if from_env and isinstance(raw, str) and raw.strip().lower() in _TRUE | _FALSE:
            return raw.strip().lower() in _TRUE
        raise ValueError(raw)
    if kind is float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(raw)
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError(raw)
        return value
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(raw)
    if kind is str:
        return _expand(raw.strip(), home)
    if raw.strip().lower() not in kind:
        raise ValueError(raw)
    return raw.strip().lower()


def load_settings(env=None, talk_key=None):
    """env MINITOO_<ИМЯ> > talk_key[<имя>] > умолчание. Ничего не читает с диска."""
    env = os.environ if env is None else env
    talk_key = talk_key if isinstance(talk_key, dict) else {}
    paths = _default_paths(env)
    s = types.SimpleNamespace(repo=paths["repo"], config=paths["config"], indicator=paths["indicator"])
    for name, (kind, default) in OPTIONS.items():
        default = paths.get(name, default)
        source, raw = "MINITOO_" + name.upper(), env.get("MINITOO_" + name.upper())
        if raw is None and name in talk_key:
            source, raw = "talk_key." + name, talk_key[name]
        value = default
        if raw is not None:
            try:
                value = _parse(kind, raw, source.startswith("MINITOO_"), _home(env))
            except (TypeError, ValueError):
                log(f"настройка {source}={raw!r} некорректна, беру {default!r}")
        setattr(s, name, value)
    for name in talk_key:
        if name not in OPTIONS:
            log(f"talk_key.{name}: неизвестный ключ, пропускаю")
    if s.stall_s is None:
        s.stall_s = s.max_s
    # CLI знает только свой state_dir по умолчанию: другой передаём явно
    s.cli_state_dir = None if s.state_dir == paths["state_dir"] else s.state_dir
    if s.press_during_start == "cancel" and s.control != "socket":
        log("press_during_start=cancel только с control=socket (два вызова CLI дольше 1 с, а "
            "talk+cancel >= 1 с открывает меню настроек Gadget): нажатия во время «Говорите» игнорирую")
        s.press_during_start = "ignored"
    return s


def cfg():
    global CFG
    if CFG is None:
        CFG = load_settings()
    return CFG


def _startup_settings(env):
    path = _default_paths(env)["config"]
    talk_key = {}
    try:
        with open(path, encoding="utf-8") as f:
            talk_key = json.load(f).get("talk_key", {})
    except (OSError, ValueError, AttributeError) as exc:
        log(f"talk_key из {path} не прочитан: {exc!r}")
    if not isinstance(talk_key, dict):
        log("talk_key в config.json не объект, пропускаю")
        talk_key = {}
    s = load_settings(env, talk_key)
    base = load_settings({k: v for k, v in env.items() if not k.startswith("MINITOO_")}, {})
    changed = [f"{k}={v}" for k, v in vars(s).items() if getattr(base, k, None) != v]
    if changed:
        log("настройки: " + ", ".join(changed))
    return s


# --- config.json и устройства ---------------------------------------------------------------

def _config():
    with open(cfg().config, encoding="utf-8") as f:
        return json.load(f)


def _audio(key):
    return _config()["audio"][key]


def _sink():
    return _audio("output")


def _minitoo_address():
    try:
        return str(_config()["minitoo"]["address"]).upper()
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _minitoo_hci_dev():
    """Индекс адаптера (hciN) из minitoo.hci_dev; тот же ключ читает hfp_gate в display.py."""
    try:
        value = _config()["minitoo"].get("hci_dev", 0)
        return value if type(value) is int and 0 <= value <= 31 else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0


def find_events(path=INPUT_DEVICES):
    """AVRCP-устройства колонки {"/dev/input/eventN": имя}, по возрастанию N.

    Основной признак — Bus=0005 Vendor=05d6 Product=000a (BlueZ копирует DID колонки в
    uinput, а имя бывает разным: -Audio/-App); если таких нет — "MiniToo" и "(AVRCP)" в имени.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return {}
    primary, fallback = [], []
    for block in blocks:
        name = re.search(r'^N: Name="(.*)"\s*$', block, re.M)
        event = re.search(r"^H: Handlers=.*\bevent(\d+)\b", block, re.M)
        if not name or not event or "(AVRCP)" not in name.group(1):
            continue
        ids = re.search(r"^I: Bus=(\w+) Vendor=(\w+) Product=(\w+)", block, re.M)
        item = (int(event.group(1)), name.group(1))
        if ids and tuple(x.lower() for x in ids.groups()) == MINITOO_IDS:
            primary.append(item)
        elif "MiniToo" in name.group(1):
            fallback.append(item)
    return {f"/dev/input/event{n}": name for n, name in sorted(primary or fallback)}


def _profile(address=None):
    """Профиль BT-карточки MiniToo (bluez_card.<адрес через _>) по pw-dump; None, если её нет."""
    addr = _minitoo_address() if address is None else address.upper()
    card = "bluez_card." + addr.replace(":", "_")
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5)
        for o in json.loads(r.stdout):
            info = o.get("info") or {}
            props = info.get("props") or {}
            name = str(props.get("device.name", ""))
            if not addr or not name.startswith("bluez_card"):
                continue
            if name.upper() == card.upper() or str(props.get("api.bluez5.address", "")).upper() == addr:
                pr = (info.get("params") or {}).get("Profile") or []
                return pr[0].get("name") if pr else None
    except Exception:
        return None
    return None


def set_ind(state):
    """Индикатор на экране отключён (по просьбе Саши); только чистим старый файл."""
    try:
        if os.path.exists(cfg().indicator):
            os.remove(cfg().indicator)
    except OSError:
        pass


# --- подсказка «Говорите» -------------------------------------------------------------------

def ensure_wav():
    """«Говорите» с 0,9 с тишины в начале: колонка при смене профиля съедает начало звука.

    Атомарно (TTS -> ffmpeg во временный файл -> os.replace), пустой файл (<= 44 байт, только
    WAV-заголовок) создаётся заново. Ошибка TTS/ffmpeg не роняет сервис. True — подсказка есть.
    """
    c = cfg()
    wav = c.speak_wav
    try:
        if os.path.getsize(wav) > 44:
            return True
    except OSError:
        pass
    folder = os.path.dirname(wav) or "."
    raw = wav + ".orig"
    txt = tmp = None
    try:
        os.makedirs(folder, exist_ok=True)
        fd, txt = tempfile.mkstemp(prefix="minitoo-speak-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("Говорите!")
        subprocess.run([c.tts_python, c.tts_client, txt, raw, c.tts_voice], check=True, timeout=120)
        fd, tmp = tempfile.mkstemp(prefix=".minitoo-speak-", suffix=".wav", dir=folder)
        os.close(fd)
        try:
            r = subprocess.run([c.ffmpeg, "-loglevel", "error", "-y", "-i", raw, "-af",
                                "adelay=900:all=1,apad=pad_dur=0.3", "-ar", "48000", tmp], timeout=60)
            padded = r.returncode == 0 and os.path.getsize(tmp) > 44
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"ffmpeg: {exc!r}")
            padded = False
        if padded:
            os.replace(tmp, wav)
            tmp = None
        else:
            os.replace(raw, wav)  # как раньше: без паузы в начале
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"подсказка «Говорите» не создана: {exc!r}")
        return False
    finally:
        for p in (txt, tmp):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _retry_wav():
    """Подсказка не создалась при старте (Piper ещё не поднялся?): пробовать в фоне, реже и реже."""
    delay = 30
    while not SHUTDOWN.wait(delay):
        if ensure_wav():
            log("подсказка «Говорите» создана")
            return
        delay = min(3600, delay * 2)


def play_prompt():
    if not cfg().speak_prompt:
        log("подсказка «Говорите» отключена (speak_prompt=0)")
        return
    wav = cfg().speak_wav
    if not os.path.exists(wav):
        log("подсказки «Говорите» нет, записываю без неё")
        return
    try:
        r = subprocess.run(["pw-play", f"--target={_sink()}", wav],
                           timeout=15, capture_output=True, text=True)
        log(f"говорите rc={r.returncode} {r.stderr.strip()[:80]}")
    except Exception as exc:
        log(f"pw-play: {exc!r}")


# --- VAD ------------------------------------------------------------------------------------

class _Pcm:
    """pw-record блоками CHUNK через select + os.read: без вечной блокировки, если данных нет."""

    def __init__(self, stream):
        self.fd = stream.fileno()
        self.buf = bytearray()
        self.eof = False
        self.last_data = time.monotonic()

    def read(self, wait_s):
        """CHUNK байт; меньше (возможно b"") при EOF; None, если за wait_s блок не набрался."""
        deadline = time.monotonic() + wait_s
        while len(self.buf) < CHUNK and not self.eof:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], left)
            if not ready:
                return None
            data = os.read(self.fd, CHUNK - len(self.buf))
            if data:
                self.buf += data
                self.last_data = time.monotonic()
            else:
                self.eof = True
        out = bytes(self.buf[:CHUNK])
        del self.buf[:CHUNK]
        return out


def _last_run_end(values, thr, settled):
    """Индекс конца последней серии значений > thr, если она считается речью, иначе None.

    values[:settled] — калибровка. Серия, начатая после неё, — речь. Серия, начатая во время
    калибровки (речь сразу после «Говорите» или мусор старта SCO), — речь, только если длится
    ещё settled блоков после калибровки.
    """
    i = len(values) - 1
    while i >= 0 and values[i] <= thr:
        i -= 1
    end = i
    while i >= 0 and values[i] > thr:
        i -= 1
    if end >= 0 and (i + 1 >= settled or end - settled + 1 >= settled):
        return end
    return None


class Vad:
    """Решения VAD по RMS блоков 100 мс (без ввода-вывода).

    Классический режим (по умолчанию, как раньше): порог = max(floor, mult × медиана первых
    calib блоков). adaptive: уровень шума = низкий перцентиль последних 3 с, падает сразу,
    растёт не быстрее ADAPTIVE_RISE за блок; речь — по _last_run_end после калибровки, поэтому
    мусор старта SCO и пауза после него не заканчивают запись до речи.
    """

    def __init__(self, t0, silence_s=1.5, nospeech_s=12.0, warmup_s=0.0, calib_s=0.8,
                 floor=250.0, mult=3.0, adaptive=False, ceiling=0.0):
        self.t0, self.silence_s, self.nospeech_s = t0, silence_s, nospeech_s
        self.warmup = round(warmup_s * 10)
        self.calib = max(1, round(calib_s * 10))
        self.floor, self.mult, self.adaptive = floor, mult, adaptive
        self.ceiling = max(ceiling, floor) if ceiling > 0 else 0.0
        self.seen = 0
        self.noise = []
        self.thr = None
        self.speech = False
        self.last_voice = t0
        self.times, self.levels = [], []  # adaptive: после прогрева
        self.nf = None

    def feed(self, rms, now):
        """Причина остановки или None."""
        self.seen += 1
        if self.seen <= self.warmup:
            return None
        if self.adaptive:
            if not self._adaptive(rms, now):
                return None
        else:
            if self.thr is None:
                self.noise.append(rms)
                if len(self.noise) >= self.calib:
                    self.thr = self._cap(max(self.floor, self.mult * sorted(self.noise)[len(self.noise) // 2]))
                    log(f"VAD порог={self.thr:.0f}")
                return None
            if rms > self.thr:
                self.speech = True
                self.last_voice = now
        if self.speech and now - self.last_voice > self.silence_s:
            return "тишина после речи"
        if not self.speech and now - self.t0 > self.nospeech_s:
            return "речи нет"
        return None

    def _cap(self, thr):
        return min(thr, self.ceiling) if self.ceiling else thr

    def _adaptive(self, rms, now):
        """Обновить уровень шума и речь; False, пока идёт калибровка."""
        self.times.append(now)
        self.levels.append(rms)
        window = sorted(self.levels[-ADAPTIVE_WINDOW:])
        level = window[len(window) * ADAPTIVE_PCT // 100]
        self.nf = level if self.nf is None else min(level, self.nf * (1 + ADAPTIVE_RISE))
        self.thr = self._cap(max(self.floor, self.mult * self.nf))
        if len(self.levels) == self.calib:
            log(f"VAD порог={self.thr:.0f} адаптивный")
        if len(self.levels) <= self.calib:
            return False
        end = _last_run_end(self.levels, self.thr, self.calib)
        if end is not None and (not self.speech or self.times[end] > self.last_voice):
            self.speech, self.last_voice = True, self.times[end]
        return True


def vad_wait(stop_evt=None, max_s=None, silence_s=None, nospeech_s=None):
    """Ждёт конец фразы по тишине. Возвращает причину остановки."""
    c = cfg()
    max_s = c.max_s if max_s is None else max_s
    silence_s = c.silence_s if silence_s is None else silence_s
    nospeech_s = c.nospeech_s if nospeech_s is None else nospeech_s
    proc = subprocess.Popen(["pw-record", f"--target={_audio('input')}", "--rate", "16000",
                             "--channels", "1", "--format", "s16", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    t0 = time.time()
    vad = Vad(t0, silence_s, nospeech_s, c.vad_warmup_s, c.vad_calib_s, c.vad_floor, c.vad_mult,
              c.vad_adaptive, c.vad_ceiling)
    pcm = _Pcm(proc.stdout)
    trace = []
    try:
        while time.time() - t0 < max_s:
            buf = pcm.read(0.2)
            if stop_evt is not None and stop_evt.is_set():
                return "кнопка"
            if buf is None:
                if time.monotonic() - pcm.last_data > c.stall_s:
                    return f"нет звука от pw-record {c.stall_s:g} с"
                continue
            if len(buf) < CHUNK:
                return "поток закрыт"
            a = array.array("h")
            a.frombytes(buf)
            rms = math.sqrt(sum(x * x for x in a) / len(a))
            if len(trace) < 40:
                trace.append(round(rms))
            why = vad.feed(rms, time.time())
            if why:
                return why
        return "максимум"
    finally:
        proc.kill()
        proc.wait()
        proc.stdout.close()
        thr = "-" if vad.thr is None else f"{vad.thr:.0f}"
        log(f"VAD rms(100мс)={trace} порог={thr}{' адаптивный' if vad.adaptive else ''}")


# --- Hermes Gadget: кнопки и статус ---------------------------------------------------------

def control_request(message, timeout=None):
    """Как hermes_gadget.linux.control.request(): одна JSON-строка в <state_dir>/control.sock."""
    raw = json.dumps(message, allow_nan=False).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(CONTROL_TIMEOUT_S if timeout is None else timeout)
        sock.connect(os.path.join(cfg().state_dir, "control.sock"))
        sock.sendall(raw)
        data = bytearray()
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("device service closed the connection")
            data += chunk
            if len(data) > 1 << 20:
                raise RuntimeError("control response is too large")
    reply = json.loads(data.split(b"\n", 1)[0])
    if not isinstance(reply, dict):
        raise ValueError("control response is not an object")
    return reply


def _control_button(which, pressed):
    reply = control_request({"command": "button", "button": which, "pressed": pressed})
    if "error" in reply:
        raise RuntimeError(str(reply["error"])[:200])


def _output_text(*parts):
    out = []
    for p in parts:
        if isinstance(p, bytes):
            p = p.decode("utf-8", "replace")
        if p:
            out.append(p)
    return " ".join(out).strip()[:200]


def _button_cli(state, which="talk"):
    c = cfg()
    cmd = [c.bin] + (["--state-dir", c.cli_state_dir] if c.cli_state_dir else []) + ["button", which, state]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        out = _output_text(exc.stdout, exc.stderr)
        log(f"button {which} {state} failed: таймаут {exc.timeout:g} с {out}")
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"button {which} {state} failed: {exc!r}")
        return False
    if r.returncode != 0:  # CLI печатает ошибку в stdout (cli.py), а не в stderr
        log(f"button {which} {state} failed (rc={r.returncode}): {_output_text(r.stdout, r.stderr)}")
    return r.returncode == 0


def button(state, which="talk"):
    """press/release кнопки Gadget: CLI (по умолчанию) или control.sock (control=socket).

    К CLI откатываемся, только если сокета нет или он не принимает соединения — тогда press
    точно не дошёл. Таймаут не повторяем через CLI: иначе возможно двойное нажатие.
    """
    if cfg().control == "socket":
        try:
            _control_button(which, state == "press")
            return True
        except (FileNotFoundError, ConnectionRefusedError, PermissionError) as exc:
            log(f"control.sock недоступен ({exc!r}), button {which} {state} через CLI")
        except (OSError, ValueError, RuntimeError) as exc:
            log(f"button {which} {state} failed: {exc!r}")
            return False
    return _button_cli(state, which)


def _log_status_async(tag):
    """Только лог: экран, фаза и ошибки звука Hermes Gadget (status через control.sock, в фоне).

    Gadget сам отменяет запись при ошибке входного потока PortAudio, а press отвечает ok, даже
    если запись не началась (подсказка, вопрос на экране), — без этого лога такие случаи выглядят
    как «нажатие ничего не сделало». Нет сокета — молча пропускаем.
    """
    def run():
        try:
            st = control_request({"command": "status"})
        except (FileNotFoundError, ConnectionRefusedError, PermissionError):
            return
        except (OSError, ValueError, RuntimeError) as exc:
            log(f"gadget[{tag}]: status не получен: {exc!r}")
            return
        audio = st.get("audio") if isinstance(st.get("audio"), dict) else {}
        log(f"gadget[{tag}]: screen={st.get('screen')} phase={st.get('phase')} "
            f"audio_errors={audio.get('errors')}")
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def cancel_listening():
    """press_during_start=cancel: cancel press и release подряд через control.sock, пока talk зажат.

    Hermes core: cancel при зажатом talk в Listening отменяет запись. Только сокет: два вызова
    CLI дольше 1 с, а talk+cancel >= 1 с открывает меню настроек. Если экран уже не Listening,
    cancel не шлём: его release ответил бы «нет» на вопрос или отменил бы идущий ход.
    """
    try:
        screen = control_request({"command": "status"}).get("screen")
        if screen != "listening":
            log(f"отмена: экран {screen}, cancel не шлю")
            return False
        _control_button("cancel", True)
    except (OSError, ValueError, RuntimeError) as exc:
        log(f"отмена не удалась: {exc!r}")
        return False
    for attempt in (1, 2):  # release без зажатого cancel — no-op, повтор безопасен
        try:
            _control_button("cancel", False)
            log("запись отменена (cancel)")
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            log(f"cancel release, попытка {attempt}: {exc!r}")
    return False


# --- preroll_wait_sco -----------------------------------------------------------------------

def _sco_check():
    """(функция адрес -> есть ли SCO/eSCO к колонке, шаг опроса в секундах)."""
    global _LINK
    if _LINK is None:
        try:
            src = os.path.join(cfg().repo, "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            from hermes_minitoo import linkstate
            probe = linkstate.LinkProbe(_minitoo_hci_dev())
            probe.connections()  # одно чтение таблицы соединений: есть ли доступ к hci0
            _LINK = probe
        except Exception as exc:
            log(f"preroll_wait_sco: hermes_minitoo.linkstate недоступен ({exc!r}), жду профиль "
                "headset-head-unit")
            _LINK = False
    if _LINK:
        return _LINK.sco_up, 0.05
    return (lambda addr: str(_profile(addr) or "").startswith("headset-head-unit")), 0.25


def wait_sco(stop_evt, max_s=SCO_WAIT_S):
    """После press: до max_s ждать SCO/eSCO к minitoo.address (пара к minitoo.listen_preroll)."""
    addr = _minitoo_address()
    check, step = _sco_check()
    t0 = time.monotonic()
    while not stop_evt.is_set() and not SHUTDOWN.is_set():
        try:
            if check(addr):
                log(f"SCO к колонке есть через {time.monotonic() - t0:.2f} с")
                return True
        except Exception as exc:
            log(f"preroll_wait_sco: {exc!r}")
            return False
        if time.monotonic() - t0 >= max_s:
            log(f"SCO к колонке нет за {max_s:g} с, играю подсказку")
            return False
        time.sleep(step)
    return False


# --- запись ---------------------------------------------------------------------------------

def record_once(st):
    """Одна запись: press -> «Говорите» -> VAD/кнопка -> release (release — всегда)."""
    with LOCK:
        st["phase"] = "starting"
    try:
        if not button("press"):
            return  # release в finally: no-op, если press не дошёл; снимает «зажатие», если дошёл
        _log_status_async("press")
        if cfg().preroll_wait_sco:
            wait_sco(st["stop"])
        if not st.get("cancel"):
            play_prompt()
        with LOCK:
            st["phase"] = "recording"
        log(f"запись начата, профиль={_profile()}")
        if st.get("cancel"):
            why = "отмена нажатием во время «Говорите»"
        else:
            try:
                why = vad_wait(st["stop"])
            except Exception as exc:
                why = f"vad ошибка {exc!r}"
        log(f"остановка: {why}")
        _log_status_async("vad-end")
        if st.get("cancel"):
            cancel_listening()
    finally:
        button("release")


def worker(st):
    try:
        record_once(st)
    except Exception as exc:
        log(f"запись упала: {exc!r}")
    finally:
        with LOCK:
            st["end"] = time.time()  # до "idle", иначе нажатие между строками обойдёт debounce
            st["phase"] = "idle"


def on_press(st, source="avrcp"):
    """Нажатие. idle -> старт записи, recording -> досрочный стоп, иначе игнор.

    source="hfp" (AT-команда колонки): в idle только лог, если не включён hfp_keys_start, и даже
    тогда не раньше HFP_QUIET_S после конца записи; во время «Говорите» — только лог.
    """
    with LOCK:
        phase = st["phase"]
        now = time.time()
        if phase == "idle":
            if SHUTDOWN.is_set():
                return "ignored"
            if source == "hfp":
                if not cfg().hfp_keys_start:
                    return "только лог"
                if now - st["end"] <= HFP_QUIET_S:
                    return f"игнор: меньше {HFP_QUIET_S:g} с после записи"
            if now - st["end"] < DEBOUNCE_S:
                return "debounce"
            st["stop"] = threading.Event()
            st["cancel"] = False
            st["started"] = now
            st["phase"] = "starting"
            th = threading.Thread(target=worker, args=(st,), daemon=True)
            st["thread"] = th
            th.start()
            return "start"
        if phase == "recording":
            st["stop"].set()
            return "stop"
        if (phase == "starting" and source == "avrcp" and cfg().press_during_start == "cancel"
                and now - st.get("started", 0.0) >= DEBOUNCE_S):  # дубль события AVRCP — не отмена
            st["cancel"] = True
            st["stop"].set()
            return "cancel"
        return "ignored"


# --- hfp_keys -------------------------------------------------------------------------------

def hfp_command(line):
    """AT-команда из строки журнала WirePlumber (в верхнем регистре) или None."""
    m = HFP_RE.search(line)
    return m.group(1).upper() if m else None


def hfp_key(cmd):
    """Имя команды-«кнопки» по началу команды (AT+CHUP, AT+BVRA=1 -> AT+BVRA, ATD123; -> ATD)."""
    if cmd == "ATA":
        return cmd
    if cmd.startswith("ATD"):
        return "ATD"
    m = re.match(r"AT\+[A-Z]+", cmd)
    return m.group(0) if m and m.group(0) in HFP_KEYS else None


class HfpWatcher:
    """Строки журнала -> нажатия; одинаковые команды в пределах HFP_DEDUPE_S — одна."""

    def __init__(self, st, clock=time.monotonic):
        self.st, self.clock = st, clock
        self.last = (None, 0.0)

    def feed(self, line):
        cmd = hfp_command(line)
        if cmd is None:
            return None
        now = self.clock()
        if cmd == self.last[0] and now - self.last[1] < HFP_DEDUPE_S:
            return None
        self.last = (cmd, now)
        phase = self.st["phase"]
        action = on_press(self.st, source="hfp") if hfp_key(cmd) else "-"
        log(f"hfp at={cmd} phase={phase} action={action}")
        return action


def hfp_follow(st, cmd=HFP_JOURNAL):
    """Следит за журналом WirePlumber; journalctl перезапускается, если вышел."""
    global _HFP_PROC
    watcher = HfpWatcher(st)
    delay = 5.0
    while not SHUTDOWN.is_set():
        started = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            log("hfp_keys: journalctl не найден, AT-команды колонки не отслеживаю")
            return
        except OSError as exc:
            log(f"hfp_keys: journalctl: {exc!r}")
        else:
            _HFP_PROC = proc
            with proc.stdout:
                for line in proc.stdout:
                    watcher.feed(line)
            proc.wait()
            if SHUTDOWN.is_set():
                return
            log(f"hfp_keys: journalctl завершился (rc={proc.returncode}), перезапуск через {delay:g} с")
        SHUTDOWN.wait(delay)
        delay = 5.0 if time.monotonic() - started > 60 else min(60.0, delay * 2)


def _run(cmd, timeout=10):
    """(успех, stdout+stderr) без исключений."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, repr(exc)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def wp_log_level_ok(metadata):
    """Есть ли в выводе `pw-metadata -n settings` уровень I/D/T для spa.bluez5.native."""
    return re.search(r"key:'log\.level' value:'[^']*spa\.bluez5\.native:[IDT345]", metadata) is not None


def wp_log_level_keeper():
    """INFO для spa.bluez5.native в WirePlumber: при старте и после его рестарта.

    wpctl пишет log.level в метаданные settings под id клиента WirePlumber (не под id 0),
    PipeWire удаляет эти записи вместе с клиентом — их пропажа и значит рестарт.
    """
    applied = warned = False
    while not SHUTDOWN.is_set():
        meta_ok, meta = _run(["pw-metadata", "-n", "settings"])
        present = meta_ok and wp_log_level_ok(meta)
        if meta_ok and not present:
            applied = False
        if not present:
            ok, out = _run(["wpctl", "set-log-level", WP_LOG_LEVEL])
            if ok and not applied:
                log(f"hfp_keys: wpctl set-log-level {WP_LOG_LEVEL}")
            elif not ok and not warned:
                log(f"hfp_keys: wpctl set-log-level не удалось: {out[:200]}")
                warned = True
            applied = ok
        SHUTDOWN.wait(WP_CHECK_S)


# --- главный цикл ---------------------------------------------------------------------------

def scroll(st, which):
    """Джойстик в idle -> короткое нажатие up/down Gadget (в фоне, чтобы не блокировать чтение)."""
    with LOCK:
        if st["phase"] != "idle":
            return "scroll игнор: не idle"

    def _tap():
        if button("press", which):
            button("release", which)

    threading.Thread(target=_tap, daemon=True).start()
    return f"scroll {which}"


def handle_events(st, data):
    for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
        sec, usec, typ, code, val = EVENT.unpack_from(data, i)
        if typ != 1:
            continue
        lag = time.time() - (sec + usec / 1e6)  # задержка между событием ядра и нашим чтением
        note = ""
        if val == 1 and code in KEYS:
            note = on_press(st)
        elif val == 1 and code in SCROLL_KEYS and cfg().scroll_keys:
            note = scroll(st, SCROLL_KEYS[code])
        log(f"key code={code} val={val} phase={st['phase']} lag={lag:.2f}с {note}")


def _read_events(fd):
    return os.read(fd, EVENT.size * 16)


def listen(st):
    """Нажатия со всех AVRCP-устройств колонки; пересканирует каждые 5 с и после пропажи."""
    fds = {}  # path -> fd
    rescan_at = 0.0
    try:
        while not SHUTDOWN.is_set():
            if time.monotonic() >= rescan_at:
                for path, name in find_events().items():
                    if path in fds:
                        continue
                    try:
                        fds[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        log(f"слушаю {path} ({name})")
                    except OSError as exc:
                        log(f"open {path}: {exc}")
                rescan_at = time.monotonic() + (RESCAN_S if fds else RESCAN_EMPTY_S)
            if not fds:
                SHUTDOWN.wait(max(0.0, rescan_at - time.monotonic()))
                continue
            wait = min(1.0, max(0.0, rescan_at - time.monotonic()))
            ready, _, _ = select.select(list(fds.values()), [], [], wait)
            for path, fd in list(fds.items()):
                if fd not in ready:
                    continue
                try:
                    data = _read_events(fd)
                except BlockingIOError:
                    continue
                except OSError:
                    os.close(fd)
                    del fds[path]
                    rescan_at = 0.0
                    log(f"устройство {path} пропало, жду")
                    continue
                handle_events(st, data)
    finally:
        for fd in fds.values():
            os.close(fd)


def shutdown(st, timeout=10.0):
    """SIGTERM/SIGINT: закончить запись штатно (release в finally record_once), ждать до timeout."""
    SHUTDOWN.set()
    with LOCK:
        st["stop"].set()
        th = st.get("thread")
    if th is not None and th.is_alive():
        log("остановка сервиса: завершаю запись")
        th.join(timeout)
    proc = _HFP_PROC
    if proc is not None and proc.poll() is None:
        proc.terminate()


def _on_signal(signum, frame):
    SHUTDOWN.set()


def main():
    global CFG
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    CFG = _startup_settings(os.environ)
    if not ensure_wav():
        threading.Thread(target=_retry_wav, daemon=True).start()
    set_ind(None)  # не оставлять красный экран от прошлого запуска
    button("release")  # не оставлять «зажатую» кнопку talk после падения
    st = {"phase": "idle", "end": 0.0, "stop": threading.Event()}
    if CFG.hfp_keys:
        threading.Thread(target=hfp_follow, args=(st,), daemon=True).start()
        threading.Thread(target=wp_log_level_keeper, daemon=True).start()
    listen(st)
    shutdown(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())

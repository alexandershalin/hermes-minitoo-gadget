#!/usr/bin/env python3
"""Сторож адреса MiniToo: колонка меняет BT-адрес, находим её по имени/префиксу,
сопрягаем, правим config.json и перезапускаем hermes-minitoo.

Без переменных окружения ниже поведение то же, что в origin/main: те же команды
bluetoothctl (через stdin с теми же паузами), тот же 12-секундный скан, когда
колонка из config.json не подключена, цикл раз в 20 с, тот же выбор адреса
(«чаще всех» в строках «Device X RSSI») и те же условия успеха (Paired=yes и
Connected=yes).

Пути (env):
  MINITOO_CONFIG           config.json; по умолчанию ~/hermes-minitoo-gadget/config.json
                           (~ берётся из HOME)
  MINITOO_AUTOADDR_PREFIX  префикс адреса колонки, по умолчанию "B1:21:81:". Префикс не
                           уникален: у чужих MiniToo он тот же (см. IDENTIFY)

Правка config.json при смене адреса (всегда):
  файл читается как JSON; меняются minitoo.address и, если есть, audio.input и
  audio.output: в них старый адрес заменяется новым в обоих написаниях
  (B1:21:81:.. как у loopback-узлов WirePlumber и B1_21_81_.. как у
  bluez_output.<MAC>.N), без учёта регистра. Остальные ключи не трогаются.
  Запись атомарная: временный файл в том же каталоге, fsync, права исходного
  файла (config.json хранит token, 0600 остаётся 0600), os.replace, fsync каталога.
  Копия прежнего содержимого — config.json.bak-autoaddr, с теми же правами.
  Если после правки ничего не изменилось, файл не пишется и hermes-minitoo
  не перезапускается.

Опции (env, по умолчанию выключены; включаются значением 1/true/yes/on):
  MINITOO_AUTOADDR_SAFE_SCAN=1
      не запускать поиск (inquiry), пока подключена ЛЮБАЯ известная BlueZ запись
      MiniToo (префикс + MiniToo в имени): поиск отнимает эфир у A2DP/SCO/RFCOMM.
      Пока в эфире пусто, паузы между сканами растут 20 с -> 40 -> 80 -> 160 ->
      300 с (5 мин) и сбрасываются, как только колонка видна или подключена.
  MINITOO_AUTOADDR_ACCEPT_UNPAIRED=1
      считать успехом Connected=yes без Paired=yes (колонка теряет сопряжение
      в BlueZ, а SPP при этом работает): так принимается и уже подключённая
      запись MiniToo, и результат pair/connect.
  MINITOO_AUTOADDR_IDENTIFY=1
      адрес попадает в config.json, только если по `bluetoothctl info`:
      Modalias начинается с bluetooth:v05D6p000A (DID JieLi 0x05D6/0x000A; версия
      dXXXX не сравнивается), в Name или Alias есть MiniToo и тип адреса не random
      (random бывает только у LE). Из результатов скана сразу отбрасываются
      устройства без MiniToo в имени, с random-адресом или с чужим Modalias;
      у ещё не подключавшегося устройства Modalias обычно нет, поэтому его
      сопрягают, а Modalias проверяют после подключения, до правки config.json.
      Tiivoo 2 имеет тот же VID/PID, его отсекает проверка имени.
  MINITOO_AUTOADDR_ONESHOT=1
      вместо bluetoothctl через stdin с паузами — отдельные неинтерактивные
      команды: `bluetoothctl --timeout 12 scan bredr` (только BR/EDR; LE-реклама
      не попадает в выборку), `bluetoothctl --agent NoInputNoOutput --timeout 30
      pair X`, `bluetoothctl trust X`, `bluetoothctl connect X`. С фильтром по
      транспорту BlueZ печатает RSSI на каждый отчёт, а не только на скачки
      >= 8 дБ, поэтому счёт «чаще всех» в этом режиме другой.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

CONF = os.environ.get("MINITOO_CONFIG") or os.path.expanduser("~/hermes-minitoo-gadget/config.json")
PREFIX = os.environ.get("MINITOO_AUTOADDR_PREFIX", "B1:21:81:").upper()
NAME_RE = re.compile(r"MiniToo", re.I)
MODALIAS_PREFIX = "bluetooth:v05d6p000a"
INTERVAL = 20  # пауза между циклами, с
SCAN_S = 12
BACKOFF_MAX_S = 300
MAC = r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}"
OPTIONS = ("MINITOO_AUTOADDR_SAFE_SCAN", "MINITOO_AUTOADDR_ACCEPT_UNPAIRED",
           "MINITOO_AUTOADDR_IDENTIFY", "MINITOO_AUTOADDR_ONESHOT")


def _flag(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def btctl(cmds, timeout=40):
    """Команды по очереди с паузами (bluetoothctl не любит пачку без пауз)."""
    p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        for c, wait in cmds:
            p.stdin.write(c + "\n")
            p.stdin.flush()
            time.sleep(wait)
        p.stdin.write("quit\n")
        p.stdin.flush()
        out, _ = p.communicate(timeout=timeout)
    except Exception:
        p.kill()
        p.wait()
        out = ""
    return out


def btctl1(args, timeout):
    """Одна неинтерактивная команда bluetoothctl (MINITOO_AUTOADDR_ONESHOT)."""
    try:
        r = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ошибка: {exc!r}"
    return r.stdout + r.stderr


def _last_line(out):
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", s).strip() for s in out.splitlines()]
    lines = [s for s in lines if s]
    return lines[-1][:200] if lines else "(нет вывода)"


def info(addr):
    r = subprocess.run(["bluetoothctl", "info", addr], capture_output=True, text=True, timeout=10)
    st = dict(re.findall(r"\t(Paired|Trusted|Connected|Name|Alias|Modalias): (.*)", r.stdout))
    m = re.search(rf"^Device {MAC} \((\w+)\)", r.stdout, re.M)
    if m:
        st["AddressType"] = m.group(1)
    return st


def _load():
    with open(CONF, encoding="utf-8") as f:
        return json.load(f)


def conf_addr():
    return _load()["minitoo"]["address"]


def known_minitoo():
    r = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True, timeout=10)
    return [a for a, n in re.findall(rf"Device ({MAC}) (.*)", r.stdout)
            if a.startswith(PREFIX) and NAME_RE.search(n)]


def _named_minitoo(st):
    return bool(NAME_RE.search(st.get("Name", "") + " " + st.get("Alias", "")))


def is_minitoo(st):
    """IDENTIFY: Modalias JieLi 05D6:000A, MiniToo в имени, адрес не random."""
    return (st.get("Modalias", "").lower().startswith(MODALIAS_PREFIX)
            and _named_minitoo(st) and st.get("AddressType") != "random")


def maybe_minitoo(st):
    """IDENTIFY до сопряжения: Modalias может ещё отсутствовать, но не может быть чужим."""
    modalias = st.get("Modalias", "").lower()
    return ((not modalias or modalias.startswith(MODALIAS_PREFIX))
            and _named_minitoo(st) and st.get("AddressType") != "random")


def _ident(st):
    return (f"Name={st.get('Name', '?')!r} Modalias={st.get('Modalias', '-')} "
            f"AddressType={st.get('AddressType', '?')}")


def connected_ok(st):
    if st.get("Connected") != "yes":
        return False
    return st.get("Paired") == "yes" or _flag("MINITOO_AUTOADDR_ACCEPT_UNPAIRED")


def _swap_mac(value, old, new):
    """Старый MAC -> новый в строке, в обоих написаниях и без учёта регистра."""
    if not isinstance(value, str):
        return value
    for o, n in ((old, new), (old.replace(":", "_"), new.replace(":", "_"))):
        value = re.sub(re.escape(o), lambda _m, n=n: n, value, flags=re.I)
    return value


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_atomic(path, text, mode):
    """Записать файл целиком через временный файл рядом и os.replace, с правами mode."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(d)


def apply_address(new):
    """Перевести config.json на адрес new; True, если файл изменён и сервис перезапущен."""
    with open(CONF, encoding="utf-8") as f:
        text = f.read()
    cfg = json.loads(text)
    upd = json.loads(text)
    old = cfg["minitoo"]["address"]
    upd["minitoo"]["address"] = new
    changes = []
    audio = upd.get("audio")
    if isinstance(audio, dict):
        for key in ("input", "output"):
            if key in audio:
                before = audio[key]
                audio[key] = _swap_mac(before, old, new)
                if audio[key] != before:
                    changes.append(f"audio.{key}: {before} -> {audio[key]}")
    if upd == cfg:
        log(f"config: адрес уже {new}, файл не меняю, hermes-minitoo не перезапускаю")
        return False
    mode = os.stat(CONF).st_mode & 0o777  # config.json содержит token: права не расширять
    write_atomic(CONF + ".bak-autoaddr", text, mode)
    write_atomic(CONF, json.dumps(upd, indent=2, ensure_ascii=False) + "\n", mode)
    for change in changes:
        log(f"config: {change}")
    log(f"config: {old} -> {new}; перезапуск hermes-minitoo")
    subprocess.run(["systemctl", "--user", "restart", "hermes-minitoo"], timeout=60)
    return True


def scan_visible(seconds=SCAN_S):
    if _flag("MINITOO_AUTOADDR_ONESHOT"):
        out = btctl1(["--timeout", str(seconds), "scan", "bredr"], timeout=seconds + 20)
    else:
        out = btctl([("scan on", seconds), ("scan off", 1)], timeout=seconds + 20)
    seen = {}
    for a in re.findall(rf"Device ({MAC}) RSSI", out):
        if a.startswith(PREFIX):
            seen[a] = seen.get(a, 0) + 1
    return seen


def pair_connect(addr):
    if _flag("MINITOO_AUTOADDR_ONESHOT"):
        for args, timeout in ((["--agent", "NoInputNoOutput", "--timeout", "30", "pair", addr], 50),
                              (["trust", addr], 20),
                              (["connect", addr], 60)):
            log(f"bluetoothctl {args[-2]} {addr}: {_last_line(btctl1(args, timeout))}")
    else:
        btctl([("agent on", 1), ("default-agent", 1), (f"pair {addr}", 15),
               (f"trust {addr}", 2), (f"connect {addr}", 10)], timeout=70)
    return connected_ok(info(addr))


class ScanBackoff:
    """Состояние между циклами: пауза поиска для MINITOO_AUTOADDR_SAFE_SCAN
    (20 с -> 40 -> 80 -> 160 -> 300 с, пока в эфире пусто) и уже выведенные заметки."""

    def __init__(self, first=INTERVAL, cap=BACKOFF_MAX_S, clock=time.monotonic):
        self.first, self.cap, self.clock = first, cap, clock
        self.reset()

    def rearm(self):
        """Следующий поиск без паузы, пауза снова начнётся с 20 с."""
        self.delay = self.first
        self.next_at = 0.0

    def reset(self):
        """Колонка видна или подключена: rearm() и заметки заново."""
        self.rearm()
        self.noted = set()

    def ready(self):
        return self.clock() >= self.next_at

    def missed(self):
        """Поиск ничего не нашёл: следующий не раньше чем через delay; вернуть эту паузу."""
        wait = self.delay
        self.next_at = self.clock() + wait
        self.delay = min(self.delay * 2, self.cap)
        return wait

    def note(self, msg):
        """Записать в лог один раз, а не каждые 20 с."""
        if msg not in self.noted:
            self.noted.add(msg)
            log(msg)


def cycle(backoff=None):
    backoff = backoff if backoff is not None else ScanBackoff()
    safe = _flag("MINITOO_AUTOADDR_SAFE_SCAN")
    identify = _flag("MINITOO_AUTOADDR_IDENTIFY")
    cur = conf_addr()
    if info(cur).get("Connected") == "yes":
        backoff.reset()
        return
    # другая запись MiniToo уже подключена (например, адрес сменился сам)?
    busy = []
    for a in known_minitoo():
        st = info(a)
        if st.get("Connected") != "yes":
            continue
        busy.append(a)
        if identify and not is_minitoo(st):
            backoff.note(f"{a} подключено, но не опознано как MiniToo ({_ident(st)}); не беру")
            continue
        if connected_ok(st):
            apply_address(a)
            return
    if safe:
        if busy:
            backoff.rearm()
            backoff.note(f"поиск не запускаю: подключено {', '.join(busy)}")
            return
        if not backoff.ready():
            return
    seen = scan_visible()
    if identify:
        for a in list(seen):
            st = info(a)
            if not maybe_minitoo(st):
                backoff.note(f"{a} в эфире, но не MiniToo ({_ident(st)}); пропускаю")
                del seen[a]
    if not seen:
        if safe:
            log(f"в эфире пусто; следующий поиск не раньше чем через {backoff.missed()} с")
        return  # колонка выключена/вне зоны
    backoff.reset()
    # самый «частый» в эфире адрес — живой; старые записи BlueZ молчат
    addr = max(seen, key=seen.get)
    log(f"в эфире {seen}; пробую {addr}")
    if pair_connect(addr):
        log(f"сопряжено и подключено: {addr}")
        if identify:
            st = info(addr)
            if not is_minitoo(st):
                log(f"{addr} не опознано как MiniToo после подключения ({_ident(st)}); config не меняю")
                return
        if addr != cur:
            apply_address(addr)
    else:
        log(f"не удалось подключить {addr}")


def main():
    log(f"сторож адреса MiniToo запущен; config={CONF} prefix={PREFIX}")
    enabled = [name for name in OPTIONS if _flag(name)]
    if enabled:
        log("включены опции: " + ", ".join(enabled))
    backoff = ScanBackoff()
    while True:
        try:
            cycle(backoff)
        except Exception as exc:
            log(f"ошибка цикла: {exc!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())

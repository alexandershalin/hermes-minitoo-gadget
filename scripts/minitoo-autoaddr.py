#!/usr/bin/env python3
"""Сторож адреса MiniToo: колонка меняет BT-адрес, находим её по имени/префиксу,
сопрягаем, правим config.json и перезапускаем hermes-minitoo."""
import json, os, re, subprocess, sys, time

CONF = "/home/bishop/hermes-minitoo-gadget/config.json"
PREFIX = "B1:21:81:"
NAME_RE = re.compile(r"MiniToo", re.I)
INTERVAL = 20


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def btctl(cmds, timeout=40):
    """Команды по очереди с паузами (bluetoothctl не любит пачку без пауз)."""
    p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        for c, wait in cmds:
            p.stdin.write(c + "\n"); p.stdin.flush()
            time.sleep(wait)
        p.stdin.write("quit\n"); p.stdin.flush()
        out, _ = p.communicate(timeout=timeout)
    except Exception:
        p.kill()
        p.wait()
        out = ""
    return out


def info(addr):
    r = subprocess.run(["bluetoothctl", "info", addr], capture_output=True, text=True, timeout=10)
    return {k: v for k, v in re.findall(r"\t(Paired|Trusted|Connected|Name|Alias): (.*)", r.stdout)}


def conf_addr():
    return json.load(open(CONF))["minitoo"]["address"]


def known_minitoo():
    r = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True, timeout=10)
    return [a for a, n in re.findall(r"Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.*)", r.stdout)
            if a.startswith(PREFIX) and NAME_RE.search(n)]


def apply_address(new):
    old = conf_addr()
    text = open(CONF).read()
    open(CONF + ".bak-autoaddr", "w").write(text)
    tmp = CONF + ".tmp"
    open(tmp, "w").write(text.replace(old, new))
    os.replace(tmp, CONF)
    log(f"config: {old} -> {new}; перезапуск hermes-minitoo")
    subprocess.run(["systemctl", "--user", "restart", "hermes-minitoo"], timeout=60)


def scan_visible(seconds=12):
    out = btctl([("scan on", seconds), ("scan off", 1)], timeout=seconds + 20)
    seen = {}
    for a in re.findall(r"Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) RSSI", out):
        if a.startswith(PREFIX):
            seen[a] = seen.get(a, 0) + 1
    return seen


def pair_connect(addr):
    btctl([("agent on", 1), ("default-agent", 1), (f"pair {addr}", 15),
           (f"trust {addr}", 2), (f"connect {addr}", 10)], timeout=70)
    st = info(addr)
    return st.get("Paired") == "yes" and st.get("Connected") == "yes"


def cycle():
    cur = conf_addr()
    if info(cur).get("Connected") == "yes":
        return
    # другая запись MiniToo уже подключена (например, адрес сменился сам)?
    for a in known_minitoo():
        st = info(a)
        if st.get("Connected") == "yes" and st.get("Paired") == "yes":
            apply_address(a)
            return
    seen = scan_visible()
    if not seen:
        return  # колонка выключена/вне зоны
    # самый «частый» в эфире адрес — живой; старые записи BlueZ молчат
    addr = max(seen, key=seen.get)
    log(f"в эфире {seen}; пробую {addr}")
    if pair_connect(addr):
        log(f"сопряжено и подключено: {addr}")
        if addr != cur:
            apply_address(addr)
    else:
        log(f"не удалось подключить {addr}")


def main():
    log("сторож адреса MiniToo запущен")
    while True:
        try:
            cycle()
        except Exception as exc:
            log(f"ошибка цикла: {exc!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())

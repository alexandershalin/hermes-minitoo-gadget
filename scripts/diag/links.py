#!/usr/bin/env python3
"""Живая таблица Bluetooth-соединений адаптера hciN: ACL / SCO / eSCO (и LE). Без root.

Что делает: раз в 0,5 с спрашивает у ядра список соединений адаптера (ioctl
HCIGETCONNLIST) через ОДИН raw HCI-сокет, открытый на всё время работы (каждый новый
сокет виден в btmon как открытие/закрытие), и печатает строку с отметкой времени,
когда таблица меняется. Сокет не привязывается (bind) и HCI-трафик не читает; в эфир
ничего не уходит, колонке ничего не пишется. Семейство сокета задано числом (31), так
что скрипт работает и на Python без socket.AF_BLUETOOTH.

Отвечает на вопрос: когда поднимается и падает SCO/eSCO к колонке, живо ли ACL в момент
сбоев экрана, кто central. Запускать во время записи (нажатие Play/Pause) и во время
scripts/diag/steady-hfp.sh. Адрес колонки берётся из config.json (minitoo.address),
её соединения помечены «*».

--rssi: если установлен hcitool, раз в --rssi-every секунд (только пока есть ACL к
колонке) печатает RSSI и Link Quality соединения (`hcitool rssi/lq`; это запросы к
своему контроллеру, не к колонке). RSSI соединения BR/EDR считается от «золотого»
диапазона: 0 — в норме, отрицательное — ниже. hcitool каждый раз открывает свой сокет.

Запуск:  python3 -I ~/hermes-minitoo-gadget/scripts/diag/links.py [--rssi] [--dev 0]
Выход — Ctrl-C.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections import namedtuple

HCIGETCONNLIST = 0x800448D4  # _IOR('H', 212, int), include/net/bluetooth/hci_sock.h
AF_BLUETOOTH = 31
BTPROTO_HCI = 1
MAX_CONN = 32
# struct hci_conn_list_req { u16 dev_id; u16 conn_num; struct hci_conn_info conn_info[]; }
REQ = struct.Struct("<HH")
# struct hci_conn_info { u16 handle; bdaddr_t bdaddr; u8 type; u8 out; u16 state; u32 link_mode; }
INFO = struct.Struct("<H6sBBHI")
LINK_TYPES = {0x00: "SCO", 0x01: "ACL", 0x02: "eSCO", 0x80: "LE", 0x82: "ISO"}  # include/net/bluetooth/hci.h
STATES = {1: "connected", 2: "open", 3: "bound", 4: "listen", 5: "connecting", 6: "connect2",
          7: "config", 8: "disconnecting", 9: "closed"}  # include/net/bluetooth/bluetooth.h
LM_MASTER, LM_ENCRYPT = 0x0001, 0x0004
DEFAULT_CONFIG = "~/hermes-minitoo-gadget/config.json"

Link = namedtuple("Link", "address type handle central out state encrypted")


def parse_conn_list(buf: bytes) -> list[Link]:
    """Разобрать буфер, заполненный ядром по HCIGETCONNLIST."""
    count = REQ.unpack_from(buf, 0)[1]
    room = (len(buf) - REQ.size) // INFO.size
    links = []
    for i in range(min(count, room)):
        handle, raw, kind, out, state, mode = INFO.unpack_from(buf, REQ.size + i * INFO.size)
        links.append(Link(":".join(f"{b:02X}" for b in reversed(raw)), LINK_TYPES.get(kind, f"type{kind:#x}"),
                          handle, bool(mode & LM_MASTER), bool(out), STATES.get(state, str(state)),
                          bool(mode & LM_ENCRYPT)))
    return links


class ConnList:
    """HCIGETCONNLIST через один долгоживущий сокет; после ошибки он открывается заново."""

    def __init__(self, dev_id: int = 0, opener=None, ioctl=fcntl.ioctl):
        self.dev_id = dev_id
        self._open = opener or (lambda: socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI))
        self._ioctl = ioctl
        self.sock = None
        self.opened = 0

    def query(self) -> list[Link]:
        if self.sock is None:
            self.sock = self._open()
            self.opened += 1
        buf = bytearray(REQ.size + INFO.size * MAX_CONN)
        REQ.pack_into(buf, 0, self.dev_id, MAX_CONN)
        try:
            self._ioctl(self.sock.fileno(), HCIGETCONNLIST, buf, True)
        except OSError as exc:
            if exc.errno != errno.ENODEV:  # нет hciN — сокет при этом исправен
                self.close()
            raise
        return parse_conn_list(buf)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


def format_links(links: list[Link], speaker: str | None) -> str:
    if not links:
        return "-"
    parts = []
    for link in links:
        mark = "*" if speaker and link.address == speaker else " "
        flags = ["central" if link.central else "peripheral"]
        if link.encrypted:
            flags.append("enc")
        if link.out:
            flags.append("out")
        if link.state != "connected":
            flags.append(link.state)
        parts.append(f"{mark}{link.address} {link.type} h={link.handle} {' '.join(flags)}")
    return " | ".join(parts)


def stamp(now: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"


def speaker_address(path: str) -> str | None:
    """minitoo.address из config.json (печатается только адрес, токен не читается наружу)."""
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)["minitoo"]["address"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value.upper() if isinstance(value, str) else None


def hcitool_metrics(address: str, run=subprocess.run) -> str:
    """RSSI и Link Quality соединения через hcitool (запросы к своему контроллеру)."""
    values = []
    for what, pattern in (("rssi", r"RSSI return value:\s*(-?\d+)"), ("lq", r"Link quality:\s*(\d+)")):
        try:
            r = run(["hcitool", what, address], capture_output=True, text=True, timeout=3)
            m = re.search(pattern, r.stdout + r.stderr)
            values.append(f"{what}={m.group(1) if m else '?'}")
        except (OSError, subprocess.SubprocessError):
            values.append(f"{what}=?")
    return " ".join(values)


def watch(probe: ConnList, speaker: str | None, *, interval: float = 0.5, every: bool = False,
          rssi_every: float | None = None, prefix: str = "", out=print, clock=time.time,
          sleep=time.sleep, metrics=hcitool_metrics, limit: int | None = None) -> int:
    """Опрос раз в interval; строка — при изменении таблицы (или на каждом опросе с every).
    Возвращает 0, если последний опрос удался, иначе 1 (важно для --once)."""
    last_line = last_error = None
    last_rssi_at = -1e9
    polls = 0

    def pause(seconds: float) -> None:
        if limit is None or polls < limit:
            sleep(seconds)

    while limit is None or polls < limit:
        polls += 1
        now = clock()
        try:
            links = probe.query()
        except OSError as exc:
            error = f"ошибка HCIGETCONNLIST на hci{probe.dev_id}: {exc}"
            if error != last_error:
                out(f"{prefix}{stamp(now)}  {error}")
            last_error, last_line = error, None
            pause(max(interval, 2.0))
            continue
        last_error = None
        line = format_links(links, speaker)
        if every or line != last_line:
            out(f"{prefix}{stamp(now)}  {line}")
        last_line = line
        if (rssi_every is not None and speaker and now - last_rssi_at >= rssi_every
                and any(link.address == speaker and link.type == "ACL" for link in links)):
            last_rssi_at = now
            out(f"{prefix}{stamp(now)}  {speaker} {metrics(speaker)}")
        pause(interval)
    return 1 if last_error else 0


def main(argv: list[str] | None = None) -> int:
    default_conf = os.environ.get("MINITOO_CONFIG") or os.path.expanduser(DEFAULT_CONFIG)
    p = argparse.ArgumentParser(description="Live ACL/SCO/eSCO link table via HCIGETCONNLIST (no root).")
    p.add_argument("--dev", type=int, default=0, help="номер адаптера N в hciN (по умолчанию 0)")
    p.add_argument("--interval", type=float, default=0.5, help="период опроса, с (0.5)")
    p.add_argument("--every", action="store_true", help="печатать каждый опрос, а не только изменения")
    p.add_argument("--once", action="store_true", help="напечатать таблицу один раз и выйти")
    p.add_argument("--rssi", action="store_true", help="RSSI/LQ соединения с колонкой через hcitool")
    p.add_argument("--rssi-every", type=float, default=2.0, help="период --rssi, с (2)")
    p.add_argument("--config", default=default_conf, help="config.json с minitoo.address")
    p.add_argument("--prefix", default="", help="префикс каждой строки (для общего лога)")
    p.add_argument("--no-header", action="store_true", help="без пояснительной шапки")
    args = p.parse_args(argv)

    speaker = speaker_address(args.config)
    rssi_every = None
    if args.rssi:
        if shutil.which("hcitool"):
            rssi_every = args.rssi_every
        else:
            print(f"{args.prefix}hcitool не установлен: --rssi пропущен", file=sys.stderr)
    if not args.no_header:
        print(f"{args.prefix}hci{args.dev}: соединения раз в {args.interval} с (строка — при изменении); "
              f"колонка из {args.config}: {speaker or 'не прочитана'} (помечена *). Ctrl-C — выход.")
    probe = ConnList(args.dev)
    try:
        return watch(probe, speaker, interval=args.interval, every=args.every, rssi_every=rssi_every,
                     prefix=args.prefix, limit=1 if args.once else None)
    except KeyboardInterrupt:
        return 0
    finally:
        probe.close()


if __name__ == "__main__":
    sys.exit(main())

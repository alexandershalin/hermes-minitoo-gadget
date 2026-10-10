#!/usr/bin/env python3
"""Hermes command-TTS provider: render text through the local Piper daemon.

Usage: piper_tts_client.py INPUT_TXT OUTPUT_WAV [VOICE]
Daemon: piper-tts.service on 127.0.0.1:8770 (see docs/piper-tts.md).
"""
import json
import os
import sys
import urllib.request

URL = os.environ.get("PIPER_TTS_URL", "http://127.0.0.1:8770/tts")


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    text = open(argv[1], encoding="utf-8").read().strip()
    voice = argv[3] if len(argv) > 3 and argv[3] else "dmitri"
    # Авто-язык: ответ без кириллицы читаем английским голосом (ryan), иначе — выбранным русским.
    letters = [c for c in text if c.isalpha()]
    if letters and sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters) / len(letters) < 0.3:
        voice = os.environ.get("PIPER_EN_VOICE", "ryan")
    req = urllib.request.Request(
        URL, data=json.dumps({"text": text[:1000], "voice": voice}).encode(),
        headers={"Content-Type": "application/json"})
    # Direct connection: the daemon is local, never go through a proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as r, open(argv[2], "wb") as out:
        out.write(r.read())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

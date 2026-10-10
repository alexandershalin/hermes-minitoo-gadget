#!/usr/bin/env python3
"""Piper TTS daemon: keeps voice models in memory, 127.0.0.1 only.

GET  /health
POST /tts  {"text": "...", "voice": "dmitri|irina|ryan|amy", "play": false, "target": "<pipewire sink>"}
     -> audio/wav (or JSON {"played": true} when play=true)
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.environ.get("PIPER_SITE", ""))
from piper import PiperVoice  # noqa: E402

DIR = os.path.expanduser("~/.local/share/piper-voices")
VOICES = {
    "dmitri": "ru_RU-dmitri-medium", "irina": "ru_RU-irina-medium",
    "ryan": "en_US-ryan-medium", "amy": "en_US-amy-medium",
}
PRELOAD = ("dmitri", "ryan")
DEFAULT_SINK = os.environ.get("TTS_SINK", "bluez_output.B1:21:81:7C:8A:B5")
MAX_CHARS = 1000
PORT = int(os.environ.get("TTS_PORT", "8770"))

_models, _locks, _mlock = {}, {}, threading.Lock()


def get_voice(name):
    with _mlock:
        if name not in _models:
            _models[name] = PiperVoice.load(os.path.join(DIR, VOICES[name] + ".onnx"))
            _locks[name] = threading.Lock()
        return _models[name], _locks[name]


def synth(text, name):
    voice, lock = get_voice(name)
    buf = io.BytesIO()
    with lock, wave.open(buf, "wb") as w:
        voice.synthesize_wav(text, w)
    return buf.getvalue()


def pad_silence(wav_bytes, lead_ms=600):
    """Bluetooth sinks wake from idle and clip the first ~0.3 s: prepend silence."""
    src = wave.open(io.BytesIO(wav_bytes))
    frames = src.readframes(src.getnframes())
    silence = b"\x00" * (int(src.getframerate() * lead_ms / 1000) * src.getsampwidth() * src.getnchannels())
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(src.getparams())
        w.writeframes(silence + frames)
    return out.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "loaded": sorted(_models), "voices": sorted(VOICES)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/tts":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
            text = str(req.get("text", "")).strip()
            name = req.get("voice", "dmitri")
            if not text or len(text) > MAX_CHARS:
                return self._json(400, {"error": "text empty or longer than %d" % MAX_CHARS})
            if name not in VOICES:
                return self._json(400, {"error": "unknown voice", "voices": sorted(VOICES)})
            t0 = time.time()
            wav = synth(text, name)
            took = round(time.time() - t0, 2)
            if req.get("play"):
                sink = req.get("target") or DEFAULT_SINK
                r = subprocess.run(["pw-play", "--target", sink, "-"], input=pad_silence(wav),
                                   capture_output=True, timeout=120)
                return self._json(200 if r.returncode == 0 else 502,
                                  {"played": r.returncode == 0, "render_s": took,
                                   "err": r.stderr.decode(errors="replace")[:200]})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("X-Render-Seconds", str(took))
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
        except Exception as e:  # keep the daemon alive
            self._json(500, {"error": repr(e)[:200]})


def main():
    for v in PRELOAD:
        t = time.time()
        get_voice(v)
        print("loaded %s in %.1fs" % (v, time.time() - t), flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("listening 127.0.0.1:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

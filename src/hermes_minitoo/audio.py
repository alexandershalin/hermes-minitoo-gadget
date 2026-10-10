"""PortAudio streams for the MiniToo's Bluetooth sound devices.

Same contract as ``hermes_gadget.linux.audio.Audio`` (the device core calls ``mic_*`` and
``speaker_*`` on it); adapted from that class, MIT licence, see NOTICE.md. On top of it, two
MiniToo-specific behaviours that used to be patched in from outside:

- ``mic_start`` / ``mic_stop`` tell the display when the microphone stream really opens and
  closes (the HFP gate's leading signal).
- With ``minitoo.listen_preroll``, ``mic_start`` only arms the display and returns True, so
  the core switches to Listening and renders it; ``read()`` opens the real stream once the
  display reports the Listening frame uploaded, or its deadline passed. Until then ``read()``
  returns no samples.

No recordings and no silence fallback: if the device is unavailable the error is reported.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from array import array

LOG = logging.getLogger(__name__)


def backend():
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise RuntimeError("install libportaudio2 and the sounddevice package to use audio") from exc
    return sounddevice


def check(config: dict) -> dict:
    sd = backend()
    rate = config.get("rate", 16000)
    result = {"rate": rate}
    for kind in ("input", "output"):
        if kind in config:
            try:
                getattr(sd, f"check_{kind}_settings")(device=config[kind], channels=1, dtype="int16",
                                                      samplerate=rate)
                result[kind] = sd.query_devices(config[kind], kind)["name"]
            except sd.PortAudioError as exc:
                raise RuntimeError(f"audio {kind} is unavailable: {exc}") from exc
    return result


def check_loopback(config: dict) -> None:
    """Explicit local microphone/speaker check; the samples never reach Hermes."""
    if "input" not in config or "output" not in config:
        raise ValueError("audio-check needs both audio.input and audio.output")
    devices = check(config)
    sd = backend()
    rate = devices["rate"]
    print(f"Speak for three seconds into {devices['input']}.", flush=True)
    with sd.RawInputStream(device=config["input"], samplerate=rate, channels=1, dtype="int16") as mic:
        data, overflow = mic.read(rate * 3)
    samples = array("h")
    samples.frombytes(data)
    peak = max((abs(value) for value in samples), default=0)
    db = 20 * math.log10(peak / 32768) if peak else -96
    print(f"Peak: {db:.1f} dBFS. Playing back at half volume on {devices['output']}.", flush=True)
    if overflow:
        raise RuntimeError("input overflow; try another audio device or sample rate")
    pcm = array("h", (int(value / 2) for value in samples)).tobytes()
    with sd.RawOutputStream(device=config["output"], samplerate=rate, channels=1, dtype="int16") as speaker:
        if speaker.write(pcm):
            raise RuntimeError("output underflow; try another audio device or sample rate")
    if peak == 0:
        raise RuntimeError("no microphone signal was captured; check the selected device")


class Audio:
    def __init__(self, config: dict, display=None):
        self.config = config
        self.display = display  # a MiniTooDisplay (or None): gets the microphone/preroll signals
        self.devices = check(config)
        self.mic = None
        self.speaker = None
        self.errors: dict[str, str] = {}
        self.volume = 70
        self.rate = config.get("rate", 16000)
        self.input_buffer = bytearray()
        self.output_buffer = bytearray()
        self.lock = threading.Lock()
        self.ended = True
        self.play_until = 0.0
        self._mic_open = False
        self._deferred_rate: int | None = None

    # ---- microphone --------------------------------------------------------------------

    def mic_start(self, rate: int) -> bool:
        disp = self.display
        if not (disp is not None and disp.listen_preroll):
            return self._open_mic(rate)
        self.mic_stop()  # same as the SDK: close a previous stream first
        self.errors.pop("input", None)
        disp.arm_listening()
        self._deferred_rate = rate
        return True

    def _open_mic(self, rate: int) -> bool:
        self.mic_stop()
        self.errors.pop("input", None)
        sd = backend()

        def capture(data, frames, timing, status):
            with self.lock:
                if status or len(self.input_buffer) + len(data) > rate * 4:
                    self.errors["input"] = str(status) if status else "microphone buffer overflow"
                    raise sd.CallbackAbort
                self.input_buffer.extend(data)

        try:
            self.mic = sd.RawInputStream(device=self.config["input"], samplerate=rate, channels=1,
                                         dtype="int16", callback=capture)
            self.mic.start()
        except (sd.PortAudioError, OSError, ValueError) as exc:
            self.errors["input"] = str(exc)
            self.mic_stop()
            return False
        self._mic_open = True
        if self.display is not None:
            self.display.note_mic(True)
        return True

    def mic_stop(self) -> None:
        disp = self.display
        if self._deferred_rate is not None:
            self._deferred_rate = None
            if disp is not None:
                disp.end_preroll()
        was_open = self._mic_open
        self._mic_open = False
        if self.mic is not None:
            try:
                self.mic.abort()
            finally:
                self.mic.close()
                self.mic = None
        with self.lock:
            self.input_buffer.clear()
        if was_open and disp is not None:
            disp.note_mic(False)

    def read(self) -> bytes:
        rate = self._deferred_rate
        if rate is not None:
            disp = self.display
            if disp is None or disp.preroll_ready():
                self._deferred_rate = None
                if disp is not None:
                    disp.end_preroll()
                # On failure errors["input"] is set and the client cancels listening.
                try:
                    self._open_mic(rate)
                except Exception as exc:  # the SDK's own path (a guarded C callback) would swallow this
                    LOG.exception("MiniToo deferred microphone open failed")
                    self.errors["input"] = str(exc) or type(exc).__name__
        if self.mic is not None and not self.mic.active:
            self.errors.setdefault("input", "microphone stream stopped")
        with self.lock:
            data = bytes(self.input_buffer)
            self.input_buffer.clear()
            return data

    # ---- speaker -----------------------------------------------------------------------

    def speaker_begin(self, rate: int) -> bool:
        self.speaker_abort()
        self.errors.pop("output", None)
        self.rate = rate
        self.ended = False
        sd = backend()

        def playback(data, frames, timing, status):
            with self.lock:
                count = min(len(data), len(self.output_buffer))
                data[:count] = self.output_buffer[:count]
                del self.output_buffer[:count]
                data[count:] = bytes(len(data) - count)
                if count:
                    # Include device latency so busy() covers the final audible samples.
                    latency = max(0, timing.outputBufferDacTime - timing.currentTime)
                    self.play_until = time.monotonic() + latency + count / 2 / rate
                if status:
                    self.errors["output"] = str(status)

        try:
            self.speaker = sd.RawOutputStream(device=self.config["output"], samplerate=rate, channels=1,
                                              dtype="int16", callback=playback)
            self.speaker.start()
            return True
        except (sd.PortAudioError, OSError, ValueError) as exc:
            self.errors["output"] = str(exc)
            self.speaker_abort()
            return False

    def speaker_write(self, pcm: bytes) -> None:
        if self.speaker is None:
            return
        samples = array("h")
        samples.frombytes(pcm)
        scaled = array("h", (int(value * self.volume / 100) for value in samples)).tobytes()
        with self.lock:
            overflow = len(self.output_buffer) + len(scaled) > self.rate * 2 * 30
            if not overflow:
                self.output_buffer.extend(scaled)
        if overflow:
            self.errors["output"] = "speaker buffer exceeded 30 seconds"
            self.speaker_abort()

    def speaker_end(self) -> None:
        self.ended = True

    def speaker_busy(self) -> bool:
        if self.speaker is None:
            return False
        if not self.speaker.active:
            self.errors.setdefault("output", "speaker stream stopped")
            self.speaker_abort()
            return False
        with self.lock:
            busy = not self.ended or bool(self.output_buffer) or time.monotonic() < self.play_until
        if not busy:
            self.speaker_abort()
        return busy

    def speaker_abort(self) -> None:
        if self.speaker is not None:
            try:
                self.speaker.abort()
            finally:
                self.speaker.close()
                self.speaker = None
        with self.lock:
            self.output_buffer.clear()
            self.ended = True
            self.play_until = 0.0

    def speaker_volume(self, percent: int) -> None:
        self.volume = percent

    def close(self) -> None:
        try:
            self.mic_stop()
        finally:
            self.speaker_abort()

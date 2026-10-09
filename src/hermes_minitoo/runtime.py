"""Run Hermes Gadget's Linux client with the MiniToo display backend."""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path

from . import display as minitoo_display
from .config import load_config, sdk_config
from .display import MiniTooDisplay

LOG = logging.getLogger(__name__)

_HOOKED = "_minitoo_hooked"


def install_display_backend() -> None:
    import hermes_gadget.linux.display as upstream_display

    upstream_display.Display = MiniTooDisplay


def install_audio_hooks(display_config: dict, audio_cls=None) -> bool:
    """Opt-in (minitoo.hfp_gate or minitoo.listen_preroll): wrap the SDK's Audio class.

    - mic_start/mic_stop tell the active MiniTooDisplay when the Gadget microphone
      stream really opens and closes (the HFP gate's leading signal).
    - With listen_preroll, mic_start only arms the display and returns True, so the core
      switches to Listening and renders it; read() opens the real stream once the
      display reports the Listening frame uploaded, or its deadline passed. Until then
      read() returns no samples.

    Written against hermes-gadget 0.2.0 (hermes_gadget.linux.audio.Audio). If that class
    or its methods are missing, a warning is logged and nothing is changed. Returns True
    if hooks were installed.
    """
    gate = bool(display_config.get("hfp_gate", False))
    preroll = bool(display_config.get("listen_preroll", False))
    if not (gate or preroll):
        return False
    if audio_cls is None:
        try:
            from hermes_gadget.linux.audio import Audio as audio_cls
        except ImportError as exc:
            LOG.warning("MiniToo audio hooks not installed: %s", exc)
            return False
    names = ("mic_start", "mic_stop", "read")
    missing = [name for name in names if not callable(getattr(audio_cls, name, None))]
    if missing:
        LOG.warning("MiniToo audio hooks not installed: %s.%s has no %s", audio_cls.__module__,
                    audio_cls.__name__, ", ".join(missing))
        return False
    if getattr(audio_cls, _HOOKED, False):
        return True

    orig_start, orig_stop, orig_read = audio_cls.mic_start, audio_cls.mic_stop, audio_cls.read

    def open_mic(audio, rate) -> bool:
        ok = orig_start(audio, rate)
        if ok:
            audio._minitoo_mic_open = True
            disp = minitoo_display.ACTIVE
            if disp is not None:
                disp.note_mic(True)
        return ok

    def mic_start(self, rate):
        disp = minitoo_display.ACTIVE
        if not (preroll and disp is not None and disp.listen_preroll):
            return open_mic(self, rate)
        self.mic_stop()  # same as the SDK: close a previous stream first
        errors = getattr(self, "errors", None)
        if isinstance(errors, dict):
            errors.pop("input", None)
        disp.arm_listening()
        self._minitoo_deferred_rate = rate
        return True

    def mic_stop(self):
        if getattr(self, "_minitoo_deferred_rate", None) is not None:
            self._minitoo_deferred_rate = None
            disp = minitoo_display.ACTIVE
            if disp is not None:
                disp.end_preroll()
        was_open = getattr(self, "_minitoo_mic_open", False)
        self._minitoo_mic_open = False
        orig_stop(self)
        disp = minitoo_display.ACTIVE
        if was_open and disp is not None:
            disp.note_mic(False)

    def read(self):
        rate = getattr(self, "_minitoo_deferred_rate", None)
        if rate is not None:
            disp = minitoo_display.ACTIVE
            if disp is None or disp.preroll_ready():
                self._minitoo_deferred_rate = None
                if disp is not None:
                    disp.end_preroll()
                # On failure the SDK sets errors["input"] and the client cancels listening.
                try:
                    open_mic(self, rate)
                except Exception as exc:
                    # The SDK's own call path (a guarded C callback) would swallow this;
                    # here it must not unwind into Client.step().
                    LOG.exception("MiniToo deferred microphone open failed")
                    errors = getattr(self, "errors", None)
                    if isinstance(errors, dict):
                        errors["input"] = str(exc) or type(exc).__name__
        return orig_read(self)

    audio_cls.mic_start = mic_start
    audio_cls.mic_stop = mic_stop
    if preroll:
        audio_cls.read = read
    setattr(audio_cls, _HOOKED, True)
    LOG.info("MiniToo audio hooks installed (hfp_gate=%s, listen_preroll=%s)", gate, preroll)
    return True


def run(config_path: Path, state_dir: Path) -> None:
    install_display_backend()
    from hermes_gadget.linux.control import run as run_upstream

    config = sdk_config(load_config(config_path))
    install_audio_hooks(config["display"])
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        run_upstream(config, state_dir, stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

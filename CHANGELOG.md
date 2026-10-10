# Changelog

## 0.1.3 — 2026-10-10

- `hermes-gadget minitoo ...` next to `hermes-gadget linux ...` (same commands as `hermes-minitoo`, which stays as an alias). The package installs a `hermes-gadget` entry point that adds `minitoo` to the SDK parser and keeps all SDK commands.
- systemd unit starts the service as `hermes-gadget minitoo run`.

## 0.1.2 — 2026-10-10

- Fix: `RFCOMMTransport.last_failure` started at `0.0`, so on a host with `time.monotonic()` < `reconnect_delay` (first minute after boot) the first `connect()` was refused with "backoff is active". Found by a flaky CI run on Python 3.14.

## 0.1.1 — 2026-10-10

- `research/` removed from `main`; capability references now point to the `development` branch.
- CI matrix reduced to Python 3.13 and 3.14.
- Joystick in recording checked on hardware: left (165) does not stop the recording; right and up send only `AT+CHUP`, indistinguishable from Play. `joystick_guard` is therefore useless and off by default (see `docs/KNOWN_ISSUES.md`).

## 0.1.0 — 2026-10-10

First stable cut: everything verified on a real MiniToo.

- Native 160×128 lossless LZO1X display path over RFCOMM, ready-ACK handling, reconnect with backoff, latest-frame-wins queue.
- Hermes Gadget SDK v0.2.0 (`323e330`) as the core; MiniToo as A2DP/HFP speaker and microphone.
- `minitoo-talk-key`: Play/Pause talk toggle, adaptive VAD (`vad_ceiling`), `hfp_keys` (stop recording via `AT+CHUP`), joystick: left = cancel, double left = new session, right = last reply.
- `listen_preroll` and `hfp_gate`: `Listening` / `Thinking` screens survive the A2DP↔HFP profile switch.
- `minitoo-autoaddr` with `SAFE_SCAN`, USB autosuspend fix, WirePlumber fragments, diagnostic scripts.
- Bilingual voice (ru/en): STT `auto`, language-based TTS voice, language hint for the model.
- `install.sh`, `scripts/doctor.sh`, `hermes-side/` (STT client, Piper TTS daemon), `Dockerfile.test`.

Not hardware-verified: installers on a clean foreign host. See `docs/KNOWN_ISSUES.md`.

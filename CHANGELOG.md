# Changelog

## 0.1.0 — 2026-10-10

First stable cut: everything verified on a real MiniToo.

- Native 160×128 lossless LZO1X display path over RFCOMM, ready-ACK handling, reconnect with backoff, latest-frame-wins queue.
- Hermes Gadget SDK v0.2.0 (`323e330`) as the core; MiniToo as A2DP/HFP speaker and microphone.
- `minitoo-talk-key`: Play/Pause talk toggle, adaptive VAD (`vad_ceiling`), `hfp_keys` (stop recording via `AT+CHUP`), joystick: left = cancel, double left = new session, right = last reply.
- `listen_preroll` and `hfp_gate`: `Listening` / `Thinking` screens survive the A2DP↔HFP profile switch.
- `minitoo-autoaddr` with `SAFE_SCAN`, USB autosuspend fix, WirePlumber fragments, diagnostic scripts.
- Bilingual voice (ru/en): STT `auto`, language-based TTS voice, language hint for the model.
- `install.sh`, `scripts/doctor.sh`, `hermes-side/` (STT client, Piper TTS daemon), `Dockerfile.test`.

Not hardware-verified: `joystick_guard`, installers on a clean foreign host. See `docs/KNOWN_ISSUES.md`.

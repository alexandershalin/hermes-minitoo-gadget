# Architecture

## Principle

Hermes Gadget SDK remains the device runtime. This repository contains only MiniToo-specific adaptation.

MiniToo knowledge is split into:

- runtime code under `src/hermes_minitoo/`;
- reverse-engineering evidence, kept on the `development` branch.

## Structure

`hermes-gadget-minitoo` is a client for the Hermes Gadget SDK, built like the SDK's `hermes-gadget linux`:

| Module | Role |
|---|---|
| `cli.py` | `hermes-gadget-minitoo run/status/send/button/...` |
| `control.py` | private Unix socket and the service loop |
| `client.py` | device identity (`device.json`) and the single-threaded host of the SDK's device core |
| `audio.py` | PortAudio streams on the MiniToo's Bluetooth devices, microphone/preroll signals for the display |
| `display.py` | the core's 160×128 framebuffer, uploaded over RFCOMM |
| `platforms/linux/` | BlueZ RFCOMM transport, HCI link table, state-directory lock |

From the SDK it uses only the device core (`hermes_gadget.sim.native`), the WebSocket transport (`hermes_gadget.sim.transport`) and the pairing protocol inside the core. Nothing from `hermes_gadget.linux` is imported. This is a client, not a Hermes fork.

## Native display pipeline

1. Hermes Gadget native C++ renderer creates a **160×128 RGB565** framebuffer.
2. `MiniTooDisplay.present()` copies it and converts it to exact RGB888.
3. A worker invokes system `liblzo2` via `ctypes`.
4. The frame is compressed as a complete LZO1X stream.
5. The same stream is immediately decompressed and compared against all 61,440 source bytes.
6. The verified stream is wrapped as:
   `23 01 <delay_be16> 08 0A 00 <lzo_len_be32> <lzo>`.
7. Transport sends a `0x8B` size announcement and waits for the exact ready response.
8. Payload is sent as ordered 256-byte `0x8B` chunks on the same RFCOMM connection.

The display worker uses **latest-frame-wins** semantics: if Hermes redraws faster than Bluetooth can upload, stale intermediate frames are discarded instead of queued.

## Why not the original 128×128 Zstandard path?

It was useful as an early, already-understood compatibility path, but newer physical-device testing demonstrates that MiniToo supports its actual **160×128** LCD through the `0x23` lossless animation format.

The native path:

- uses the whole panel;
- avoids rescaling/cropping a 128×128 canvas;
- avoids JPEG artifacts;
- provides a stricter handshake;
- performs local lossless verification before transmit.

The old Zstandard format stays documented only as reverse-engineering history.

## Audio pipeline

Hermes Gadget's existing Linux PortAudio backend targets MiniToo's normal Bluetooth audio sink through the host audio stack (typically PipeWire/WirePlumber). No proprietary Divoom audio protocol is needed for Hermes speech.

## Safety profile

Defaults follow the conservative hardware-tested live-stream profile:

- 2,500 ms update interval;
- 256-byte chunks;
- 5 ms inter-chunk delay;
- 600,000-byte payload ceiling;
- one persistent RFCOMM client;
- complete upload serialization.

Faster refresh should be based on measurements from the actual Linux host, not on socket writability alone.

## Future action layer

Likely Hermes Gadget actions:

- brightness;
- screen on/off;
- custom-face state switch;
- notification;
- selected safe native views/tools.

Firmware work remains completely separate from normal gadget startup.

## Firmware boundary

No firmware binary, patch, OTA writer, DFU/JTAG routine or destructive probe belongs in the default runtime. Firmware research starts read-only: metadata, hashes, offline parsing and hardware identification.

## Next milestones

1. Physical Linux/MiniToo native-frame smoke test.
2. Verify RFCOMM + A2DP coexistence.
3. Measure upload latency and tune update scheduling.
4. Add brightness and screen-power actions.
5. Add custom-face discovery/switching.
6. Add systemd install helper after hardware validation.
7. If the shim proves stable, propose a pluggable Linux display backend API upstream to Hermes Gadget.

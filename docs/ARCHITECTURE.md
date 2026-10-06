# Architecture

## Principle

Hermes Gadget SDK remains the device runtime. This repository should contain only MiniToo-specific adaptation.

MiniToo knowledge is split into two layers:

- runtime code under `src/hermes_minitoo/`;
- preserved reverse-engineering evidence under `research/`.

The capability registry in `src/hermes_minitoo/capabilities.py` is the seam between them. A research finding can be visible to developers without pretending it is supported.

## Current integration seam

Hermes Gadget `v0.2.0` constructs `hermes_gadget.linux.display.Display` lazily from the Linux `Client`. At startup, `hermes-minitoo` replaces that class with `MiniTooDisplay`, then runs the unmodified upstream Linux control/service loop.

This is intentionally a compatibility shim, not a fork.

## Current display pipeline

1. Hermes Gadget native C++ core renders its normal UI into RGB565.
2. `MiniTooDisplay.present()` copies the current framebuffer.
3. RGB565 LE is converted to RGB888.
4. Only the latest pending frame is retained.
5. A worker thread Zstd-compresses and sends the image through MiniToo's RFCOMM application channel.

The background worker prevents Bluetooth transfer latency from blocking WebSocket traffic, audio handling, controls, or the Hermes Gadget core clock.

## Planned display pipeline

Research now points to a better native-resolution route:

1. render Hermes Gadget at 160×128;
2. convert to exact 61,440-byte RGB888;
3. MiniLZO1X compress and verify round trip;
4. wrap in the MiniToo `0x23` animation payload with cell geometry `08 0A`;
5. announce over `0x8B`;
6. wait for ready ACK;
7. send ordered 256-byte chunks on one persistent RFCOMM connection.

The old and new paths should remain separate codecs until the new path is physically verified on Linux.

## Audio pipeline

No proprietary MiniToo audio protocol is required for Hermes speech. Hermes Gadget's existing Linux PortAudio backend is configured with MiniToo's system Bluetooth audio sink. On a modern Linux host that will normally be provided by PipeWire/WirePlumber or PulseAudio compatibility.

Proprietary volume/playback commands may later be exposed as optional device actions, but should not replace standard OS audio controls without a reason.

## Future action layer

Likely Hermes Gadget actions, in order:

- brightness;
- screen on/off;
- custom-face state switch;
- notification;
- selected safe native views/tools.

Firmware work remains completely separate from normal gadget startup.

## Firmware boundary

No firmware binary, patch, OTA writer, DFU/JTAG routine or destructive probe belongs in the default runtime.

If firmware research becomes part of this repository, it should start as:

- metadata acquisition;
- hash verification;
- offline parsing;
- read-only hardware identification.

Any write path should be an explicit research tool with separate warnings and recovery documentation.

## Next milestones

1. Physical Linux/MiniToo smoke test: RFCOMM image only.
2. Verify persistent RFCOMM reconnect behavior.
3. Verify RFCOMM and A2DP remain active simultaneously on BlueZ.
4. Migrate to the native 160×128 lossless live path.
5. Add brightness and screen-power actions.
6. Add custom-face discovery/switching.
7. Add systemd install helper after physical verification.
8. If the shim proves stable, propose a generic pluggable Linux display backend API to Hermes Gadget upstream.

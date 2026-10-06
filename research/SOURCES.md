# Sources and projects

Snapshot: **2026-10-06**.

These are references, not vendored dependencies.

## Hermes core

### Hermes Gadget SDK

<https://github.com/Adolanium/hermes-gadget-sdk>

This project's runtime core. Current dependency is pinned to `v0.2.0`.

## MiniToo Bluetooth / display reverse engineering

### bugzmanov/divoom-minitoo

<https://github.com/bugzmanov/divoom-minitoo>

Broad protocol map from Android decompilation + real-device probing. Key material:

- `FINDINGS.md`;
- persistent macOS RFCOMM daemon;
- custom face / ClockId work;
- tools, games, brightness, screen control;
- live animation, photo and notification paths;
- Clauddy (Claude Code status display).

Observed firmware in its notes: 2.4.0.

### alvinunreal/divoom-minitoo-osx

<https://github.com/alvinunreal/divoom-minitoo-osx>

Focused image/GIF/video transport work. Key material:

- `PROTOCOL.md`;
- `0x8B` 128×128 RGB888/Zstandard path;
- 256-byte chunking;
- persistent RFCOMM daemon;
- custom ClockId selection examples.

### alphafornow MiniToo SPP reference

<https://gist.github.com/alphafornow/8d38848adf9be12d0f9dc7700dff5e21>

Independent Python-oriented SPP/image notes.

### sirnugget11/divoom-minitoo-dotnet

<https://github.com/sirnugget11/divoom-minitoo-dotnet>

Windows .NET library with real-device tests. Especially important:

- native 160×128 live frame path;
- `0x23` payload;
- MiniLZO/RGB888 encoding;
- ready-ACK-aware persistent `0x8B` transfer;
- brightness/screen/storage API;
- documented safety limits and failure signatures.

### lewilou22/divoom-minitoo-tools

<https://github.com/lewilou22/divoom-minitoo-tools>

Windows experiments reporting higher-rate display driving and simultaneous Bluetooth audio/video use. Useful evidence for future performance work; details should be reproduced before being used as design assumptions.

### ruvnet/minitoo-control

<https://github.com/ruvnet/minitoo-control>

Persistent transport + animated status screens, including measured real-device update timings and defensive protocol validation.

### jsniel/home-assistant-minitoo

<https://github.com/jsniel/home-assistant-minitoo>

Home Assistant integration. Important as evidence that MiniToo-specific display support can sit on top of a more generic Divoom transport and ESP32 proxy stack.

### Codex/status-display projects

- <https://github.com/AFrayde01/divoom-minitoo-codex>
- <https://github.com/giperfast/codex-minitoo>

Useful real applications of the MiniToo as an AI-agent status/telemetry terminal. They also contain newer native-resolution display experiments.

## Firmware reverse engineering

### antiali.as/minitoo-forth (Tangled)

<https://tangled.org/antiali.as/minitoo-forth/tree/public>

Primary firmware-research reference.

Important documents:

- README: <https://tangled.org/antiali.as/minitoo-forth/blob/public/README.md>
- flash delivery: <https://tangled.org/antiali.as/minitoo-forth/blob/public/docs/flash-delivery.md>
- evidence docs index: <https://tangled.org/antiali.as/minitoo-forth/tree/public/docs>

Current public snapshot describes a FORTH code-cave kernel that is simulation-proven but not yet practically flashed onto physical MiniToo hardware.

## Hardware / regulatory

### FCC ID A8I-MINITOO

<https://fccid.io/A8I-MINITOO>

Internal photos:

<https://fccid.io/A8I-MINITOO/Internal-Photos/Internal-Photos-8647766>

Useful for board layout/chip/pad investigation without immediately opening the user's unit.

## Divoom firmware ecosystem reference

### REvoom

<https://divoom.2a03.party/>

Community reverse-engineering/index work for several Divoom devices and firmware versions. Useful for firmware naming/CDN conventions; do not assume every MiniToo build is indexed.

## Source-handling rule

When implementing a feature from one of these projects:

1. record the exact upstream commit;
2. preserve its license/attribution requirements;
3. independently test packet construction;
4. do not copy vendor firmware, APKs, decompiled vendor source, proprietary libraries or datasheets into this repository.

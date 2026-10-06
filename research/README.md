# MiniToo research archive

Snapshot date: **2026-10-06**.

This directory is deliberately separate from the production adapter. Its job is to preserve public reverse-engineering knowledge so the project can grow without treating every community claim as a supported feature.

## Status vocabulary

- **implemented** — code exists in this repository.
- **verified upstream** — another project reports real-device verification and provides reproducible code/notes.
- **reported** — credible reverse-engineering evidence exists, but this project has not reproduced it.
- **research-only** — useful lead, not a deployable feature.
- **blocked** — design exists, but a known missing step prevents practical use.

## Documents

- [HACKS.md](HACKS.md) — protocol, display, device controls, custom faces, tools, games, notifications, integrations and oddities.
- [FIRMWARE.md](FIRMWARE.md) — firmware acquisition metadata, OTA security model, code-cave/FORTH work and candidate flashing/debug routes.
- [SOURCES.md](SOURCES.md) — primary repositories, FCC material and cross-project references.
- [firmware/MANIFEST.md](firmware/MANIFEST.md) — intentionally binary-free placeholder for future local firmware research.

## Project rule

A finding moves from this directory into normal runtime code only after:

1. the exact source/revision is recorded;
2. the expected packet/format is covered by an offline test;
3. dangerous or stateful behavior is documented;
4. the feature is verified on a real MiniToo, preferably on the Linux target.

The `hermes-minitoo capabilities` command mirrors this separation in machine-readable form.

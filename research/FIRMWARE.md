# MiniToo firmware / OTA / custom-code research

Snapshot: **2026-10-06**.

This document preserves public research. **This repository does not ship firmware binaries and currently contains no flashing implementation.**

Primary source: `antiali.as/minitoo-forth` on Tangled.

## Reported firmware target

The minitoo-forth project identifies:

- application firmware: `flag41007.bin`;
- version meaning: product flag 41 / build 007;
- size: **1,183,237 bytes** (`0x120e05`);
- SHA-1: **c950735f817bd8a22b0d4d616f04e9dd058d93d5**;
- target: Divoom MiniToo;
- reported main application SoC: **Actions ATS2831**;
- reported auxiliary/USB-side component: **JieLi AC690N**;
- display: **160×128 IPS**.

The ATS2831 full datasheet is reported as non-public/NDA. The research uses sibling-family documentation and firmware analysis to infer some hardware details.

## Firmware acquisition

The research project intentionally does not redistribute the vendor firmware. It documents reacquisition through the Divoom app OTA flow:

- API family: `GetUpdateFileV3`;
- response includes a `FileId`;
- firmware is then fetched from Divoom's CDN (`f.divoom-gz.com`).

Older Divoom firmware indexes such as REvoom are useful for mapping version/CDN conventions but may not contain this exact build.

Our placeholder manifest is in [firmware/MANIFEST.md](firmware/MANIFEST.md). Do not commit a downloaded vendor binary to this repository.

## OTA integrity model

The most important reported finding is that the MiniToo application OTA image uses an **additive checksum rather than a cryptographic signature**.

That means a modified image can be made structurally valid in principle. It does **not** mean a stock device currently accepts arbitrary modified OTA images: delivery/boot state creates additional constraints.

## The FORTH code-cave project

`minitoo-forth` builds a small self-hosting FORTH kernel intended to live in a roughly **6.5 KiB firmware code cave**.

Reported design:

- wake on magic Bluetooth command `0x0407`;
- execute one FORTH line per packet;
- return output over SPP;
- coexist with normal firmware;
- end-to-end path works in simulation (for example `3 4 + .` → `7`).

**Critical status:** custom code has **not** been demonstrated running on a physical MiniToo through a practical software flashing route. Treat the kernel as a proven firmware patch/simulation experiment, not an installable mod.

## Candidate delivery/debug routes

The minitoo-forth evidence map currently classifies routes roughly as follows.

### Bluetooth SPP OTA

Status: **blocked/closed in current research**.

Although the image itself is checksum-only, the normal SPP OTA flow has a bootloader-seeded/state dependency described by the project as a catch-22. In other words, “unsigned” does not equal “easy arbitrary OTA”.

### SD-card application update

Status: **closed/retracted**.

A path initially suspected to load firmware from SD was later determined not to provide the desired application-image flashing route.

### USB DFU / button combination

Status: **open question**.

Research notes describe a topology contradiction: it is not yet clear whether a host-visible DFU path actually reaches the ATS2831 or terminates at/through the JieLi-side component.

### Boot straps / exposed GPIO

Status: **hardware research; requires opening the device**.

The research maps candidate boot strap pins. No practical procedure is part of this project.

### JTAG/debug

Status: **hardware research; requires opening the device and adapter**.

Public notes map candidate debug pins, but also contain mixed terminology about the exact CPU/debug core. We should independently identify chip markings and pads before relying on those labels.

### JieLi AC690N as a bridge

Status: **unknown and interesting**.

Because USB-side behavior may involve the JieLi part, understanding whether it can proxy access to the application processor could resolve the USB/DFU uncertainty.

### No-flash code execution

Status: **research-only**.

The minitoo-forth project reports executable RAM/no MPU and catalogs decoder/crash surfaces as possible arbitrary-code-execution directions. This is not a reliable exploit or product feature.

## Safe project stance

For now:

- no automatic firmware download;
- no firmware binary in git;
- no blind OTA writes;
- no “flash” CLI;
- no destructive hardware instructions in normal setup docs.

Future firmware tooling, if added, should start with **read-only acquisition, hashing and analysis**, then require explicit opt-in for anything that writes the device.

## Questions worth preserving

- Is the reported ATS2831 core/debug architecture independently confirmed from silicon/pads?
- What exactly does the JieLi AC690N control?
- Can a stock MiniToo expose DFU through a reproducible button combination?
- Can HCI capture of an official app update resolve the bootloader-state question?
- Is there a non-destructive RAM execution primitive?
- Which hardware revision matches FCC photos versus the firmware research unit?
- Can a patched image be restored safely if a flash experiment fails?

Until those are answered, Hermes integration should remain entirely on the stock firmware and RFCOMM application protocol.

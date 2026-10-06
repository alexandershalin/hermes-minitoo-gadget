# Firmware asset manifest (placeholder)

**Do not commit vendor firmware binaries to this repository.**

This file records metadata needed to reacquire and verify a research copy outside git.

## Reported MiniToo target

| Field | Value |
|---|---|
| File | `flag41007.bin` |
| Product/build interpretation | MiniToo product flag 41, build 007 |
| Size | `1,183,237` bytes (`0x120e05`) |
| SHA-1 | `c950735f817bd8a22b0d4d616f04e9dd058d93d5` |
| Primary research source | `antiali.as/minitoo-forth` |
| Git policy | never commit binary |

## Acquisition placeholder

The public firmware-research project documents reacquisition via the Divoom app's OTA API (`GetUpdateFileV3`) and Divoom CDN FileId.

This repository intentionally does not automate that flow yet.

Future read-only helper, if added:

```text
hermes-minitoo firmware acquire-metadata
hermes-minitoo firmware verify /path/to/flag41007.bin
```

Those commands are **design placeholders only**. They do not exist yet.

## Local research layout

If firmware analysis is eventually performed, keep proprietary/vendor assets outside the repository, for example:

```text
~/.local/share/hermes-minitoo-research/
  firmware/
    flag41007.bin
  apk/
  datasheets/
```

Only hashes, offsets, derived facts and original analysis should be committed here.

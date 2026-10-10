"""Known MiniToo capabilities and research placeholders.

References point at the research notes kept on the ``development`` branch."""

from __future__ import annotations

from dataclasses import asdict, dataclass

RESEARCH_URL = "https://github.com/alexandershalin/hermes-minitoo-gadget/blob/development/"


@dataclass(frozen=True)
class Capability:
    status: str
    summary: str
    reference: str


CAPABILITIES: dict[str, Capability] = {
    "display.native_lzo_160x128": Capability(
        "implemented",
        "native 160x128 lossless RGB888/MiniLZO live path over 0x8B",
        RESEARCH_URL + "research/HACKS.md#32-native-160128-lossless-live-frames",
    ),
    "display.compat_zstd_128": Capability(
        "legacy",
        "early 128x128 RGB888/Zstandard path retained as research, no longer the runtime backend",
        RESEARCH_URL + "research/HACKS.md#31-legacy-path-128128-rgb888--zstandard",
    ),
    "display.native_zstd_160x128": Capability(
        "research",
        "newer reports of native-resolution RGB/Zstd; format needs reconciliation",
        RESEARCH_URL + "research/HACKS.md#33-native-160128-rgbzstd-reports",
    ),
    "device.brightness": Capability(
        "planned",
        "JSON Channel/SetBrightness and binary opcode 0x32",
        RESEARCH_URL + "research/HACKS.md#5-brightness-screen-and-audio-control-commands",
    ),
    "device.screen_power": Capability(
        "planned",
        "screen on/off while preserving current view",
        RESEARCH_URL + "research/HACKS.md#5-brightness-screen-and-audio-control-commands",
    ),
    "device.volume": Capability(
        "research",
        "proprietary volume command exists; OS A2DP may remain preferred",
        RESEARCH_URL + "research/HACKS.md#5-brightness-screen-and-audio-control-commands",
    ),
    "device.tools": Capability(
        "research",
        "native stopwatch, scoreboard, noise meter and countdown views",
        RESEARCH_URL + "research/HACKS.md#6-built-in-tools",
    ),
    "device.games": Capability(
        "research",
        "built-in game/Tetris modes via opcode 0xA0",
        RESEARCH_URL + "research/HACKS.md#7-built-in-views-and-games",
    ),
    "custom_face.switch": Capability(
        "planned",
        "instant switch between already-installed faces by real ClockId",
        RESEARCH_URL + "research/HACKS.md#4-persistent-custom-faces-and-fast-state-switching",
    ),
    "custom_face.install": Capability(
        "research",
        "persistent custom-face installation/file workflow",
        RESEARCH_URL + "research/HACKS.md#4-persistent-custom-faces-and-fast-state-switching",
    ),
    "photo.album": Capability(
        "research",
        "create/manage local photo albums and local image files",
        RESEARCH_URL + "research/HACKS.md#8-photo-albums",
    ),
    "notification.text_icon": Capability(
        "planned",
        "short ANCS-style text notification with built-in icon",
        RESEARCH_URL + "research/HACKS.md#9-notifications",
    ),
    "firmware.metadata": Capability(
        "research",
        "read-only firmware acquisition/hash metadata; no binary redistribution",
        RESEARCH_URL + "research/FIRMWARE.md",
    ),
    "firmware.custom_code": Capability(
        "blocked",
        "FORTH code-cave work is simulation-proven but lacks practical physical flashing",
        RESEARCH_URL + "research/FIRMWARE.md#the-forth-code-cave-project",
    ),
    "firmware.dfu_jtag": Capability(
        "research",
        "hardware/DFU/JTAG paths remain unresolved and require independent verification",
        RESEARCH_URL + "research/FIRMWARE.md#candidate-deliverydebug-routes",
    ),
}


def capabilities_dict() -> dict[str, dict[str, str]]:
    return {name: asdict(cap) for name, cap in CAPABILITIES.items()}

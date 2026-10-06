import json

from hermes_minitoo.config import load_config, sdk_config


def test_config_translates_minitoo_to_sdk_display(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "server": "ws://127.0.0.1:8765/gadget",
        "name": "Desk MiniToo",
        "minitoo": {
            "address": "AA:BB:CC:DD:EE:FF",
            "channel": 1,
            "update_interval_ms": 2500,
            "chunk_delay_ms": 5,
        },
        "audio": {"output": "Divoom MiniToo", "rate": 48000},
    }))
    cfg = sdk_config(load_config(path))
    assert cfg["display"]["address"] == "AA:BB:CC:DD:EE:FF"
    assert cfg["display"]["update_interval_ms"] == 2500
    assert cfg["audio"]["rate"] == 48000

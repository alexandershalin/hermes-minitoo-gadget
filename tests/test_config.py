import json

import pytest

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


def _write(tmp_path, **overrides):
    import json

    cfg = {"server": "ws://127.0.0.1:1/g", "minitoo": {"address": "AA:BB:CC:DD:EE:FF"}}
    cfg.update(overrides)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg))
    return path


def test_address_must_be_a_mac(tmp_path):
    import pytest

    from hermes_minitoo.config import load_config

    with pytest.raises(ValueError, match="MAC"):
        load_config(_write(tmp_path, minitoo={"address": "x:y"}))


@pytest.mark.parametrize("extra", [
    {"token": 123}, {"token": ""}, {"name": 5}, {"audio": {"rate": "48000"}},
    {"audio": {"output": 1}}, {"audio": {"rate": 5}},
])
def test_rejects_bad_types(tmp_path, extra):
    from hermes_minitoo.config import load_config

    with pytest.raises(ValueError):
        load_config(_write(tmp_path, **extra))


def test_warns_on_open_token_file_and_plain_ws(tmp_path, caplog):
    from hermes_minitoo.config import load_config

    path = _write(tmp_path, token="secret", server="ws://example.com/g")
    path.chmod(0o644)
    load_config(path)
    text = caplog.text
    assert "unencrypted" in text and "chmod 600" in text

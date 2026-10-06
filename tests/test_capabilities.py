from hermes_minitoo.capabilities import CAPABILITIES, capabilities_dict


def test_capability_registry_marks_native_path_implemented():
    assert CAPABILITIES["display.native_lzo_160x128"].status == "implemented"
    assert CAPABILITIES["display.compat_zstd_128"].status == "legacy"
    assert CAPABILITIES["firmware.custom_code"].status == "blocked"


def test_capabilities_are_json_friendly():
    data = capabilities_dict()
    assert data["device.brightness"]["reference"].startswith("research/")

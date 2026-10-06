from hermes_minitoo.capabilities import CAPABILITIES, capabilities_dict


def test_capability_registry_separates_current_and_future_features():
    assert CAPABILITIES["display.compat_zstd_128"].status == "implemented"
    assert CAPABILITIES["display.native_lzo_160x128"].status == "planned"
    assert CAPABILITIES["firmware.custom_code"].status == "blocked"


def test_capabilities_are_json_friendly():
    data = capabilities_dict()
    assert data["device.brightness"]["reference"].startswith("research/")

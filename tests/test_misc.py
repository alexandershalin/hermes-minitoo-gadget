import re
from pathlib import Path

import hermes_minitoo
from hermes_minitoo.capabilities import CAPABILITIES
from hermes_minitoo.cli import parser

ROOT = Path(__file__).resolve().parent.parent


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", text)


def test_capability_references_point_to_existing_anchors():
    anchors: dict[str, set[str]] = {}
    for cap in CAPABILITIES.values():
        path, _, anchor = cap.reference.partition("#")
        file = ROOT / path
        assert file.is_file(), cap.reference
        if anchor:
            if path not in anchors:
                anchors[path] = {
                    _slug(m.group(1))
                    for m in re.finditer(r"^#+\s+(.*)$", file.read_text(encoding="utf-8"), re.M)
                }
            assert anchor in anchors[path], cap.reference


def test_common_options_work_before_and_after_subcommand():
    p = parser()
    assert p.parse_args(["--verbose", "status"]).verbose is True
    assert p.parse_args(["status", "--verbose"]).verbose is True
    assert p.parse_args(["--state-dir", "/a", "status"]).state_dir == Path("/a")
    assert p.parse_args(["status", "--state-dir", "/b"]).state_dir == Path("/b")
    assert p.parse_args(["status"]).verbose is False


def test_version_is_a_string():
    assert isinstance(hermes_minitoo.__version__, str)

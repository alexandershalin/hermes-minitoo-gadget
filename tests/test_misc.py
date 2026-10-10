from pathlib import Path

import hermes_minitoo
from hermes_minitoo.capabilities import CAPABILITIES, RESEARCH_URL
from hermes_minitoo.cli import parser


def test_capability_references_point_to_development_branch():
    for cap in CAPABILITIES.values():
        assert cap.reference.startswith(RESEARCH_URL), cap.reference
        assert cap.reference.split("#")[0].endswith(".md"), cap.reference


def test_common_options_work_before_and_after_subcommand():
    p = parser()
    assert p.parse_args(["--verbose", "status"]).verbose is True
    assert p.parse_args(["status", "--verbose"]).verbose is True
    assert p.parse_args(["--state-dir", "/a", "status"]).state_dir == Path("/a")
    assert p.parse_args(["status", "--state-dir", "/b"]).state_dir == Path("/b")
    assert p.parse_args(["status"]).verbose is False


def test_version_is_a_string():
    assert isinstance(hermes_minitoo.__version__, str)
